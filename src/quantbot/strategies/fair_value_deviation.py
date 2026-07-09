"""Fair value deviation: trade when market price diverges from model fair value.

Hypothesis: short-horizon Polymarket prices overshoot around news/flow while
model-derived fair value (e.g. digital-option pricing off the underlying)
moves smoothly; the gap mean-reverts toward fair value.

Edge must exceed both the entry threshold AND the model's own uncertainty —
we only trade divergences the model is confident about.
"""
from __future__ import annotations

from quantbot.core.types import Side, Signal
from quantbot.strategies.base import MarketView, Strategy


class FairValueDeviation(Strategy):
    name = "fair_value_deviation"

    def __init__(
        self,
        entry_threshold: float = 0.05,
        uncertainty_mult: float = 1.0,
        max_spread: float = 0.06,
    ) -> None:
        super().__init__(
            entry_threshold=entry_threshold,
            uncertainty_mult=uncertainty_mult,
            max_spread=max_spread,
        )
        self.entry_threshold = entry_threshold
        self.uncertainty_mult = uncertainty_mult
        self.max_spread = max_spread

    def on_view(self, view: MarketView) -> list[Signal]:
        fv = view.fair_value
        if fv is None or view.price is None:
            return []
        if view.book is not None and (s := view.book.spread) is not None and s > self.max_spread:
            return []
        edge = fv.prob - view.price
        required = max(self.entry_threshold, self.uncertainty_mult * fv.std)
        if abs(edge) < required:
            return []
        side = Side.BUY if edge > 0 else Side.SELL
        confidence = min(abs(edge) / (fv.std + 1e-6) / 3.0, 1.0)
        return [
            Signal(
                strategy=self.name,
                token_id=view.market.yes_token_id,
                condition_id=view.market.condition_id,
                side=side,
                fair_value=fv.prob,
                market_price=view.price,
                edge=abs(edge),
                confidence=confidence,
                ts=view.now,
                metadata={"fv_model": fv.model, "fv_std": fv.std},
            )
        ]
