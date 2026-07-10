"""Technical-analysis layer tests — no network, no trading access."""
import asyncio

import pytest

from quantbot.analysis.technical import (
    NullProvider, TechnicalSnapshot, TradingViewProvider, build_provider,
)


def test_disabled_returns_null_provider():
    p = build_provider(enabled=False)
    assert isinstance(p, NullProvider)
    assert asyncio.run(p.snapshot("BTCUSDT")) is None
    assert asyncio.run(p.scan("top_gainers")) == []


def test_unknown_provider_degrades_to_disabled():
    assert isinstance(build_provider(True, provider="bloomberg"), NullProvider)


def test_snapshot_to_dict_drops_raw():
    s = TechnicalSnapshot(symbol="BTCUSDT", timeframe="1h", rsi=55.0,
                          raw={"huge": "blob"})
    d = s.to_dict()
    assert d["rsi"] == 55.0 and "raw" not in d


def test_provider_has_no_trading_surface():
    """The analysis layer must be structurally incapable of trading."""
    import quantbot.analysis.technical as mod

    src = open(mod.__file__).read()
    for forbidden in ("Broker", "submit", "RiskManager", "Portfolio", "Order("):
        assert forbidden not in src, f"analysis layer references {forbidden}"


def test_scan_normalization(monkeypatch):
    p = TradingViewProvider()
    fake_rows = [
        {"symbol": "BINANCE:AAAUSDT", "changePercent": 5.0,
         "indicators": {"SMA20": 100.0, "BB_upper": 104.0, "BB_lower": 96.0,
                        "RSI": 71.0, "close": 101.0}},
        {"symbol": "BINANCE:BBBUSDT", "changePercent": -2.0,
         "indicators": {"SMA20": 50.0, "BB_upper": 50.5, "BB_lower": 49.5,
                        "RSI": 44.0, "close": 50.0}},
    ]
    import quantbot.analysis.technical as mod

    class FakeSS:
        @staticmethod
        def fetch_bollinger_analysis(*a):
            return fake_rows

        @staticmethod
        def fetch_trending_analysis(*a):
            return fake_rows

    monkeypatch.setitem(
        __import__("sys").modules,
        "tradingview_mcp.core.services.screener_service", FakeSS,
    )
    squeeze = asyncio.run(p.scan("bollinger_squeeze", timeframe="4h", limit=5))
    assert squeeze[0]["symbol"] == "BBBUSDT"  # tightest bands first (bbw 0.02)
    assert abs(squeeze[0]["bbw"] - 0.02) < 1e-9
    assert abs(squeeze[1]["bbw"] - 0.08) < 1e-9
    gainers = asyncio.run(p.scan("top_gainers", timeframe="4h", limit=5))
    assert gainers[0]["symbol"] == "AAAUSDT" and gainers[0]["change_pct"] == 5.0
    with pytest.raises(ValueError, match="unknown scan kind"):
        p._scan_sync("moon_phase", "4h", 5)
