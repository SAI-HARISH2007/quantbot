"""Execution-mode governor tests: live gating, risk scaling, shadow mode."""
import pytest

from quantbot.config import RiskConfig
from quantbot.execution.modes import (
    CONFIRM_ENV, CONFIRM_PHRASE, ExecutionMode, PROFILES,
    ShadowBroker, require_live_confirmation, scaled_risk,
)


def test_mode_properties():
    assert ExecutionMode.LIVE_NORMAL.is_live
    assert not ExecutionMode.SHADOW.is_live
    assert ExecutionMode.PAPER.trades
    assert not ExecutionMode.RESEARCH.trades


def test_risk_scaling_only_reduces():
    cfg = RiskConfig()
    for mode, profile in PROFILES.items():
        s = scaled_risk(cfg, mode)
        assert s.max_total_exposure <= cfg.max_total_exposure
        assert s.max_position_per_market <= cfg.max_position_per_market
        assert s.max_kelly_stake_pct <= cfg.max_kelly_stake_pct
        # the kill switch is NEVER loosened by any mode
        assert s.max_drawdown_pct == cfg.max_drawdown_pct
        assert profile.risk_scale <= 1.0
    cons = scaled_risk(cfg, ExecutionMode.LIVE_CONSERVATIVE)
    assert cons.max_total_exposure == cfg.max_total_exposure * 0.25


def test_live_requires_env_phrase(monkeypatch):
    monkeypatch.delenv(CONFIRM_ENV, raising=False)
    with pytest.raises(PermissionError, match="acknowledgement phrase"):
        require_live_confirmation(ExecutionMode.LIVE_CONSERVATIVE, interactive_ok=True)
    monkeypatch.setenv(CONFIRM_ENV, "yes please")  # wrong phrase
    with pytest.raises(PermissionError):
        require_live_confirmation(ExecutionMode.LIVE_NORMAL, interactive_ok=True)
    monkeypatch.setenv(CONFIRM_ENV, CONFIRM_PHRASE)
    with pytest.raises(PermissionError, match="interactive"):
        require_live_confirmation(ExecutionMode.LIVE_NORMAL, interactive_ok=False)
    # all gates satisfied -> no raise
    require_live_confirmation(ExecutionMode.LIVE_NORMAL, interactive_ok=True)
    # non-live modes never require anything
    monkeypatch.delenv(CONFIRM_ENV)
    require_live_confirmation(ExecutionMode.PAPER, interactive_ok=False)


async def test_shadow_broker_never_fills():
    assert await ShadowBroker().submit(object()) is None
