"""Technical market-context layer (provider-agnostic, ANALYSIS ONLY).

Hard boundary, by construction:
* Providers return data. They have no access to the broker, the risk
  engine, the portfolio, or order objects — nothing here can trade.
* Context is attached to decisions/views as *supplementary evidence*.
  Whether a strategy uses it is a research hypothesis (H8 in RESEARCH.md),
  never an assumption.
* The whole layer is disabled by setting ``analysis.enabled: false`` (or if
  the provider package is missing) — the engine runs identically without it.

The default provider wraps the tradingview-mcp server's underlying services
IN-PROCESS (same functions its MCP tools call) — more reliable and testable
than speaking JSON-RPC to a subprocess. The same server can additionally be
registered as a real MCP server for the AI copilot (see .mcp.json and
docs/INTEGRATIONS.md).
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Optional, Protocol

logger = logging.getLogger(__name__)


@dataclass
class TechnicalSnapshot:
    """One symbol's technical state on one timeframe."""

    symbol: str
    timeframe: str
    recommendation: str = ""        # e.g. STRONG_BUY .. STRONG_SELL
    buy_signals: int = 0
    sell_signals: int = 0
    rsi: Optional[float] = None
    macd_hist: Optional[float] = None
    bb_width: Optional[float] = None      # Bollinger width — squeeze detector
    adx: Optional[float] = None           # trend strength
    change_pct: Optional[float] = None
    ts: float = 0.0
    raw: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        d.pop("raw", None)  # keep event payloads small; raw stays in logs
        return d


class TechnicalContextProvider(Protocol):
    """Swap implementations freely; QuantBot depends only on this shape."""

    name: str

    async def snapshot(self, symbol: str, timeframe: str = "1h") -> Optional[TechnicalSnapshot]: ...

    async def scan(self, kind: str, **params: object) -> list[dict]:
        """kind: 'bollinger_squeeze' | 'top_gainers' | 'top_losers' | 'strong_rated'"""
        ...


class NullProvider:
    """Used when analysis is disabled — every call returns nothing."""

    name = "disabled"

    async def snapshot(self, symbol: str, timeframe: str = "1h") -> None:
        return None

    async def scan(self, kind: str, **params: object) -> list[dict]:
        return []


