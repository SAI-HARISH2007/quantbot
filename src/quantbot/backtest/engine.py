"""Event-driven backtest engine over Polymarket price history.

Honest-simulation notes (all assumptions explicit and configurable):

* Historical *full books* aren't available from public endpoints, so fills
  use a synthetic book: executable buy price = observed price + half the
  assumed spread + slippage; sells symmetric. The assumed spread is a cost
  parameter — sensitivity to it is part of every strategy report.
* SELL YES without inventory is executed as BUY NO at (1 - price), matching
  how Polymarket actually settles short exposure.
* Settlement: markets whose final observed price is >= yes_threshold are
  treated as resolved YES, <= no_threshold as NO. In-between markets settle
  at the last observed price (a mark, not a real outcome) — strategy metrics
  can exclude those via the `settled_only` flag in reports.
* Signals act on the *next* event for their market (no look-ahead), after a
  configurable latency.
"""
from __future__ import annotations

import itertools
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Optional

import pandas as pd

from quantbot.config import AppConfig
from quantbot.core.clock import SimClock
from quantbot.core.types import (
    Fill, MarketInfo, Order, OrderType, Side, Signal,
)
from quantbot.backtest.trades import TradeLog
from quantbot.fairvalue.base import FairValueContext, FairValueEstimate, FairValueModel
from quantbot.portfolio.portfolio import Portfolio
from quantbot.risk.limits import RiskManager, RiskState
from quantbot.risk.sizing import as_complement_buy, size_order
from quantbot.strategies.base import MarketView, Strategy

logger = logging.getLogger(__name__)


@dataclass
class BacktestData:
    """Inputs: per-market YES price history plus optional underlying candles."""

    markets: dict[str, MarketInfo]  # condition_id -> info
    prices: dict[str, pd.DataFrame]  # condition_id -> [ts, price] (YES token)
    candles: dict[str, pd.DataFrame] = field(default_factory=dict)  # symbol -> OHLCV


@dataclass
class BacktestResult:
    run_id: str
    equity: pd.Series
    trades: pd.DataFrame
    fills: list[Fill]
    params: dict = field(default_factory=dict)

    @property
    def final_equity(self) -> float:
        return float(self.equity.iloc[-1]) if len(self.equity) else 0.0


