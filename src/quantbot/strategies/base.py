"""Strategy interface — plug-and-play, identical in backtest and live.

A strategy consumes a MarketView (current state of one market plus whatever
context is available) and emits zero or more Signals. It never touches
orders, sizing, or portfolios: that separation is what keeps strategies
comparable and independently testable.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import pandas as pd

from quantbot.core.types import MarketInfo, OrderBook, Signal
from quantbot.fairvalue.base import FairValueEstimate


@dataclass
class MarketView:
    now: datetime
    market: MarketInfo
    price: Optional[float] = None  # last traded / mid price of YES token
    book: Optional[OrderBook] = None
    history: Optional[pd.DataFrame] = None  # YES price history [ts, price]
    fair_value: Optional[FairValueEstimate] = None
    extra: dict = field(default_factory=dict)


class Strategy(abc.ABC):
    """Subclasses must be stateless across markets or key state by condition_id."""

    name: str = "base"

    def __init__(self, **params: object) -> None:
        self.params = params

    @abc.abstractmethod
    def on_view(self, view: MarketView) -> list[Signal]: ...

    def reset(self) -> None:
        """Clear any per-run state (called between backtest folds)."""