class TradingViewProvider:
    """In-process wrapper over the tradingview-mcp server's service layer
    (tradingview-ta for per-symbol analysis, its screener services for
    scans). All calls are cached (TTL) and run in a worker thread since the
    underlying libraries are synchronous."""

    name = "tradingview"

    def __init__(self, exchange: str = "BINANCE", cache_ttl: float = 120.0):
        self.exchange = exchange
        self.cache_ttl = cache_ttl
        self._cache: dict[str, tuple[float, object]] = {}

    def _cached(self, key: str) -> Optional[object]:
        hit = self._cache.get(key)
        if hit and time.monotonic() - hit[0] < self.cache_ttl:
            return hit[1]
        return None

    def _put(self, key: str, value: object) -> None:
        self._cache[key] = (time.monotonic(), value)

    # ------------------------------------------------------------ snapshot
    def _snapshot_sync(self, symbol: str, timeframe: str) -> Optional[TechnicalSnapshot]:
        from tradingview_ta import Interval, TA_Handler

        tf_map = {"5m": Interval.INTERVAL_5_MINUTES, "15m": Interval.INTERVAL_15_MINUTES,
                  "1h": Interval.INTERVAL_1_HOUR, "4h": Interval.INTERVAL_4_HOURS,
                  "1d": Interval.INTERVAL_1_DAY}
        h = TA_Handler(symbol=symbol, exchange=self.exchange, screener="crypto",
                       interval=tf_map.get(timeframe, Interval.INTERVAL_1_HOUR))
        a = h.get_analysis()
        ind = a.indicators
        sma20 = ind.get("SMA20") or 0
        bbw = ((ind.get("BB.upper", 0) - ind.get("BB.lower", 0)) / sma20) if sma20 else None
        return TechnicalSnapshot(
            symbol=symbol, timeframe=timeframe,
            recommendation=a.summary.get("RECOMMENDATION", ""),
            buy_signals=a.summary.get("BUY", 0), sell_signals=a.summary.get("SELL", 0),
            rsi=ind.get("RSI"), macd_hist=ind.get("MACD.macd", 0) - ind.get("MACD.signal", 0),
            bb_width=bbw, adx=ind.get("ADX"), change_pct=ind.get("change"),
            ts=time.time(), raw=dict(ind),
        )

    async def snapshot(self, symbol: str, timeframe: str = "1h") -> Optional[TechnicalSnapshot]:
        key = f"snap:{symbol}:{timeframe}"
        if (hit := self._cached(key)) is not None:
            return hit  # type: ignore[return-value]
        try:
            snap = await asyncio.to_thread(self._snapshot_sync, symbol, timeframe)
        except Exception as e:  # noqa: BLE001 — analysis must never break trading
            logger.warning("technical snapshot failed for %s: %s", symbol, e)
            return None
        self._put(key, snap)
        return snap

    # ------------------------------------------------------------ scans
    def _scan_sync(self, kind: str, timeframe: str, limit: int) -> list[dict]:
        from tradingview_mcp.core.services.screener_service import (
            fetch_bollinger_analysis, fetch_trending_analysis,
        )

        def norm(r: dict) -> dict:
            """Normalize provider rows to a stable schema QuantBot owns —
            downstream code never sees provider-specific field names."""
            ind = r.get("indicators") or {}
            sma20 = ind.get("SMA20") or 0
            bbw = (
                (ind.get("BB_upper", 0) - ind.get("BB_lower", 0)) / sma20
                if sma20 else None
            )
            return {
                "symbol": str(r.get("symbol", "")).split(":")[-1],
                "change_pct": r.get("changePercent"),
                "rsi": ind.get("RSI"),
                "bbw": bbw,
                "close": ind.get("close"),
            }

        if kind == "bollinger_squeeze":
            rows = [norm(r) for r in (fetch_bollinger_analysis(self.exchange, timeframe) or [])
                    if isinstance(r, dict)]
            rows = [r for r in rows if r["bbw"] is not None]
            rows.sort(key=lambda r: r["bbw"])
            return rows[:limit]
        if kind in ("top_gainers", "top_losers", "strong_rated"):
            rows = [norm(r) for r in (fetch_trending_analysis(self.exchange, timeframe) or [])
                    if isinstance(r, dict)]
            if kind == "top_gainers":
                rows.sort(key=lambda r: -(r["change_pct"] or 0))
            elif kind == "top_losers":
                rows.sort(key=lambda r: (r["change_pct"] or 0))
            else:  # strong_rated: extreme RSI as momentum proxy
                rows.sort(key=lambda r: -abs((r["rsi"] or 50) - 50))
            return rows[:limit]
        raise ValueError(f"unknown scan kind '{kind}'")

    async def scan(self, kind: str, timeframe: str = "4h", limit: int = 10,
                   **_params: object) -> list[dict]:
        key = f"scan:{kind}:{timeframe}:{limit}"
        if (hit := self._cached(key)) is not None:
            return hit  # type: ignore[return-value]
        try:
            rows = await asyncio.to_thread(self._scan_sync, kind, timeframe, limit)
        except Exception as e:  # noqa: BLE001
            logger.warning("technical scan '%s' failed: %s", kind, e)
            return []
        self._put(key, rows)
        return rows


def build_provider(enabled: bool, provider: str = "tradingview",
                   exchange: str = "BINANCE", cache_ttl: float = 120.0):
    """Factory from config. Unknown/broken providers degrade to disabled."""
    if not enabled:
        return NullProvider()
    if provider == "tradingview":
        try:
            import tradingview_ta  # noqa: F401
        except ImportError:
            logger.warning("analysis enabled but tradingview packages missing — "
                           "run: pip install -e <path-to-tradingview-mcp>; disabling")
            return NullProvider()
        return TradingViewProvider(exchange=exchange, cache_ttl=cache_ttl)
    logger.warning("unknown analysis provider '%s' — disabling", provider)
    return NullProvider()
