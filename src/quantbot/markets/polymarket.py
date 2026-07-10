"""Polymarket adapter: the reference MarketAdapter implementation, wrapping
the existing Gamma/CLOB connectors. Binary payoff, prices in [0,1]."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

import pandas as pd

from quantbot.config import PolymarketConfig
from quantbot.core.types import OrderBook
from quantbot.data.polymarket.clob import ClobClient
from quantbot.data.polymarket.gamma import GammaClient
from quantbot.markets.base import AdapterRegistry, Instrument, MarketAdapter, PayoffType


@AdapterRegistry.register
class PolymarketAdapter(MarketAdapter):
    name = "polymarket"

    def __init__(self, cfg: Optional[PolymarketConfig] = None):
        self.cfg = cfg or PolymarketConfig()
        self.gamma = GammaClient(self.cfg)
        self.clob = ClobClient(self.cfg)

    async def list_instruments(self, min_liquidity: float = 0.0) -> list[Instrument]:
        markets = await self.gamma.list_markets(min_liquidity=min_liquidity)
        out: list[Instrument] = []
        for m in markets:
            for token_id, outcome in ((m.yes_token_id, "YES"), (m.no_token_id, "NO")):
                out.append(Instrument(
                    instrument_id=token_id,
                    venue=self.name,
                    symbol=f"{m.slug}:{outcome}",
                    description=m.question,
                    payoff=PayoffType.BINARY,
                    quote_currency="USDC",
                    tick_size=m.min_tick,
                    min_order_size=m.min_order_size,
                    price_bounds=(0.0, 1.0),
                    expiry=m.end_date,
                    meta={"condition_id": m.condition_id, "outcome": outcome,
                          "liquidity": m.liquidity},
                ))
        return out

    async def get_book(self, instrument_id: str) -> Optional[OrderBook]:
        return await self.clob.get_book(instrument_id)

    async def get_history(
        self, instrument_id: str, start: datetime, end: datetime, bar_minutes: int = 10
    ) -> pd.DataFrame:
        points = await self.clob.get_price_history(
            instrument_id,
            start_ts=int(start.timestamp()),
            end_ts=int(end.timestamp()),
            fidelity_minutes=bar_minutes,
        )
        return pd.DataFrame([{"ts": p.ts, "price": p.price} for p in points])

    async def close(self) -> None:
        await self.gamma.close()
        await self.clob.close()
