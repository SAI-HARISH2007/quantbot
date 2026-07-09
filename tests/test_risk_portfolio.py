from datetime import datetime, timezone

from quantbot.config import RiskConfig
from quantbot.core.types import Fill, Order, OrderType, Side, Signal
from quantbot.portfolio.portfolio import Portfolio
from quantbot.risk.limits import RiskManager, RiskState
from quantbot.risk.sizing import as_complement_buy, kelly_fraction_binary, size_order

NOW = datetime(2026, 7, 1, tzinfo=timezone.utc)


def _fill(side: Side, price: float, size: float, token="tok_yes") -> Fill:
    return Fill(
        order_id="o1", token_id=token, condition_id="c1",
        side=side, price=price, size=size, ts=NOW,
    )


# ---------------- Kelly ----------------
def test_kelly_zero_when_no_edge():
    assert kelly_fraction_binary(q=0.5, price=0.5, side=Side.BUY) == 0.0
    assert kelly_fraction_binary(q=0.5, price=0.5, side=Side.SELL) == 0.0


def test_kelly_positive_with_edge_and_symmetric():
    buy = kelly_fraction_binary(q=0.6, price=0.5, side=Side.BUY)
    sell = kelly_fraction_binary(q=0.4, price=0.5, side=Side.SELL)
    assert buy > 0 and abs(buy - sell) < 1e-12
    # f* = (q - p) / (1 - p) = 0.1 / 0.5 = 0.2
    assert abs(buy - 0.2) < 1e-9


def test_size_order_caps_and_min_notional():
    cfg = RiskConfig(initial_capital=10_000)
    sig = Signal(
        strategy="s", token_id="t", condition_id="c", side=Side.BUY,
        fair_value=0.9, market_price=0.5, edge=0.4, confidence=1.0,
    )
    shares = size_order(sig, equity=10_000, price=0.5, cfg=cfg)
    # hard cap: max_kelly_stake_pct(5%) * 10k = $500 notional -> but per-market
    # cap is also 500 -> 1000 shares at 0.5
    assert 0 < shares <= 1000 + 1e-9
    # tiny equity -> below min notional -> zero
    assert size_order(sig, equity=10, price=0.5, cfg=cfg) == 0.0


def test_complement_conversion_preserves_kelly_size(market):
    """Sizing a SELL-YES directly (in YES space) and sizing its converted
    BUY-NO (in NO space) must produce the same notional commitment."""
    cfg = RiskConfig()
    sell_yes = Signal(
        strategy="s", token_id=market.yes_token_id, condition_id="c",
        side=Side.SELL, fair_value=0.30, market_price=0.40, edge=0.10,
        confidence=1.0,
    )
    buy_no = as_complement_buy(sell_yes, market)
    assert buy_no.side == Side.BUY
    assert buy_no.token_id == market.no_token_id
    assert abs(buy_no.fair_value - 0.70) < 1e-12
    assert abs(buy_no.market_price - 0.60) < 1e-12
    # Kelly agreement: f_sell(q, p) == f_buy(1-q, 1-p)
    f_sell = kelly_fraction_binary(0.30, 0.40, Side.SELL)
    f_buy = kelly_fraction_binary(0.70, 0.60, Side.BUY)
    assert abs(f_sell - f_buy) < 1e-12
    # And size_order agrees on notional: SELL YES at p costs (1-p) per share;
    # BUY NO at (1-p) costs (1-p) per share.
    s1 = size_order(sell_yes, 10_000, 0.40, cfg)
    s2 = size_order(buy_no, 10_000, 0.60, cfg)
    assert s1 > 0
    assert abs(s1 * (1 - 0.40) - s2 * 0.60) < 1e-6


# ---------------- portfolio ----------------
def test_portfolio_buy_sell_roundtrip():
    pf = Portfolio(1000.0)
    pf.apply_fill(_fill(Side.BUY, 0.40, 100))
    assert abs(pf.cash - 960.0) < 1e-9
    assert abs(pf.positions["tok_yes"].avg_price - 0.40) < 1e-9
    pf.apply_fill(_fill(Side.SELL, 0.50, 100))
    assert abs(pf.cash - 1010.0) < 1e-9
    assert abs(pf.positions["tok_yes"].realized_pnl - 10.0) < 1e-9
    assert pf.positions["tok_yes"].size == 0.0


def test_portfolio_resolution_settlement():
    pf = Portfolio(1000.0)
    pf.apply_fill(_fill(Side.BUY, 0.60, 50))
    pf.resolve("tok_yes", payout=1.0, ts=NOW)
    assert abs(pf.cash - (1000 - 30 + 50)) < 1e-9
    assert abs(pf.positions["tok_yes"].realized_pnl - 20.0) < 1e-9


def test_equity_marks_open_positions():
    pf = Portfolio(1000.0)
    pf.apply_fill(_fill(Side.BUY, 0.40, 100))
    assert abs(pf.equity({"tok_yes": 0.55}) - (960 + 55)) < 1e-9
    # unmarked -> carried at avg price
    assert abs(pf.equity({}) - 1000.0) < 1e-9


# ---------------- limits ----------------
def _order(size=100.0, price=0.5) -> Order:
    return Order(
        order_id="o", token_id="t", condition_id="c", side=Side.BUY,
        order_type=OrderType.LIMIT, price=price, size=size,
    )


def _state(equity=10_000.0, exposure=0.0, per_market=None) -> RiskState:
    return RiskState(
        equity=equity, peak_equity=equity, exposure=exposure,
        per_market_notional=per_market or {},
    )


def test_limits_accept_normal_order():
    rm = RiskManager(RiskConfig())
    ok, reason = rm.check_order(_order(), _state())
    assert ok, reason


def test_limits_reject_per_market_and_total():
    rm = RiskManager(RiskConfig(max_position_per_market=40.0))
    ok, reason = rm.check_order(_order(size=100, price=0.5), _state())
    assert not ok and reason == "per_market_limit"
    rm2 = RiskManager(RiskConfig(max_total_exposure=10.0))
    ok, reason = rm2.check_order(_order(size=100, price=0.5), _state())
    assert not ok and reason == "total_exposure_limit"


def test_kill_switch_halts_trading():
    rm = RiskManager(RiskConfig(max_drawdown_pct=0.10))
    rm.update_equity(equity=8_500.0, peak_equity=10_000.0)
    assert rm.halted
    ok, reason = rm.check_order(_order(), _state())
    assert not ok and reason == "kill_switch_active"
