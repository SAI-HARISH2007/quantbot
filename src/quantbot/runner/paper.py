"""Live paper trading engine.

Trades real Polymarket books with simulated money, with three properties
the dashboard and daily reports depend on:

* **Every decision is recorded** — signals that became trades AND signals
  that were rejected, with fair-value evidence, sizing math, and the risk
  verdict (core/decisions.py).
* **Every position close produces a TradeCloseReport** — exit reason, PnL,
  fees, slippage, and whether the entry hypothesis held up.
* **State persists** — portfolio, open-lot bookkeeping, and equity history
  are checkpointed to the store every cycle, so a crash or restart resumes
  (``resume=True``) instead of resetting.

Only the execution client would change for live trading; everything else
in this file is endpoint-agnostic by design.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional

import pandas as pd

from quantbot.config import AppConfig
from quantbot.core.decisions import DecisionRecord, TradeCloseReport
from quantbot.core.types import (
    Fill, MarketInfo, Order, OrderBook, OrderType, Side, Signal,
)
from quantbot.data.crypto.binance import BinanceClient
from quantbot.data.polymarket.clob import ClobClient
from quantbot.data.storage import Store
from quantbot.execution.paper import PaperBroker
from quantbot.fairvalue.base import FairValueContext, FairValueModel
from quantbot.portfolio.portfolio import Portfolio
from quantbot.risk.limits import RiskManager, RiskState
from quantbot.risk.sizing import as_complement_buy, size_order_explain
from quantbot.strategies.base import MarketView, Strategy

logger = logging.getLogger(__name__)

# Event sink: the dashboard subscribes here. Signature: (event_type, data).
EventSink = Callable[[str, dict], Awaitable[None]]


async def _null_sink(_t: str, _d: dict) -> None:
    return None


class PaperRunner:
    def __init__(
        self,
        cfg: AppConfig,
        store: Store,
        strategies: list[Strategy],
        fair_value: Optional[FairValueModel] = None,
        poll_seconds: float = 30.0,
        sink: EventSink = _null_sink,
        resume: bool = False,
    ):
        self.cfg = cfg
        self.store = store
        self.strategies = strategies
        self.fair_value = fair_value
        self.poll_seconds = poll_seconds
        self.sink = sink
        self.clob = ClobClient(cfg.polymarket)
        self.binance = BinanceClient(cfg.crypto)
        self.portfolio = Portfolio(cfg.risk.initial_capital)
        self.risk = RiskManager(cfg.risk)
        self.broker = PaperBroker(self._fetch_book, cfg.costs)
        self._book_cache: dict[str, OrderBook] = {}
        self._history: dict[str, list[tuple[datetime, float]]] = {}
        self._candles: dict[str, pd.DataFrame] = {}
        self._last_fv: dict[str, dict] = {}  # condition_id -> fv snapshot
        # open-lot bookkeeping for close reports: token -> list of lot dicts
        self._lots: dict[str, list[dict]] = {}
        self.markets: dict[str, MarketInfo] = {}
        self.run_id = f"paper_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}"
        self.started_at = datetime.now(timezone.utc)
        self.cycle_count = 0
        self.last_cycle_at: Optional[datetime] = None
        if resume:
            self._try_resume()

    # ------------------------------------------------------------- state
    def _try_resume(self) -> None:
        loaded = self.store.load_runner_state()
        if not loaded:
            logger.info("no previous paper state found; starting fresh")
            return
        run_id, state = loaded
        self.run_id = run_id
        self.portfolio.cash = state["cash"]
        self.portfolio.peak_equity = state.get("peak_equity", self.portfolio.cash)
        from quantbot.core.types import Position

        self.portfolio.positions = {
            tid: Position(**p) for tid, p in state.get("positions", {}).items()
        }
        self._lots = state.get("lots", {})
        self.risk.halted = state.get("halted", False)
        logger.info(
            "resumed paper run %s: cash $%.2f, %d open positions%s",
            run_id, self.portfolio.cash,
            sum(1 for p in self.portfolio.positions.values() if p.size > 0),
            " [KILL SWITCH STILL ACTIVE]" if self.risk.halted else "",
        )

    def _checkpoint(self) -> None:
        self.store.save_runner_state(
            self.run_id,
            {
                "cash": self.portfolio.cash,
                "peak_equity": self.portfolio.peak_equity,
                "positions": {
                    tid: p.model_dump()
                    for tid, p in self.portfolio.positions.items()
                    if p.size > 0
                },
                "lots": {t: lots for t, lots in self._lots.items() if lots},
                "halted": self.risk.halted,
            },
        )

    # ------------------------------------------------------------- data
    async def _fetch_book(self, token_id: str) -> Optional[OrderBook]:
        try:
            book = await self.clob.get_book(token_id)
            self._book_cache[token_id] = book
            return book
        except Exception as e:  # noqa: BLE001
            logger.warning("book fetch failed %s: %s", token_id[:16], e)
            return None

    async def _refresh_candles(self) -> None:
        for sym in self.cfg.crypto.symbols:
            try:
                candles = await self.binance.get_klines(sym, "1m", limit=1000)
                self._candles[sym] = pd.DataFrame([c.model_dump() for c in candles])
            except Exception as e:  # noqa: BLE001
                logger.warning("candle refresh failed %s: %s", sym, e)

    def _marks(self) -> dict[str, float]:
        return {t: b.mid for t, b in self._book_cache.items() if b.mid is not None}

    # ------------------------------------------------------------- views
    def _view_for(self, market: MarketInfo, now: datetime) -> Optional[MarketView]:
        book = self._book_cache.get(market.yes_token_id)
        if book is None or book.mid is None:
            return None
        hist = self._history.setdefault(market.condition_id, [])
        hist.append((now, book.mid))
        if len(hist) > 5000:
            del hist[: len(hist) - 5000]
        hist_df = pd.DataFrame(hist, columns=["ts", "price"])
        fv = None
        if self.fair_value is not None:
            ctx = FairValueContext(
                now=now, market=market, book=book, pm_price_history=hist_df,
                candles_by_symbol={s: df for s, df in self._candles.items() if len(df)},
            )
            try:
                fv = self.fair_value.estimate(ctx)
            except Exception:  # noqa: BLE001
                logger.exception("fair value failed for %s", market.slug)
        self._last_fv[market.condition_id] = {
            "prob": fv.prob if fv else None,
            "std": fv.std if fv else None,
            "models": fv.detail if fv else {},
        }
        no_book = self._book_cache.get(market.no_token_id)
        return MarketView(
            now=now, market=market, price=book.mid, book=book, history=hist_df,
            fair_value=fv, extra={"no_book": no_book} if no_book else {},
        )

    async def _emit_market(self, market: MarketInfo, view: MarketView) -> None:
        book = view.book
        fv = self._last_fv.get(market.condition_id, {})
        await self.sink("market", {
            "condition_id": market.condition_id,
            "question": market.question,
            "slug": market.slug,
            "end_date": market.end_date.isoformat() if market.end_date else None,
            "price": view.price,
            "best_bid": book.best_bid.price if book and book.best_bid else None,
            "best_ask": book.best_ask.price if book and book.best_ask else None,
            "spread": book.spread if book else None,
            "imbalance": book.imbalance() if book else None,
            "bids": [[l.price, l.size] for l in (book.bids[:8] if book else [])],
            "asks": [[l.price, l.size] for l in (book.asks[:8] if book else [])],
            "fair_value": fv.get("prob"),
            "fair_value_std": fv.get("std"),
            "fv_models": fv.get("models", {}),
        })

    # ------------------------------------------------------------- lots
    def _record_entry_lot(self, fill: Fill, decision: DecisionRecord) -> None:
        self._lots.setdefault(fill.token_id, []).append({
            "entry_ts": fill.ts.isoformat(),
            "size": fill.size,
            "price": fill.price,
            "fee": fill.fee,
            "strategy": fill.strategy,
            "decision_id": decision.decision_id,
            "fair_value": decision.fair_value,
            "question": decision.market_question,
            "condition_id": fill.condition_id,
        })

    async def _close_lots(
        self, token_id: str, size: float, exit_price: float, exit_fee: float,
        exit_reason: str, ts: datetime,
    ) -> None:
        """FIFO-close lots and emit a TradeCloseReport per closed lot."""
        lots = self._lots.get(token_id, [])
        remaining = size
        while remaining > 1e-9 and lots:
            lot = lots[0]
            take = min(lot["size"], remaining)
            entry_ts = datetime.fromisoformat(lot["entry_ts"])
            pnl = (exit_price - lot["price"]) * take
            fees = lot["fee"] * (take / max(lot["size"], 1e-9)) + exit_fee
            fv = lot.get("fair_value")
            hypothesis = (
                f"Priced at {lot['price']:.3f}, model fair value {fv:.3f} — "
                f"expected price to rise toward fair value."
                if fv is not None and fv > lot["price"]
                else f"Entered at {lot['price']:.3f} on a {lot['strategy']} signal."
            )
            correct: Optional[bool] = None
            if exit_reason == "resolution":
                correct = exit_price > lot["price"]
            elif fv is not None:
                correct = abs(exit_price - fv) < abs(lot["price"] - fv)
            lessons = []
            if pnl < 0 and fees > abs(pnl) * 0.5:
                lessons.append("Costs were a large share of the loss — edge too small for the spread.")
            if pnl > 0 and correct:
                lessons.append("Price converged toward the model estimate as hypothesized.")
            if pnl < 0 and correct is False:
                lessons.append("The market moved further against fair value — model likely missed information.")
            report = TradeCloseReport(
                run_id=self.run_id,
                token_id=token_id,
                condition_id=lot["condition_id"],
                market_question=lot["question"],
                strategy=lot["strategy"],
                entry_decision_ids=[lot["decision_id"]],
                entry_ts=entry_ts,
                entry_price=lot["price"],
                exit_price=exit_price,
                size=take,
                pnl=pnl,
                fees=fees,
                holding_hours=(ts - entry_ts).total_seconds() / 3600,
                exit_reason=exit_reason,
                entry_fair_value=fv,
                hypothesis=hypothesis,
                hypothesis_correct=correct,
                lessons=" ".join(lessons) or "Within normal variance; no single cause dominates.",
            )
            self.store.save_trade_report(report)
            await self.sink("trade_closed", report.model_dump(mode="json"))
            lot["size"] -= take
            remaining -= take
            if lot["size"] <= 1e-9:
                lots.pop(0)

    # ------------------------------------------------------------- signals
    async def _handle_signal(self, sig: Signal, market: MarketInfo) -> None:
        marks = self._marks()
        equity = self.portfolio.equity(marks)
        decision = DecisionRecord(
            run_id=self.run_id,
            strategy=sig.strategy,
            condition_id=sig.condition_id,
            token_id=sig.token_id,
            market_question=market.question,
            side=sig.side.value,
            signal_edge=sig.edge,
            signal_confidence=sig.confidence,
            signal_metadata=sig.metadata,
            market_price=sig.market_price,
            expected_value=sig.edge,
        )
        fvs = self._last_fv.get(market.condition_id, {})
        decision.fair_value = fvs.get("prob")
        decision.fair_value_std = fvs.get("std")
        decision.fair_value_models = fvs.get("models", {})

        pos = self.portfolio.positions.get(sig.token_id)
        exit_trade = sig.side == Side.SELL and pos is not None and pos.size > 0
        if sig.side == Side.SELL and not exit_trade:
            sig = as_complement_buy(sig, market)
            pos = self.portfolio.positions.get(sig.token_id)
            decision.side = f"SELL→BUY {'NO' if sig.token_id == market.no_token_id else 'YES'}"
            decision.token_id = sig.token_id
        book = self._book_cache.get(sig.token_id)
        if book is None:
            decision.outcome = "rejected"
            decision.risk_reason = "no_book"
            await self._finish_decision(decision)
            return
        ref = book.best_ask if sig.side == Side.BUY else book.best_bid
        if ref is None:
            decision.outcome = "rejected"
            decision.risk_reason = "empty_book"
            await self._finish_decision(decision)
            return
        decision.best_bid = book.best_bid.price if book.best_bid else None
        decision.best_ask = book.best_ask.price if book.best_ask else None
        decision.spread = book.spread
        decision.book_imbalance = book.imbalance()

        limit = (
            min(ref.price + 0.02, 0.999) if sig.side == Side.BUY
            else max(ref.price - 0.02, 0.001)
        )
        decision.limit_price = limit
        size, sizing = size_order_explain(sig, equity, limit, self.cfg.risk)
        decision.sizing = sizing
        if size <= 0:
            decision.outcome = "rejected"
            decision.risk_reason = sizing.get("result", "sized_to_zero")
            await self._finish_decision(decision)
            return
        if sig.side == Side.SELL and pos is not None:
            size = min(size, pos.size)
        order = Order(
            order_id=uuid.uuid4().hex[:12],
            token_id=sig.token_id,
            condition_id=sig.condition_id,
            side=sig.side,
            order_type=OrderType.LIMIT,
            price=limit,
            size=size,
            strategy=sig.strategy,
        )
        state = RiskState(
            equity=equity,
            peak_equity=self.portfolio.peak_equity,
            exposure=self.portfolio.exposure(marks),
            per_market_notional=self.portfolio.per_market_notional(marks),
        )
        decision.risk_state = {
            "equity": equity,
            "exposure": state.exposure,
            "market_notional": state.per_market_notional.get(order.condition_id, 0.0),
        }
        ok, reason = self.risk.check_order(order, state)
        decision.risk_ok = ok
        decision.risk_reason = reason
        if not ok:
            decision.outcome = "rejected"
            await self._finish_decision(decision)
            return
        decision.order_id = order.order_id
        await self.sink("order", {
            "order_id": order.order_id, "status": "submitted",
            "side": order.side.value, "token_id": order.token_id,
            "price": order.price, "size": order.size, "strategy": order.strategy,
            "question": market.question,
        })
        fill = await self.broker.submit(order)
        if fill is None:
            decision.outcome = "unfilled"
            await self.sink("order", {"order_id": order.order_id, "status": "unfilled"})
            await self._finish_decision(decision)
            return
        decision.outcome = "filled"
        decision.fill_price = fill.price
        decision.fill_size = fill.size
        decision.fee = fill.fee
        mid = book.mid
        if mid:
            decision.slippage = (
                fill.price - mid if fill.side == Side.BUY else mid - fill.price
            )
        self.portfolio.apply_fill(fill)
        self.store.save_fill(fill, self.run_id)
        if fill.side == Side.BUY:
            self._record_entry_lot(fill, decision)
        else:
            await self._close_lots(
                fill.token_id, fill.size, fill.price, fill.fee,
                "opposite_signal", fill.ts,
            )
        await self.sink("fill", {
            **fill.model_dump(mode="json"),
            "question": market.question,
            "decision_id": decision.decision_id,
        })
        await self._finish_decision(decision)
        logger.info(
            "[%s] %s %s %.1f @ %.3f (%s, decision %s)",
            self.run_id, fill.side.value, market.slug[:40], fill.size,
            fill.price, sig.strategy, decision.decision_id,
        )

    async def _finish_decision(self, decision: DecisionRecord) -> None:
        self.store.save_decision(decision)
        await self.sink("decision", decision.model_dump(mode="json"))

    # ------------------------------------------------------------- resolution
    async def _settle_expired(self, now: datetime) -> None:
        """Settle positions in markets past their end date at the final mark
        (>=0.90 -> $1, <=0.10 -> $0, else last mark; same proxy the backtester
        uses and flagged as such in the close report)."""
        for m in list(self.markets.values()):
            if not m.end_date or m.end_date > now:
                continue
            for tid in (m.yes_token_id, m.no_token_id):
                pos = self.portfolio.positions.get(tid)
                if pos is None or pos.size <= 0:
                    continue
                mark = self._marks().get(tid, pos.avg_price)
                payout = 1.0 if mark >= 0.90 else 0.0 if mark <= 0.10 else mark
                self.portfolio.resolve(tid, payout, now)
                await self._close_lots(tid, pos.size + 1e-9, payout, 0.0, "resolution", now)
                logger.info("settled %s at %.2f (market ended)", m.slug[:40], payout)

    # ------------------------------------------------------------- main loop
    async def run(self, markets: list[MarketInfo], duration_seconds: float = 0) -> None:
        self.markets = {m.condition_id: m for m in markets}
        logger.info(
            "paper run %s: %d markets, %d strategies, $%.0f capital, kill switch %s",
            self.run_id, len(markets), len(self.strategies),
            self.cfg.risk.initial_capital,
            "ACTIVE (halted)" if self.risk.halted else "armed",
        )
        await self.sink("status", self.status())
        try:
            while True:
                now = datetime.now(timezone.utc)
                await self._refresh_candles()
                for m in markets:
                    await self.sink("thinking", {
                        "phase": "data", "market": m.question,
                        "condition_id": m.condition_id,
                        "text": f"Fetching books for “{m.question[:60]}”",
                    })
                    await self._fetch_book(m.yes_token_id)
                    await self._fetch_book(m.no_token_id)
                    view = self._view_for(m, now)
                    if view is None:
                        continue
                    await self._emit_market(m, view)
                    fv = self._last_fv.get(m.condition_id, {})
                    if fv.get("prob") is not None and view.price is not None:
                        edge = fv["prob"] - view.price
                        await self.sink("thinking", {
                            "phase": "models", "market": m.question,
                            "condition_id": m.condition_id,
                            "edge": edge,
                            "text": (
                                f"Fair value {fv['prob']:.3f}±{fv['std']:.3f} vs price "
                                f"{view.price:.3f} → edge {edge:+.3f} "
                                f"({len(fv.get('models', {}))} models)"
                            ),
                        })
                    for strat in self.strategies:
                        for sig in strat.on_view(view):
                            await self.sink("signal", {
                                "strategy": sig.strategy, "side": sig.side.value,
                                "condition_id": sig.condition_id,
                                "question": m.question, "edge": sig.edge,
                                "confidence": sig.confidence,
                                "ts": sig.ts.isoformat(),
                            })
                            await self._handle_signal(sig, m)
                await self._settle_expired(now)
                marks = self._marks()
                eq = self.portfolio.record_equity(now, marks)
                was_halted = self.risk.halted
                self.risk.update_equity(eq, self.portfolio.peak_equity)
                if self.risk.halted and not was_halted:
                    await self.sink("alert", {
                        "level": "critical",
                        "message": "KILL SWITCH: drawdown limit hit — all new trading halted.",
                    })
                self.store.save_equity_point(
                    self.run_id, now, eq, self.portfolio.cash,
                    self.portfolio.exposure(marks),
                )
                self._checkpoint()
                self.cycle_count += 1
                self.last_cycle_at = now
                await self.sink("status", self.status())
                if duration_seconds and (
                    now - self.started_at
                ).total_seconds() >= duration_seconds:
                    break
                await asyncio.sleep(self.poll_seconds)
        finally:
            self._checkpoint()
            await self.clob.close()
            await self.binance.close()
            logger.info("paper run %s stopped; state checkpointed", self.run_id)

    # ------------------------------------------------------------- regime
    def _regime(self) -> dict:
        """Simple, honest regime gauge: BTC realized vol percentile vs its own
        recent history + average Polymarket spread. Not a prediction — a
        description of current conditions."""
        vol_pct = None
        btc = self._candles.get("BTCUSDT")
        if btc is not None and len(btc) > 120:
            import numpy as np

            r = np.log(btc["close"]).diff().dropna()
            recent = float(r.tail(60).std())
            windows = r.rolling(60).std().dropna()
            if len(windows) > 10 and windows.max() > 0:
                vol_pct = float((windows < recent).mean())
        spreads = [b.spread for b in self._book_cache.values() if b.spread is not None]
        avg_spread = sum(spreads) / len(spreads) if spreads else None
        if vol_pct is None:
            label = "warming up"
        elif vol_pct > 0.85:
            label = "volatile"
        elif vol_pct > 0.55:
            label = "active"
        else:
            label = "calm"
        return {"label": label, "vol_percentile": vol_pct, "avg_spread": avg_spread}

    # ------------------------------------------------------------- status
    def status(self) -> dict:
        marks = self._marks()
        eq = self.portfolio.equity(marks)
        exposure = self.portfolio.exposure(marks)
        return {
            "run_id": self.run_id,
            "regime": self._regime(),
            "started_at": self.started_at.isoformat(),
            "cycle": self.cycle_count,
            "last_cycle_at": self.last_cycle_at.isoformat() if self.last_cycle_at else None,
            "equity": eq,
            "cash": self.portfolio.cash,
            "buying_power": max(self.cfg.risk.max_total_exposure - exposure, 0.0),
            "exposure": exposure,
            "peak_equity": self.portfolio.peak_equity,
            "drawdown": 1 - eq / self.portfolio.peak_equity if self.portfolio.peak_equity else 0,
            "realized_pnl": sum(p.realized_pnl for p in self.portfolio.positions.values()),
            "unrealized_pnl": sum(
                p.unrealized_pnl(marks.get(t, p.avg_price))
                for t, p in self.portfolio.positions.items() if p.size > 0
            ),
            "halted": self.risk.halted,
            "positions": [
                {
                    "token_id": t,
                    "condition_id": p.condition_id,
                    "size": p.size,
                    "avg_price": p.avg_price,
                    "mark": marks.get(t, p.avg_price),
                    "unrealized_pnl": p.unrealized_pnl(marks.get(t, p.avg_price)),
                    "question": next(
                        (m.question for m in self.markets.values()
                         if t in (m.yes_token_id, m.no_token_id)), "",
                    ),
                    "outcome": "YES" if any(
                        m.yes_token_id == t for m in self.markets.values()
                    ) else "NO",
                }
                for t, p in self.portfolio.positions.items() if p.size > 0
            ],
        }
