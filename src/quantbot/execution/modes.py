"""Execution modes and the trading governor.

One pipeline, many modes. The engine's analysis path (data → models →
signals → sizing → risk) is identical in every mode; the mode selects the
execution adapter and scales risk limits DOWN (never up):

  disabled          no engine at all
  research          analysis only; signals are never sized or risk-checked
  backtesting       historical simulation (BacktestEngine)
  paper             fills simulated against real live books
  shadow            full pipeline incl. risk verdicts, but NO orders at all —
                    the final rehearsal before live; decisions record what
                    WOULD have been done
  live_conservative real orders at 25% of configured risk limits
  live_normal       real orders at 50% of configured limits
  live_aggressive   real orders at 100% of configured limits — never more;
                    "aggressive" means your configured ceiling, not beyond it

Live activation is triple-gated: (1) env QUANTBOT_LIVE_CONFIRM must equal
the exact acknowledgement phrase, (2) an interactive typed confirmation at
startup, (3) the LiveBroker's own credential/install gates. The AI
assistant layer can explain decisions but has no code path to change mode.
"""
from __future__ import annotations

import enum
import os
from dataclasses import dataclass

from quantbot.config import RiskConfig

CONFIRM_ENV = "QUANTBOT_LIVE_CONFIRM"
CONFIRM_PHRASE = "I-UNDERSTAND-LIVE-TRADING-RISKS-REAL-MONEY-CAN-BE-LOST"


class ExecutionMode(str, enum.Enum):
    DISABLED = "disabled"
    RESEARCH = "research"
    BACKTESTING = "backtesting"
    PAPER = "paper"
    SHADOW = "shadow"
    LIVE_CONSERVATIVE = "live_conservative"
    LIVE_NORMAL = "live_normal"
    LIVE_AGGRESSIVE = "live_aggressive"

    @property
    def is_live(self) -> bool:
        return self.value.startswith("live_")

    @property
    def trades(self) -> bool:
        return self in (ExecutionMode.PAPER,) or self.is_live


@dataclass(frozen=True)
class ModeProfile:
    risk_scale: float  # multiplies exposure/stake limits; always <= 1.0
    description: str


PROFILES: dict[ExecutionMode, ModeProfile] = {
    ExecutionMode.PAPER: ModeProfile(1.0, "simulated fills on real books"),
    ExecutionMode.SHADOW: ModeProfile(1.0, "full pipeline, zero orders"),
    ExecutionMode.LIVE_CONSERVATIVE: ModeProfile(0.25, "real orders, 25% of limits"),
    ExecutionMode.LIVE_NORMAL: ModeProfile(0.50, "real orders, 50% of limits"),
    ExecutionMode.LIVE_AGGRESSIVE: ModeProfile(1.00, "real orders, 100% of limits"),
}


def scaled_risk(cfg: RiskConfig, mode: ExecutionMode) -> RiskConfig:
    """Return a risk config scaled for the mode. Scaling only ever reduces
    limits; the kill-switch threshold is never loosened."""
    scale = PROFILES.get(mode, ModeProfile(1.0, "")).risk_scale
    return cfg.model_copy(update={
        "max_position_per_market": cfg.max_position_per_market * scale,
        "max_total_exposure": cfg.max_total_exposure * scale,
        "max_kelly_stake_pct": cfg.max_kelly_stake_pct * scale,
    })


def require_live_confirmation(mode: ExecutionMode, interactive_ok: bool) -> None:
    """Raise unless every live gate is satisfied. Called at engine startup —
    there is deliberately no way to flip to live at runtime."""
    if not mode.is_live:
        return
    if os.environ.get(CONFIRM_ENV) != CONFIRM_PHRASE:
        raise PermissionError(
            f"Live mode '{mode.value}' requires the environment variable "
            f"{CONFIRM_ENV} to be set to the exact acknowledgement phrase "
            f"(see HOW_TO_USE_QUANTBOT.md §9/§11). Refusing to start."
        )
    if not interactive_ok:
        raise PermissionError(
            "Live mode requires interactive typed confirmation at startup; "
            "it cannot be enabled from a non-interactive environment."
        )


class ShadowBroker:
    """Executes nothing. The runner records the full decision (including the
    risk verdict and the order that WOULD have been sent); submit() simply
    declines, so outcome becomes 'shadow' — a perfect dress rehearsal."""

    async def submit(self, order: object) -> None:  # noqa: ARG002
        return None
