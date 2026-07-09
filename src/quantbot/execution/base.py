"""Execution client protocol — paper, simulated, and live all satisfy this."""
from __future__ import annotations

from typing import Optional, Protocol

from quantbot.core.types import Fill, Order


class ExecutionClient(Protocol):
    async def submit(self, order: Order) -> Optional[Fill]:
        """Submit an order; return the fill (possibly partial) or None if unfilled."""
        ...
