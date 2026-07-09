"""Experiment tracking: every backtest/walk-forward run is recorded with its
config, code version, data window, and full metrics — reproducibility is a
hard requirement, not a nice-to-have."""
from __future__ import annotations

import json
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


def _git_rev() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


class ExperimentTracker:
    def __init__(self, root: Path = Path("experiments/runs")):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def log_run(
        self,
        kind: str,
        strategy: str,
        params: dict,
        metrics: dict,
        data_window: Optional[dict] = None,
        notes: str = "",
    ) -> str:
        run_id = f"{datetime.now(timezone.utc):%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:6]}"
        record: dict[str, Any] = {
            "run_id": run_id,
            "kind": kind,
            "strategy": strategy,
            "params": params,
            "metrics": metrics,
            "data_window": data_window or {},
            "git_rev": _git_rev(),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "notes": notes,
        }
        with open(self.root / f"{run_id}.json", "w") as f:
            json.dump(record, f, indent=2, default=str)
        return run_id

    def list_runs(self, strategy: Optional[str] = None) -> list[dict]:
        runs = []
        for p in sorted(self.root.glob("*.json")):
            with open(p) as f:
                r = json.load(f)
            if strategy is None or r.get("strategy") == strategy:
                runs.append(r)
        return runs
