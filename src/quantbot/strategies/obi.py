"""Order book imbalance (OBI).

Hypothesis from microstructure literature (Cont, Kukanov & Stoikov 2014):
signed depth imbalance at the top of the book predicts the direction of the
next mid-price move. Requires live/recorded book snapshots.
"""
from __future__ import annotations

from quantbot.core.types import Side, Signal
from quantbot.strategies.base import MarketView, Strategy


class OrderBookImbalance(Strategy):
    name = "obi"

    def __init__(
        self,
        depth: int = 5,
        entry_imbalance: float = 0.6,
        max_spread: float = 0.04,
    ) -> None:
        super().__init__(depth=depth, entry_imbalance=entry_imbalance, max_spread=max_spread)
        self.depth = depth
        self.entry_imbalance = entry_imbalance
        self.max_spread = max_spread

    def on_view(self, view: MarketView) -> list[Signal]:
        book = view.book
        if book is None or view.price is None:
            return []
        spread = book.spread
        imb = book.imbalance(self.depth)
        if spread is None or imb is None or spread > self.max_spread:
            return []
        if abs(imb) < self.entry_imbalance:
            return []
        side = Side.BUY if imb > 0 else Side.SELL
        return [
            Signal(
                strategy=self.name,
                token_id=view.market.yes_token_id,
                condition_id=view.market.condition_id,
                side=side,
                market_price=view.price,
                edge=abs(imb) * spread,  # expected capture scales with both
                confidence=abs(imb),
                ts=view.now,
                ttl_seconds=30.0,
                metadata={"imbalance": imb, "spread": spread},
            )
        ]
