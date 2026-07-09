"""Core domain types shared across every subsystem.

All prices are decimal probabilities in [0, 1] (Polymarket convention).
All timestamps are timezone-aware UTC datetimes.
"""
from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field, field_validator


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Side(str, enum.Enum):
    BUY = "BUY"
    SELL = "SELL"


class Outcome(str, enum.Enum):
    YES = "YES"
    NO = "NO"


class OrderType(str, enum.Enum):
    LIMIT = "LIMIT"
    MARKET = "MARKET"


class MarketInfo(BaseModel):
    """Static metadata for one Polymarket binary market."""

    condition_id: str
    question: str
    slug: str = ""
    yes_token_id: str
    no_token_id: str
    end_date: Optional[datetime] = None
    active: bool = True
    closed: bool = False
    min_tick: float = 0.001
    min_order_size: float = 5.0
    liquidity: float = 0.0
    volume: float = 0.0
    category: str = ""

    @field_validator("end_date")
    @classmethod
    def _tz(cls, v: Optional[datetime]) -> Optional[datetime]:
        if v is not None and v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v


class BookLevel(BaseModel):
    price: float
    size: float


class OrderBook(BaseModel):
    """Snapshot of one token's order book (prices in probability space)."""

    token_id: str
    ts: datetime
    bids: list[BookLevel] = Field(default_factory=list)  # sorted desc by price
    asks: list[BookLevel] = Field(default_factory=list)  # sorted asc by price

    @property
    def best_bid(self) -> Optional[BookLevel]:
        return self.bids[0] if self.bids else None

    @property
    def best_ask(self) -> Optional[BookLevel]:
        return self.asks[0] if self.asks else None

    @property
    def mid(self) -> Optional[float]:
        if self.bids and self.asks:
            return (self.bids[0].price + self.asks[0].price) / 2.0
        return None

    @property
    def spread(self) -> Optional[float]:
        if self.bids and self.asks:
            return self.asks[0].price - self.bids[0].price
        return None

    def imbalance(self, depth: int = 5) -> Optional[float]:
        """Order book imbalance in [-1, 1]. Positive = bid pressure."""
        b = sum(l.size for l in self.bids[:depth])
        a = sum(l.size for l in self.asks[:depth])
        tot = a + b
        return (b - a) / tot if tot > 0 else None

    def microprice(self, depth: int = 1) -> Optional[float]:
        """Size-weighted mid: better short-horizon predictor than raw mid."""
        if not (self.bids and self.asks):
            return None
        bb, ba = self.bids[0], self.asks[0]
        tot = bb.size + ba.size
        if tot <= 0:
            return self.mid
        return (bb.price * ba.size + ba.price * bb.size) / tot


class Candle(BaseModel):
    """OHLCV bar for an external asset (e.g. BTCUSDT)."""

    symbol: str
    ts: datetime  # open time
    open: float
    high: float
    low: float
    close: float
    volume: float


class PricePoint(BaseModel):
    """One point from Polymarket's price history."""

    token_id: str
    ts: datetime
    price: float


class Signal(BaseModel):
    """A strategy's desired action, prior to risk checks and sizing."""

    strategy: str
    token_id: str
    condition_id: str
    side: Side
    fair_value: Optional[float] = None
    market_price: Optional[float] = None
    edge: float = 0.0  # expected value per share, in probability units
    confidence: float = 1.0  # [0,1] scaling applied by sizer
    ts: datetime = Field(default_factory=utcnow)
    # Must cover at least one bar of the data the strategy runs on; fast
    # microstructure signals (OBI, arb) override this with short TTLs.
    ttl_seconds: float = 3600.0
    metadata: dict = Field(default_factory=dict)


class Order(BaseModel):
    order_id: str
    token_id: str
    condition_id: str
    side: Side
    order_type: OrderType
    price: float  # limit price (for MARKET, the max acceptable)
    size: float  # number of shares
    strategy: str = ""
    ts: datetime = Field(default_factory=utcnow)


class Fill(BaseModel):
    order_id: str
    token_id: str
    condition_id: str
    side: Side
    price: float
    size: float
    fee: float = 0.0
    ts: datetime = Field(default_factory=utcnow)
    strategy: str = ""


class Position(BaseModel):
    token_id: str
    condition_id: str
    size: float = 0.0  # shares held (long only on a token; shorting YES == buying NO)
    avg_price: float = 0.0
    realized_pnl: float = 0.0

    def unrealized_pnl(self, mark: float) -> float:
        return (mark - self.avg_price) * self.size
