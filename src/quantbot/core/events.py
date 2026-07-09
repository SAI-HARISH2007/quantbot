"""Minimal typed pub/sub event bus used to decouple subsystems."""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any, Awaitable, Callable, Type, TypeVar

logger = logging.getLogger(__name__)

E = TypeVar("E")
Handler = Callable[[Any], Awaitable[None]]


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[type, list[Handler]] = defaultdict(list)

    def subscribe(self, event_type: Type[E], handler: Handler) -> None:
        self._subs[event_type].append(handler)

    async def publish(self, event: Any) -> None:
        handlers = self._subs.get(type(event), [])
        if not handlers:
            return
        results = await asyncio.gather(
            *(h(event) for h in handlers), return_exceptions=True
        )
        for r in results:
            if isinstance(r, Exception):
                logger.exception("event handler failed", exc_info=r)
