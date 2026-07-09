"""Complement arbitrage: in a binary market, best_ask(YES) + best_ask(NO) < 1
is a risk-free structural arb (buy both, guaranteed $1 at resolution).

These windows are rare and shallow but genuinely riskless (up to fees and
resolution risk); the strategy's real value is as a market-quality monitor
and a sanity check that the pipeline sees true executable prices.
"""
from __future__ import annotations

from quantbot.core.types import OrderBook, Side, Signal
from quantbot.strategies.base import MarketView, Strategy


class ComplementArbitrage(Strategy):
    name = "complement_arb"

    def __init__(self, min_edge: float = 0.01) -> None:
        super().__init__(min_edge=min_edge)
        self.min_edge = min_edge

    def on_view(self, view: MarketView) -> list[Signal]:
        yes_book = view.book
        no_book: OrderBook | None = view.extra.get("no_book")
        if yes_book is None or no_book is None:
            return []
        ya, na = yes_book.best_ask, no_book.best_ask
        if ya is None or na is None:
            return []
        total = ya.price + na.price
        edge = 1.0 - total
        if edge < self.min_edge:
            return []
        size = min(ya.size, na.size)
        return [
            Signal(
                strategy=self.name,
                token_id=view.market.yes_token_id,
                condition_id=view.market.condition_id,
                side=Side.BUY,
                market_price=ya.price,
                edge=edge,
                confidence=1.0,
                ts=view.now,
                ttl_seconds=10.0,
                metadata={"pair_price": na.price, "pair_token": view.market.no_token_id,
                          "max_size": size, "arb_total": total},
            ),
            Signal(
                strategy=self.name,
                token_id=view.market.no_token_id,
                condition_id=view.market.condition_id,
                side=Side.BUY,
                market_price=na.price,
                edge=edge,
                confidence=1.0,
                ts=view.now,
                ttl_seconds=10.0,
                metadata={"pair_price": ya.price, "pair_token": view.market.yes_token_id,
                          "max_size": size, "arb_total": total},
            ),
        ]
