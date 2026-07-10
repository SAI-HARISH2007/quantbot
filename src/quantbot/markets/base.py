"""Market adapter layer: markets as plugins.

The core engine (strategies, risk, portfolio, backtesting, dashboard) is
being made venue-agnostic. Each venue implements MarketAdapter; the engine
only ever speaks this interface. Payoff semantics differ radically between
venues (a Polymarket YES share is a binary option settling at $0/$1; a spot
BTC position is linear; a perpetual future is linear with funding), so the
adapter — not the engine — owns instrument semantics, sizing constraints,
and settlement.

Roadmap (docs/ARCHITECTURE.md#multi-market): Polymarket (complete, wraps
the existing connectors), crypto spot (data complete, paper execution
next), then perps/forex/equities via the same contract.
"""
from __future__ import annotations

import abc
import enum
from dataclasses import dataclass, field
from datetime import datetime
from typing import AsyncIterator, Optional

import pandas as pd

from quantbot.core.types import OrderBook


class PayoffType(str, enum.Enum):
    BINARY = "binary"      # settles at 0 or 1 (prediction markets)
    LINEAR = "linear"      # spot assets
    LINEAR_FUNDING = "linear_funding"  # perpetual futures


@dataclass
class Instrument:
    """Venue-neutral tradable instrument."""

    instrument_id: str          # venue-scoped unique id (e.g. token id, symbol)
    venue: str                  # adapter name, e.g. "polymarket", "binance_spot"
    symbol: str                 # human-readable, e.g. "BTC-65K-JUL31-YES", "BTCUSDT"
    description: str = ""
    payoff: PayoffType = PayoffType.LINEAR
    quote_currency: str = "USD"
    tick_size: float = 0.01
    min_order_size: float = 0.0     # in quote currency
    price_bounds: tuple[float, float] = (0.0, float("inf"))  # (0,1) for binary
    expiry: Optional[datetime] = None
    meta: dict = field(default_factory=dict)


class MarketAdapter(abc.ABC):
    """Everything the engine needs from a venue. Implementations must be
    stateless across calls or manage their own connections."""

    name: str = "base"

    @abc.abstractmethod
    async def list_instruments(self, min_liquidity: float = 0.0) -> list[Instrument]:
        """Discover tradable instruments."""

    @abc.abstractmethod
    async def get_book(self, instrument_id: str) -> Optional[OrderBook]:
        """Current order book (best-effort; quote-only venues may synthesize
        a one-level book from bid/ask)."""

    @abc.abstractmethod
    async def get_history(
        self, instrument_id: str, start: datetime, end: datetime,
        bar_minutes: int = 10,
    ) -> pd.DataFrame:
        """Historical prices as [ts, price] (mid or close)."""

    async def stream_books(
        self, instrument_ids: list[str]
    ) -> AsyncIterator[OrderBook]:
        """Optional live stream; default raises so callers can fall back to
        polling get_book."""
        raise NotImplementedError(f"{self.name} has no streaming support")
        yield  # pragma: no cover  (makes this an async generator)

    @abc.abstractmethod
    async def close(self) -> None: ...


class AdapterRegistry:
    """Config name -> adapter factory. Venues register at import time."""

    _factories: dict[str, type[MarketAdapter]] = {}

    @classmethod
    def register(cls, adapter_cls: type[MarketAdapter]) -> type[MarketAdapter]:
        cls._factories[adapter_cls.name] = adapter_cls
        return adapter_cls

    @classmethod
    def create(cls, name: str, **kwargs: object) -> MarketAdapter:
        if name not in cls._factories:
            raise KeyError(
                f"unknown market adapter '{name}'; available: {sorted(cls._factories)}"
            )
        return cls._factories[name](**kwargs)  # type: ignore[call-arg]

    @classmethod
    def available(cls) -> list[str]:
        return sorted(cls._factories)
