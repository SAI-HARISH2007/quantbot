"""Inverse-variance ensemble of fair value models.

Bayesian-flavoured combination: each model contributes its estimate weighted
by 1/std². Disagreement between models widens the ensemble std — a natural
"don't trade what you don't understand" signal consumed by the risk layer.
"""
from __future__ import annotations

from typing import Optional, Sequence

from quantbot.fairvalue.base import FairValueContext, FairValueEstimate, FairValueModel


class EnsembleFairValue(FairValueModel):
    name = "ensemble"

    def __init__(self, models: Sequence[FairValueModel], min_models: int = 1):
        self.models = list(models)
        self.min_models = min_models

    def estimate(self, ctx: FairValueContext) -> Optional[FairValueEstimate]:
        parts: list[FairValueEstimate] = []
        for m in self.models:
            try:
                est = m.estimate(ctx)
            except Exception:  # noqa: BLE001 — one broken model must not kill pricing
                continue
            if est is not None:
                parts.append(est)
        if len(parts) < self.min_models:
            return None
        weights = [1.0 / max(p.std, 1e-4) ** 2 for p in parts]
        wsum = sum(weights)
        prob = sum(w * p.prob for w, p in zip(weights, parts)) / wsum
        # Combined variance + disagreement penalty
        var_within = 1.0 / wsum
        var_between = sum(w * (p.prob - prob) ** 2 for w, p in zip(weights, parts)) / wsum
        std = (var_within + var_between) ** 0.5
        return FairValueEstimate(
            model=self.name,
            prob=prob,
            std=max(std, 0.005),
            detail={p.model: {"prob": p.prob, "std": p.std} for p in parts},
        ).clamped()
