from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from quantbot.config import AppConfig
from quantbot.core.types import BookLevel, MarketInfo, OrderBook


@pytest.fixture
def cfg() -> AppConfig:
    return AppConfig()


@pytest.fixture
def market() -> MarketInfo:
    return MarketInfo(
        condition_id="0xcond",
        question="Will Bitcoin be above $65,000 on July 31?",
        slug="btc-above-65k",
        yes_token_id="tok_yes",
        no_token_id="tok_no",
        end_date=datetime(2026, 7, 31, tzinfo=timezone.utc),
        liquidity=50_000.0,
    )


@pytest.fixture
def book() -> OrderBook:
    return OrderBook(
        token_id="tok_yes",
        ts=datetime(2026, 7, 1, tzinfo=timezone.utc),
        bids=[BookLevel(price=0.48, size=1000), BookLevel(price=0.47, size=2000)],
        asks=[BookLevel(price=0.52, size=800), BookLevel(price=0.53, size=1500)],
    )


def make_price_history(
    n: int = 200,
    start_price: float = 0.5,
    drift: float = 0.0,
    noise: float = 0.01,
    seed: int = 3,
) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    t0 = datetime(2026, 6, 1, tzinfo=timezone.utc)
    prices = np.clip(
        start_price + np.cumsum(rng.normal(drift, noise, n)), 0.02, 0.98
    )
    return pd.DataFrame(
        {"ts": [t0 + timedelta(minutes=10 * i) for i in range(n)], "price": prices}
    )


def make_candles(n: int = 500, start: float = 60_000.0, vol: float = 0.0005, seed: int = 5):
    rng = np.random.default_rng(seed)
    t0 = datetime(2026, 6, 1, tzinfo=timezone.utc)
    closes = start * np.exp(np.cumsum(rng.normal(0, vol, n)))
    opens = np.concatenate([[start], closes[:-1]])
    highs = np.maximum(opens, closes) * (1 + np.abs(rng.normal(0, vol / 2, n)))
    lows = np.minimum(opens, closes) * (1 - np.abs(rng.normal(0, vol / 2, n)))
    return pd.DataFrame(
        {
            "symbol": "BTCUSDT",
            "ts": [t0 + timedelta(minutes=i) for i in range(n)],
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": rng.uniform(1, 10, n),
        }
    )
