"""Momentum / drift continuation.

Hypothesis (opposite of mean reversion — the data decides which wins where):
prediction market prices underreact to information, so recent drift
continues. Classic finding in prediction-market literature is *favourite–
longshot bias* plus underreaction; this strategy tests the underreaction leg.
"""
from __future__ import annotations

import numpy as np

from quantbot.core.types import Side, Signal
from quantbot.strategies.base import MarketView, Strategy


class Momentum(Strategy):
    name = "momentum"

    def __init__(
        self,
        lookback: int = 30,
        entry_move: float = 0.04,
        vol_scale: float = 1.5,
        min_history: int = 40,
        min_price: float = 0.10,
        max_price: float = 0.90,
    ) -> None:
        super().__init__(
            lookback=lookback, entry_move=entry_move, vol_scale=vol_scale,
            min_price=min_price, max_price=max_price,
        )
        self.lookback = lookback
        self.entry_move = entry_move
        self.vol_scale = vol_scale
        self.min_history = min_history
        self.min_price = min_price
        self.max_price = max_price

    def on_view(self, view: MarketView) -> list[Signal]:
        hist = view.history
        if hist is None or len(hist) < self.min_history or view.price is None:
            return []
        if not (self.min_price <= view.price <= self.max_price):
            return []
        p = hist["price"].astype(float)
        move = float(p.iloc[-1] - p.iloc[-self.lookback])
        vol = float(p.diff().rolling(self.lookback).std().iloc[-1]) * np.sqrt(self.lookback)
        if not np.isfinite(vol) or vol <= 1e-6:
            return []
        # Require the move to be large both absolutely and relative to noise
        if abs(move) < self.entry_move or abs(move) < self.vol_scale * vol:
            return []
        side = Side.BUY if move > 0 else Side.SELL
        return [
            Signal(
                strategy=self.name,
                token_id=view.market.yes_token_id,
                condition_id=view.market.condition_id,
                side=side,
                market_price=view.price,
                edge=min(abs(move) / 2, 0.1),
                confidence=min(abs(move) / (self.vol_scale * vol) / 2, 1.0),
                ts=view.now,
                metadata={"move": move, "vol": vol},
            )
        ]
