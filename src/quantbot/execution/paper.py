"""Paper broker: fills orders against real (or recorded) order books.

Fill logic is deliberately conservative:
- BUY walks the ask side level by level up to the limit price.
- SELL of held inventory walks the bid side.
- An `extra_slippage` haircut and configurable fees are applied on top.
- No fills beyond visible liquidity — if the book is empty we do not trade.

Conservatism here is a design principle: paper results must lower-bound,
not flatter, live performance.
"""
from __future__ import annotations

import logging
from typing import Awaitable, Callable, Optional

from quantbot.config import CostConfig
from quantbot.core.types import Fill, Order, OrderBook, Side

logger = logging.getLogger(__name__)

BookFetcher = Callable[[str], Awaitable[Optional[OrderBook]]]


def fill_against_book(order: Order, book: OrderBook, costs: CostConfig) -> Optional[Fill]:
    """Walk the book; return an aggregate fill or None."""
    levels = book.asks if order.side == Side.BUY else book.bids
    remaining = order.size
    filled = 0.0
    notional = 0.0
    for lvl in levels:
        if order.side == Side.BUY and lvl.price > order.price:
            break
        if order.side == Side.SELL and lvl.price < order.price:
            break
        take = min(remaining, lvl.size)
        filled += take
        notional += take * lvl.price
        remaining -= take
        if remaining <= 1e-9:
            break
    if filled <= 0:
        return None
    avg_price = notional / filled
    # Slippage haircut always moves against us
    if order.side == Side.BUY:
        avg_price = min(avg_price * (1 + costs.extra_slippage), 0.999)
    else:
        avg_price = max(avg_price * (1 - costs.extra_slippage), 0.001)
    fee = notional * costs.taker_fee_bps / 10_000 + costs.per_order_cost
    return Fill(
        order_id=order.order_id,
        token_id=order.token_id,
        condition_id=order.condition_id,
        side=order.side,
        price=avg_price,
        size=filled,
        fee=fee,
        ts=order.ts,
        strategy=order.strategy,
    )


class PaperBroker:
    """Async broker for live paper trading: fetches the current real book
    for each order and fills against it."""

    def __init__(self, fetch_book: BookFetcher, costs: CostConfig):
        self._fetch_book = fetch_book
        self._costs = costs

    async def submit(self, order: Order) -> Optional[Fill]:
        book = await self._fetch_book(order.token_id)
        if book is None:
            logger.warning("no book for %s; order dropped", order.token_id[:16])
            return None
        fill = fill_against_book(order, book, self._costs)
        if fill is None:
            logger.info("order %s unfilled (limit %.3f)", order.order_id, order.price)
        return fill
