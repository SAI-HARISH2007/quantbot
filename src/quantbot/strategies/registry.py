"""Strategy registry: config name -> class, so YAML configs stay declarative."""
from __future__ import annotations

from typing import Type

from quantbot.config import StrategyConfig
from quantbot.strategies.base import Strategy
from quantbot.strategies.complement_arb import ComplementArbitrage
from quantbot.strategies.fair_value_deviation import FairValueDeviation
from quantbot.strategies.mean_reversion import MeanReversion
from quantbot.strategies.momentum import Momentum
from quantbot.strategies.obi import OrderBookImbalance

REGISTRY: dict[str, Type[Strategy]] = {
    s.name: s
    for s in (
        FairValueDeviation,
        MeanReversion,
        Momentum,
        OrderBookImbalance,
        ComplementArbitrage,
    )
}


def build_strategies(configs: list[StrategyConfig]) -> list[Strategy]:
    out: list[Strategy] = []
    for c in configs:
        if not c.enabled:
            continue
        if c.name not in REGISTRY:
            raise KeyError(f"unknown strategy '{c.name}'; available: {sorted(REGISTRY)}")
        out.append(REGISTRY[c.name](**c.params))
    return out
