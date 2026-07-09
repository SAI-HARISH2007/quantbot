"""Pre-trade risk checks and the drawdown kill switch."""
from __future__ import annotations

import logging
from dataclasses import dataclass

from quantbot.config import RiskConfig
from quantbot.core.types import Order, Side

logger = logging.getLogger(__name__)


@dataclass
class RiskState:
    equity: float
    peak_equity: float
    exposure: float  # total absolute notional at risk
    per_market_notional: dict[str, float]


class RiskManager:
    def __init__(self, cfg: RiskConfig):
        self.cfg = cfg
        self.halted = False

    def update_equity(self, equity: float, peak_equity: float) -> None:
        if peak_equity > 0:
            dd = 1.0 - equity / peak_equity
            if dd >= self.cfg.max_drawdown_pct and not self.halted:
                self.halted = True
                logger.error(
                    "KILL SWITCH: drawdown %.1f%% >= limit %.1f%% — trading halted",
                    dd * 100, self.cfg.max_drawdown_pct * 100,
                )

    def check_order(self, order: Order, state: RiskState) -> tuple[bool, str]:
        if self.halted:
            return False, "kill_switch_active"
        if order.size <= 0 or not (0.0 < order.price < 1.0):
            return False, "invalid_order"
        notional = order.size * (order.price if order.side == Side.BUY else 1 - order.price)
        if notional < self.cfg.min_order_notional:
            return False, "below_min_notional"
        # Tolerance: sizing computes notional at exactly the cap; float
        # round-trip (notional/price*price) must not trip a strict compare.
        eps = 1e-6
        market_now = state.per_market_notional.get(order.condition_id, 0.0)
        if market_now + notional > self.cfg.max_position_per_market * (1 + eps) + eps:
            return False, "per_market_limit"
        if state.exposure + notional > self.cfg.max_total_exposure * (1 + eps) + eps:
            return False, "total_exposure_limit"
        return True, "ok"
