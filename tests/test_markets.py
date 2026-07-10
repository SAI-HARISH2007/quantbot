"""Adapter-layer contract tests (no network)."""
import pytest

from quantbot.markets.base import AdapterRegistry, Instrument, PayoffType


def test_registry_has_both_adapters():
    # importing the modules registers them
    import quantbot.markets.crypto_spot  # noqa: F401
    import quantbot.markets.polymarket  # noqa: F401

    assert "polymarket" in AdapterRegistry.available()
    assert "binance_spot" in AdapterRegistry.available()


def test_registry_unknown_name_raises():
    with pytest.raises(KeyError, match="unknown market adapter"):
        AdapterRegistry.create("nasdaq_totally_real")


def test_instrument_defaults():
    i = Instrument(instrument_id="x", venue="v", symbol="X")
    assert i.payoff == PayoffType.LINEAR
    assert i.price_bounds[0] == 0.0


def test_polymarket_instrument_mapping():
    """Adapter maps a MarketInfo into two binary instruments (YES/NO)."""
    import asyncio
    from unittest.mock import AsyncMock

    from quantbot.core.types import MarketInfo
    from quantbot.markets.polymarket import PolymarketAdapter

    a = PolymarketAdapter()
    a.gamma.list_markets = AsyncMock(return_value=[MarketInfo(
        condition_id="c1", question="Will BTC be above $65,000?", slug="btc-65k",
        yes_token_id="y1", no_token_id="n1", liquidity=5000.0,
    )])
    instruments = asyncio.run(a.list_instruments())
    assert len(instruments) == 2
    assert all(i.payoff == PayoffType.BINARY for i in instruments)
    assert all(i.price_bounds == (0.0, 1.0) for i in instruments)
    assert {i.meta["outcome"] for i in instruments} == {"YES", "NO"}
    asyncio.run(a.close())
