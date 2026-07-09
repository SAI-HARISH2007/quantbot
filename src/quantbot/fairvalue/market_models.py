"""Market-internal fair value models (no external data needed).

MicropriceModel: the size-weighted mid is a less-noisy instantaneous value.
TimeWeightedModel: EWMA of recent trade prices resists quote flickering.
These act as the "anchor" family — deviations of the tradable price from an
anchor drive mean-reversion style strategies.
"""
from __future__ import annotations

from typing import Optional

import numpy as np

from quantbot.fairvalue.base import FairValueContext, FairValueEstimate, FairValueModel


class MicropriceModel(FairValueModel):
    name = "microprice"

    def __init__(self, max_spread: float = 0.10):
        self.max_spread = max_spread  # wider books carry no information

    def estimate(self, ctx: FairValueContext) -> Optional[FairValueEstimate]:
        book = ctx.book
        if book is None:
            return None
        mp = book.microprice()
        spread = book.spread
        if mp is None or spread is None or spread > self.max_spread:
            return None
        # Uncertainty scales with the spread — a 1c-wide book pins value tightly.
        return FairValueEstimate(
            model=self.name, prob=mp, std=max(spread / 2.0, 0.005),
            detail={"spread": spread, "imbalance": book.imbalance()},
        ).clamped()


class TimeWeightedModel(FairValueModel):
    name = "ewma_price"

    def __init__(self, halflife_bars: int = 12):
        self.halflife_bars = halflife_bars

    def estimate(self, ctx: FairValueContext) -> Optional[FairValueEstimate]:
        hist = ctx.pm_price_history
        if hist is None or len(hist) < 5:
            return None
        prices = hist["price"].astype(float)
        ewma = float(prices.ewm(halflife=self.halflife_bars).mean().iloc[-1])
        resid_std = float((prices - prices.ewm(halflife=self.halflife_bars).mean()).std())
        return FairValueEstimate(
            model=self.name,
            prob=ewma,
            std=max(resid_std if np.isfinite(resid_std) else 0.05, 0.01),
            detail={"n_points": len(hist)},
        ).clamped()
