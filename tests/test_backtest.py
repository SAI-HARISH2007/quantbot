from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from quantbot.backtest.engine import BacktestData, BacktestEngine
from quantbot.backtest.trades import TradeLog
from quantbot.config import AppConfig
from quantbot.core.types import Fill, MarketInfo, Side
from quantbot.strategies.mean_reversion import MeanReversion

NOW = datetime(2026, 6, 1, tzinfo=timezone.utc)


def _market(cid="c1") -> MarketInfo:
    return MarketInfo(
        condition_id=cid, question=f"Test market {cid}?", slug=cid,
        yes_token_id=f"{cid}_yes", no_token_id=f"{cid}_no",
        end_date=NOW + timedelta(days=60),
    )


def _prices(n=300, seed=11, noise=0.01, spikes=True) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    p = 0.5 + np.cumsum(rng.normal(0, noise, n))
    if spikes:  # inject mean-reverting spikes the strategy should harvest
        for i in range(50, n - 10, 40):
            p[i] += 0.12
            p[i + 1 : i + 6] -= 0.02  # decay back
    p = np.clip(p, 0.05, 0.95)
    return pd.DataFrame(
        {"ts": [NOW + timedelta(minutes=10 * i) for i in range(n)], "price": p}
    )


def _data() -> BacktestData:
    m = _market()
    return BacktestData(markets={m.condition_id: m}, prices={m.condition_id: _prices()})


def test_engine_runs_and_produces_equity():
    cfg = AppConfig()
    engine = BacktestEngine(cfg, [MeanReversion(entry_z=2.0)])
    res = engine.run(_data())
    assert len(res.equity) > 5
    assert res.final_equity > 0
    # equity starts at initial capital
    assert abs(res.equity.iloc[0] - cfg.risk.initial_capital) < cfg.risk.initial_capital * 0.05


def test_engine_no_signals_means_flat_equity():
    cfg = AppConfig()

    class Silent(MeanReversion):
        def on_view(self, view):
            return []

    res = BacktestEngine(cfg, [Silent()]).run(_data())
    assert abs(res.final_equity - cfg.risk.initial_capital) < 1e-6
    assert len(res.trades) == 0


def test_engine_costs_hurt():
    data = _data()
    cheap = AppConfig()
    exp = AppConfig()
    exp.costs.taker_fee_bps = 200.0
    exp.costs.extra_slippage = 0.01
    r_cheap = BacktestEngine(cheap, [MeanReversion(entry_z=2.0)], assumed_spread=0.01).run(data)
    r_exp = BacktestEngine(exp, [MeanReversion(entry_z=2.0)], assumed_spread=0.06).run(data)
    if len(r_cheap.trades) and len(r_exp.trades):
        assert r_exp.final_equity <= r_cheap.final_equity + 1e-6


def test_engine_settlement_yes():
    """Market drifting to ~0.95 must settle YES holders at 1.0."""
    m = _market("cwin")
    n = 200
    p = np.linspace(0.55, 0.97, n)
    df = pd.DataFrame(
        {"ts": [NOW + timedelta(minutes=10 * i) for i in range(n)], "price": p}
    )
    cfg = AppConfig()

    class BuyOnce(MeanReversion):
        def __init__(self):
            super().__init__()
            self.done = False

        def on_view(self, view):
            from quantbot.core.types import Signal
            if self.done or view.price is None or view.price > 0.6:
                return []
            self.done = True
            return [Signal(
                strategy="buyonce", token_id=view.market.yes_token_id,
                condition_id=view.market.condition_id, side=Side.BUY,
                fair_value=0.9, market_price=view.price, edge=0.3,
                confidence=1.0, ts=view.now, ttl_seconds=3600,
            )]

    res = BacktestEngine(cfg, [BuyOnce()]).run(
        BacktestData(markets={m.condition_id: m}, prices={m.condition_id: df})
    )
    assert len(res.trades) == 1
    trade = res.trades.iloc[0]
    assert trade["exit_price"] == 1.0  # settled YES
    assert trade["pnl"] > 0
    assert res.final_equity > cfg.risk.initial_capital


def test_trade_log_fifo():
    log = TradeLog()
    t0 = NOW
    log.on_fill(Fill(order_id="a", token_id="t", condition_id="c", side=Side.BUY,
                     price=0.4, size=100, ts=t0))
    log.on_fill(Fill(order_id="b", token_id="t", condition_id="c", side=Side.BUY,
                     price=0.5, size=100, ts=t0 + timedelta(hours=1)))
    log.on_fill(Fill(order_id="c", token_id="t", condition_id="c", side=Side.SELL,
                     price=0.6, size=150, ts=t0 + timedelta(hours=2)))
    df = log.to_frame()
    assert len(df) == 2  # 100 from lot1 + 50 from lot2
    assert abs(df.iloc[0]["pnl"] - (0.6 - 0.4) * 100) < 1e-9
    assert abs(df.iloc[1]["pnl"] - (0.6 - 0.5) * 50) < 1e-9
    log.on_settlement("t", payout=0.0, ts=t0 + timedelta(hours=3))
    df2 = log.to_frame()
    assert len(df2) == 3
    assert abs(df2.iloc[2]["pnl"] - (0.0 - 0.5) * 50) < 1e-9