class BacktestEngine:
    def __init__(
        self,
        cfg: AppConfig,
        strategies: list[Strategy],
        fair_value: Optional[FairValueModel] = None,
        assumed_spread: float = 0.02,
        yes_threshold: float = 0.90,
        no_threshold: float = 0.10,
        history_window: int = 200,
    ):
        self.cfg = cfg
        self.strategies = strategies
        self.fair_value = fair_value
        self.assumed_spread = assumed_spread
        self.yes_threshold = yes_threshold
        self.no_threshold = no_threshold
        # Strategies see at most this many trailing bars. Bounds per-event
        # cost to O(window) — full-history views made the loop O(n²).
        self.history_window = history_window

    # ---------- synthetic execution ----------
    def _exec_price(self, mid: float, side: Side) -> float:
        # Spread scales down near the 0/1 bounds: a 2c spread on a 2c token
        # would be a >100% cost, which real books don't exhibit. Cap the
        # synthetic spread at 30% of the distance to the nearer bound.
        spread = min(self.assumed_spread, 0.3 * min(mid, 1.0 - mid))
        half = spread / 2 + self.cfg.costs.extra_slippage
        px = mid + half if side == Side.BUY else mid - half
        return min(max(px, 0.001), 0.999)

    def _fill(self, order: Order, mid: float) -> Fill:
        px = self._exec_price(mid, order.side)
        fee = px * order.size * self.cfg.costs.taker_fee_bps / 10_000
        return Fill(
            order_id=order.order_id,
            token_id=order.token_id,
            condition_id=order.condition_id,
            side=order.side,
            price=px,
            size=order.size,
            fee=fee,
            ts=order.ts,
            strategy=order.strategy,
        )

    # ---------- main loop ----------
    def run(self, data: BacktestData) -> BacktestResult:
        run_id = uuid.uuid4().hex[:12]
        for s in self.strategies:
            s.reset()
        portfolio = Portfolio(self.cfg.risk.initial_capital)
        risk = RiskManager(self.cfg.risk)
        trade_log = TradeLog()
        fills: list[Fill] = []

        # Build the merged event stream: (ts, condition_id, price)
        events: list[tuple[datetime, str, float]] = []
        for cid, df in data.prices.items():
            if cid not in data.markets or df.empty:
                continue
            sub = df.sort_values("ts")
            events.extend(
                (ts.to_pydatetime(), cid, float(p))
                for ts, p in zip(pd.to_datetime(sub["ts"], utc=True), sub["price"])
            )
        events.sort(key=lambda e: e[0])
        if not events:
            return BacktestResult(run_id, pd.Series(dtype=float), trade_log.to_frame(), [])

        clock = SimClock(events[0][0])
        marks: dict[str, float] = {}  # token_id -> mark
        last_price: dict[str, float] = {}  # condition_id -> latest YES price
        # signals waiting for the next event on their market (latency realism)
        pending: dict[str, list[Signal]] = {}
        history_cache: dict[str, list[tuple[datetime, float]]] = {cid: [] for cid in data.prices}
        candle_cursor = {
            sym: df.sort_values("ts").reset_index(drop=True) for sym, df in data.candles.items()
        }

        equity_points: list[tuple[datetime, float]] = []
        last_equity_ts: Optional[datetime] = None

        for ts, cid, price in events:
            clock.advance_to(ts)
            market = data.markets[cid]
            last_price[cid] = price
            marks[market.yes_token_id] = price
            marks[market.no_token_id] = 1.0 - price
            history_cache[cid].append((ts, price))

            # 1) execute pending signals for this market at *this* event
            for sig in pending.pop(cid, []):
                if (ts - sig.ts).total_seconds() > sig.ttl_seconds:
                    continue
                self._execute_signal(
                    sig, market, price, portfolio, risk, trade_log, fills, marks, ts
                )

            # 2) build view and collect new signals (windowed history)
            window = history_cache[cid][-self.history_window:]
            hist_df = pd.DataFrame(window, columns=["ts", "price"])
            fv = self._fair_value_for(market, ts, price, hist_df, candle_cursor)
            view = MarketView(
                now=ts, market=market, price=price, history=hist_df, fair_value=fv
            )
            for strat in self.strategies:
                for sig in strat.on_view(view):
                    pending.setdefault(cid, []).append(sig)

            # 3) mark equity once per hour of sim time
            if last_equity_ts is None or ts - last_equity_ts >= timedelta(hours=1):
                eq = portfolio.record_equity(ts, marks)
                risk.update_equity(eq, portfolio.peak_equity)
                equity_points.append((ts, eq))
                last_equity_ts = ts

        # 4) settle all markets at their final observed price
        end_ts = events[-1][0]
        for cid, market in data.markets.items():
            p = last_price.get(cid)
            if p is None:
                continue
            if p >= self.yes_threshold:
                yes_pay, no_pay = 1.0, 0.0
            elif p <= self.no_threshold:
                yes_pay, no_pay = 0.0, 1.0
            else:
                yes_pay, no_pay = p, 1.0 - p  # mark, not a real outcome
            for tid, pay in ((market.yes_token_id, yes_pay), (market.no_token_id, no_pay)):
                portfolio.resolve(tid, pay, end_ts)
                trade_log.on_settlement(tid, pay, end_ts)
        eq = portfolio.record_equity(end_ts, {})
        equity_points.append((end_ts, eq))

        equity = pd.Series(
            [e for _, e in equity_points],
            index=pd.DatetimeIndex([t for t, _ in equity_points], tz="UTC"),
        )
        equity = equity[~equity.index.duplicated(keep="last")]
        return BacktestResult(run_id, equity, trade_log.to_frame(), fills)

    # ---------- helpers ----------
    def _fair_value_for(
        self,
        market: MarketInfo,
        ts: datetime,
        price: float,
        hist_df: pd.DataFrame,
        candles: dict[str, pd.DataFrame],
    ) -> Optional[FairValueEstimate]:
        if self.fair_value is None:
            return None
        by_symbol = {}
        for sym, df in candles.items():
            upto = df[df["ts"] <= ts]
            if len(upto) >= 30:
                by_symbol[sym] = upto.tail(24 * 60)
        ctx = FairValueContext(
            now=ts, market=market, pm_price_history=hist_df,
            candles_by_symbol=by_symbol,
        )
        try:
            return self.fair_value.estimate(ctx)
        except Exception:  # noqa: BLE001
            logger.exception("fair value failed for %s", market.slug)
            return None

    def _execute_signal(
        self,
        sig: Signal,
        market: MarketInfo,
        price: float,
        portfolio: Portfolio,
        risk: RiskManager,
        trade_log: TradeLog,
        fills: list[Fill],
        marks: dict[str, float],
        ts: datetime,
    ) -> None:
        equity = portfolio.equity(marks)
        # SELL with inventory -> sell the token; otherwise convert the whole
        # signal into the complement-token BUY (space-consistent sizing).
        pos = portfolio.positions.get(sig.token_id)
        if sig.side == Side.SELL and (pos is None or pos.size <= 0):
            sig = as_complement_buy(sig, market)
            pos = portfolio.positions.get(sig.token_id)
            exec_mid = 1.0 - price
        else:
            exec_mid = price
        side, token_id = sig.side, sig.token_id
        # Size against the price we will actually pay (incl. spread/slippage),
        # so sizing and risk checks agree on notional.
        exec_px = self._exec_price(exec_mid, side)
        size = size_order(sig, equity, exec_px, self.cfg.risk)
        if size <= 0:
            return
        if side == Side.SELL and pos is not None:
            size = min(size, pos.size)
            if size <= 0:
                return
        order = Order(
            order_id=uuid.uuid4().hex[:12],
            token_id=token_id,
            condition_id=sig.condition_id,
            side=side,
            order_type=OrderType.MARKET,
            price=exec_px,
            size=size,
            strategy=sig.strategy,
            ts=ts,
        )
        state = RiskState(
            equity=equity,
            peak_equity=portfolio.peak_equity,
            exposure=portfolio.exposure(marks),
            per_market_notional=portfolio.per_market_notional(marks),
        )
        ok, reason = risk.check_order(order, state)
        if not ok:
            logger.debug("order rejected (%s): %s", reason, order.order_id)
            return
        fill = self._fill(order, exec_mid)
        portfolio.apply_fill(fill)
        trade_log.on_fill(fill)
        fills.append(fill)


def grid_search(
    cfg: AppConfig,
    data: BacktestData,
    strategy_cls: type[Strategy],
    param_grid: dict[str, list],
    fair_value: Optional[FairValueModel] = None,
    metric: Callable[[BacktestResult], float] = lambda r: r.final_equity,
) -> list[tuple[dict, BacktestResult]]:
    """Exhaustive parameter sweep; returns (params, result) sorted best-first."""
    keys = list(param_grid)
    results = []
    for combo in itertools.product(*(param_grid[k] for k in keys)):
        params = dict(zip(keys, combo))
        engine = BacktestEngine(cfg, [strategy_cls(**params)], fair_value=fair_value)
        res = engine.run(data)
        res.params = params
        results.append((params, res))
    results.sort(key=lambda pr: metric(pr[1]), reverse=True)
    return results
