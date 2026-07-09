"""Clock abstraction: the single mechanism that gives backtest/live parity.

Strategies never call ``datetime.now()`` — they ask the injected Clock.
In live/paper mode the clock is real; in backtests it is driven by the
event stream, so identical strategy code runs in both environments.
"""
from __future__ import annotations

import abc
import asyncio
from datetime import datetime, timezone


class Clock(abc.ABC):
    @abc.abstractmethod
    def now(self) -> datetime: ...

    @abc.abstractmethod
    async def sleep(self, seconds: float) -> None: ...


class WallClock(Clock):
    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


class SimClock(Clock):
    """Event-driven clock advanced by the backtest engine."""

    def __init__(self, start: datetime) -> None:
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        self._now = start

    def now(self) -> datetime:
        return self._now

    def advance_to(self, ts: datetime) -> None:
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts > self._now:
            self._now = ts

    async def sleep(self, seconds: float) -> None:
        # Simulated time: sleeping is a no-op; the event stream drives time.
        return None
