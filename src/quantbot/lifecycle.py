"""Strategy lifecycle: the promotion pipeline made visible.

Stages: experimental → research → backtesting → paper → candidate → live.
Stage evaluation is automatic and evidence-based, but promotion TO live is
never automatic — `live` requires an explicit entry in the config's
`live_strategies` list (which does not exist by default) AND the LiveBroker
gates. The dashboard shows each strategy's stage and exactly which criteria
are met or missing.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from quantbot.data.storage import Store
from quantbot.experiments.tracker import ExperimentTracker

MIN_OOS_TRADES = 100
MIN_PAPER_TRADES = 100
MIN_PAPER_DAYS = 14.0


class Stage(str, enum.Enum):
    EXPERIMENTAL = "experimental"
    RESEARCH = "research"
    BACKTESTING = "backtesting"
    PAPER = "paper"
    CANDIDATE = "candidate"
    LIVE = "live"


@dataclass
class Criterion:
    name: str
    met: bool
    detail: str


@dataclass
class StrategyStatus:
    name: str
    stage: Stage
    criteria: list[Criterion] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "stage": self.stage.value,
            "criteria": [c.__dict__ for c in self.criteria],
        }


def evaluate_strategy(
    name: str,
    store: Store,
    tracker: ExperimentTracker,
    enabled_in_paper: bool,
    now: Optional[datetime] = None,
) -> StrategyStatus:
    now = now or datetime.now(timezone.utc)
    runs = tracker.list_runs(name)
    backtests = [r for r in runs if r["kind"] == "backtest"]
    wfs = [r for r in runs if r["kind"] == "walkforward"]

    best_wf = None
    for r in wfs:
        m = r.get("metrics", {})
        if best_wf is None or (m.get("sharpe") or 0) > (
            best_wf.get("metrics", {}).get("sharpe") or 0
        ):
            best_wf = r
    wf_m = (best_wf or {}).get("metrics", {})
    ci_low = wf_m.get("sharpe_ci_low")
    wf_significant = ci_low is not None and ci_low > 0
    wf_trades_ok = (wf_m.get("n_trades") or 0) >= MIN_OOS_TRADES

    # paper evidence: fills across all paper runs for this strategy
    fills = store.load_fills_by_strategy(name)
    paper_trades = len(fills)
    paper_days = 0.0
    if paper_trades:
        span = fills["ts"].max() - fills["ts"].min()
        paper_days = span.total_seconds() / 86400

    criteria = [
        Criterion("has_backtests", bool(backtests), f"{len(backtests)} backtest run(s)"),
        Criterion("has_walkforward", bool(wfs), f"{len(wfs)} walk-forward run(s)"),
        Criterion(
            "oos_sharpe_significant", bool(wf_significant),
            f"best OOS sharpe CI low = {ci_low}" if ci_low is not None else "no CI yet",
        ),
        Criterion(
            "oos_trades", bool(wf_trades_ok),
            f"{wf_m.get('n_trades', 0)}/{MIN_OOS_TRADES} OOS trades",
        ),
        Criterion(
            "paper_trades", paper_trades >= MIN_PAPER_TRADES,
            f"{paper_trades}/{MIN_PAPER_TRADES} paper fills",
        ),
        Criterion(
            "paper_duration", paper_days >= MIN_PAPER_DAYS,
            f"{paper_days:.1f}/{MIN_PAPER_DAYS:.0f} days of paper history",
        ),
        Criterion(
            "manual_live_approval", False,
            "Live promotion is a manual, documented decision — never automatic.",
        ),
    ]

    if all(c.met for c in criteria[:6]):
        stage = Stage.CANDIDATE
    elif enabled_in_paper and paper_trades > 0:
        stage = Stage.PAPER
    elif enabled_in_paper:
        stage = Stage.PAPER
    elif wfs or backtests:
        stage = Stage.BACKTESTING
    else:
        stage = Stage.RESEARCH
    return StrategyStatus(name=name, stage=stage, criteria=criteria)


def evaluate_all(store: Store, tracker: ExperimentTracker, paper_enabled: list[str]) -> list[dict]:
    from quantbot.strategies.registry import REGISTRY

    return [
        evaluate_strategy(name, store, tracker, name in paper_enabled).to_dict()
        for name in sorted(REGISTRY)
    ]
