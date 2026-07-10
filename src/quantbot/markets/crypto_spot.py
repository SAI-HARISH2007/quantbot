"""Crypto spot adapter (Binance public data). Linear payoff.

Data side is complete (instruments, synthesized book from best quote,
history). Paper *execution* for linear instruments requires the linear
sizing/portfolio path in the engine — tracked in docs/ARCHITECTURE.md
multi-market roadmap; strategies remain research-only on this venue until
that lands. This adapter proves the plugin contract with a second, very
different venue.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import httpx
import pandas as pd

from quantbot.config import CryptoConfig
from quantbot.core.types import BookLevel, OrderBook
from quantbot.data.crypto.binance import BinanceClient
from quantbot.markets.base import AdapterRegistry, Instrument, MarketAdapter, PayoffType

_DEFAULT_UNIVERSE = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]


@AdapterRegistry.register
class CryptoSpotAdapter(MarketAdapter):
    name = "binance_spot"

    def __init__(self, cfg: Optional[CryptoConfig] = None,
                 universe: Optional[list[str]] = None):
        self.cfg = cfg or CryptoConfig()
        self.universe = universe or _DEFAULT_UNIVERSE
        self.client = BinanceClient(self.cfg)
        self._http = httpx.AsyncClient(base_url=self.cfg.binance_url,
                                       timeout=self.cfg.request_timeout)

    async def list_instruments(self, min_liquidity: float = 0.0) -> list[Instrument]:
        return [
            Instrument(
                instrument_id=sym, venue=self.name, symbol=sym,
                description=f"{sym} spot", payoff=PayoffType.LINEAR,
                quote_currency="USDT", tick_size=0.01, min_order_size=10.0,
            )
            for sym in self.universe
        ]

    async def get_book(self, instrument_id: str) -> Optional[OrderBook]:
        r = await self._http.get("/api/v3/ticker/bookTicker",
                                 params={"symbol": instrument_id})
        r.raise_for_status()
        d = r.json()
        return OrderBook(
            token_id=instrument_id,
            ts=datetime.now(timezone.utc),
            bids=[BookLevel(price=float(d["bidPrice"]), size=float(d["bidQty"]))],
            asks=[BookLevel(price=float(d["askPrice"]), size=float(d["askQty"]))],
        )

    async def get_history(
        self, instrument_id: str, start: datetime, end: datetime, bar_minutes: int = 10
    ) -> pd.DataFrame:
        interval = "1m" if bar_minutes <= 1 else "5m" if bar_minutes <= 5 else "15m"
        candles = await self.client.get_klines_range(
            instrument_id, interval,
            int(start.timestamp() * 1000), int(end.timestamp() * 1000),
        )
        return pd.DataFrame([{"ts": c.ts, "price": c.close} for c in candles])

    async def close(self) -> None:
        await self.client.close()
        await self._http.aclose()
