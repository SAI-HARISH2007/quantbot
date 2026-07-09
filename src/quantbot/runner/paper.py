"""Live paper trading loop.

Polls real Polymarket books + Binance spot on an interval, runs the same
strategies used in backtests, fills against the *actual* current book via
PaperBroker, and persists fills/equity for later analysis. This is the
final validation stage before any live promotion.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

import pandas as pd

from quantbot.config import AppConfig
from quantbot.core.types import MarketInfo, Order, OrderBook, OrderType, Side, Signal
from quantbot.data.crypto.binance import BinanceClient
from quantbot.data.polymarket.clob import ClobClient
from quantbot.data.storage import Store
from quantbot.execution.paper import PaperBroker
from quantbot.fairvalue.base import FairValueContext, FairValueModel
from quantbot.portfolio.portfolio import Portfolio
from quantbot.risk.limits import RiskManager, RiskState
from quantbot.risk.sizing import as_complement_buy, size_order
from quantbot.strategies.base import MarketView, Strategy

logger = logging.getLogger(__name__)


class PaperRunner:
    def __init__(
        self,
        cfg: AppConfig,
        store: Store,
        strategies: list[Strategy],
        fair_value: Optional[FairValueModel] = None,
        poll_seconds: float = 30.0,
    ):
        self.cfg = cfg
        self.store = store
        self.strategies = strategies
        self.fair_value = fair_value
        self.poll_seconds = poll_seconds
        self.run_id = f"paper_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}"
        self.clob = ClobClient(cfg.polymarket)
        self.binance = BinanceClient(cfg.crypto)
        self.portfolio = Portfolio(cfg.risk.initial_capital)
        self.risk = RiskManager(cfg.risk)
        self.broker = PaperBroker(self._fetch_book, cfg.costs)
        self._book_cache: dict[str, OrderBook] = {}
        self._history: dict[str, list[tuple[datetime, float]]] = {}
        self._candles: dict[str, pd.DataFrame] = {}

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
                now=now,
                market=market,
                book=book,
                pm_price_history=hist_df,
                candles_by_symbol={s: df for s, df in self._candles.items() if len(df)},
            )
            try:
                fv = self.fair_value.estimate(ctx)
            except Exception:  # noqa: BLE001
                logger.exception("fair value failed for %s", market.slug)
        no_book = self._book_cache.get(market.no_token_id)
        return MarketView(
            now=now, market=market, price=book.mid, book=book, history=hist_df,
            fair_value=fv, extra={"no_book": no_book} if no_book else {},
        )

    async def _handle_signal(self, sig: Signal, market: MarketInfo) -> None:
        marks = {t: b.mid for t, b in self._book_cache.items() if b.mid is not None}
        equity = self.portfolio.equity(marks)
        book = self._book_cache.get(sig.token_id)
        if book is None:
            return
        pos = self.portfolio.positions.get(sig.token_id)
        if sig.side == Side.SELL and (pos is None or pos.size <= 0):
            sig = as_complement_buy(sig, market)
            pos = self.portfolio.positions.get(sig.token_id)
            book = self._book_cache.get(sig.token_id)
            if book is None:
                return
        side, token_id = sig.side, sig.token_id
        ref = book.best_ask if side == Side.BUY else book.best_bid
        if ref is None:
            return
        limit = min(ref.price + 0.02, 0.999) if side == Side.BUY else max(ref.price - 0.02, 0.001)
        # Size against the worst price we might pay (the limit), so sizing and
        # the risk check agree on notional.
        size = size_order(sig, equity, limit, self.cfg.risk)
        if size <= 0:
            return
        if side == Side.SELL and pos is not None:
            size = min(size, pos.size)
        order = Order(
            order_id=uuid.uuid4().hex[:12],
            token_id=token_id,
            condition_id=sig.condition_id,
            side=side,
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
        ok, reason = self.risk.check_order(order, state)
        if not ok:
            logger.info("order rejected (%s)", reason)
            return
        fill = await self.broker.submit(order)
        if fill:
            self.portfolio.apply_fill(fill)
            self.store.save_fill(fill, self.run_id)
            logger.info(
                "[%s] %s %s %.1f @ %.3f (%s)",
                self.run_id, fill.side.value, market.slug[:40], fill.size,
                fill.price, sig.strategy,
            )

    async def run(self, markets: list[MarketInfo], duration_seconds: float = 0) -> None:
        logger.info(
            "paper run %s: %d markets, %d strategies, $%.0f capital",
            self.run_id, len(markets), len(self.strategies),
            self.cfg.risk.initial_capital,
        )
        started = datetime.now(timezone.utc)
        try:
            while True:
                now = datetime.now(timezone.utc)
                await self._refresh_candles()
                for m in markets:
                    await self._fetch_book(m.yes_token_id)
                    await self._fetch_book(m.no_token_id)
                    view = self._view_for(m, now)
                    if view is None:
                        continue
                    for strat in self.strategies:
                        for sig in strat.on_view(view):
                            await self._handle_signal(sig, m)
                marks = {t: b.mid for t, b in self._book_cache.items() if b.mid is not None}
                eq = self.portfolio.record_equity(now, marks)
                self.risk.update_equity(eq, self.portfolio.peak_equity)
                logger.info("equity: $%.2f (peak $%.2f)", eq, self.portfolio.peak_equity)
                if duration_seconds and (now - started).total_seconds() >= duration_seconds:
                    break
                await asyncio.sleep(self.poll_seconds)
        finally:
            await self.clob.close()
            await self.binance.close()
            logger.info("paper run %s finished; fills stored under run_id", self.run_id)
