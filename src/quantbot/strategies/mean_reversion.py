"""Mean reversion on the market's own price series.

Hypothesis: sharp single-market moves without corresponding moves in fair
value revert. Entry on z-score extremes of deviation from EWMA; exit is
implicit (opposite signal or risk layer).
"""
from __future__ import annotations

import numpy as np

from quantbot.core.types import Side, Signal
from quantbot.strategies.base import MarketView, Strategy


class MeanReversion(Strategy):
    name = "mean_reversion"

    def __init__(
        self,
        lookback: int = 20,
        entry_z: float = 2.0,
        halflife: int = 12,
        min_history: int = 30,
        min_price: float = 0.10,
        max_price: float = 0.90,
    ) -> None:
        super().__init__(
            lookback=lookback, entry_z=entry_z, halflife=halflife,
            min_price=min_price, max_price=max_price,
        )
        self.lookback = lookback
        self.entry_z = entry_z
        self.halflife = halflife
        self.min_history = min_history
        # Longshot guard: at extreme prices, "dips" are usually information,
        # not noise (favourite-longshot tail), and relative costs explode.
        self.min_price = min_price
        self.max_price = max_price

    def on_view(self, view: MarketView) -> list[Signal]:
        hist = view.history
        if hist is None or len(hist) < self.min_history or view.price is None:
            return []
        if not (self.min_price <= view.price <= self.max_price):
            return []
        p = hist["price"].astype(float)
        anchor = p.ewm(halflife=self.halflife).mean()
        dev = p - anchor
        sd = float(dev.rolling(self.lookback).std().iloc[-1])
        if not np.isfinite(sd) or sd <= 1e-6:
            return []
        z = float(dev.iloc[-1]) / sd
        if abs(z) < self.entry_z:
            return []
        # Extreme high -> expect fall -> SELL YES; extreme low -> BUY YES
        side = Side.SELL if z > 0 else Side.BUY
        edge = min(abs(float(dev.iloc[-1])), 0.2)
        return [
            Signal(
                strategy=self.name,
                token_id=view.market.yes_token_id,
                condition_id=view.market.condition_id,
                side=side,
                market_price=view.price,
                edge=edge,
                confidence=min(abs(z) / (2 * self.entry_z), 1.0),
                ts=view.now,
                metadata={"z": z, "anchor": float(anchor.iloc[-1])},
            )
        ]
