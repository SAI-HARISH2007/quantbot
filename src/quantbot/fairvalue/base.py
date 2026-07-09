"""Fair value model interface.

A FairValueModel maps (market, current context) -> probability estimate with
an uncertainty. Every model is a hypothesis: models are compared empirically
by the research harness (Brier score / log loss vs resolved outcomes) and
only survivors are enabled in production configs.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import pandas as pd

from quantbot.core.types import MarketInfo, OrderBook


@dataclass
class FairValueContext:
    """Everything a model may draw on. Fields are optional; models declare
    what they need and return None when inputs are missing."""

    now: datetime
    market: MarketInfo
    book: Optional[OrderBook] = None
    pm_price_history: Optional[pd.DataFrame] = None  # [ts, price] for YES token
    spot: Optional[float] = None  # underlying spot (crypto markets)
    candles: Optional[pd.DataFrame] = None  # underlying OHLCV
    # Preferred over `candles`/`spot`: models that know their underlying
    # symbol pick the right series here (BTC market must not price off ETH).
    candles_by_symbol: dict = field(default_factory=dict)
    extra: dict = field(default_factory=dict)


@dataclass
class FairValueEstimate:
    model: str
    prob: float  # P(YES) in [0,1]
    std: float = 0.10  # model's own uncertainty about prob
    detail: dict = field(default_factory=dict)

    def clamped(self, lo: float = 0.001, hi: float = 0.999) -> "FairValueEstimate":
        self.prob = min(max(self.prob, lo), hi)
        return self


class FairValueModel(abc.ABC):
    name: str = "base"

    @abc.abstractmethod
    def estimate(self, ctx: FairValueContext) -> Optional[FairValueEstimate]:
        """Return None when this model cannot price the given market."""
