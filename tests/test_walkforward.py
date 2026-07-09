from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from quantbot.backtest.engine import BacktestData, grid_search
from quantbot.backtest.walkforward import walk_forward
from quantbot.config import AppConfig
from quantbot.core.types import MarketInfo
from quantbot.strategies.mean_reversion import MeanReversion

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _data(n=800, seed=4) -> BacktestData:
    rng = np.random.default_rng(seed)
    m = MarketInfo(
        condition_id="c1", question="Test?", slug="t",
        yes_token_id="c1_yes", no_token_id="c1_no",
        end_date=NOW + timedelta(days=365),
    )
    p = np.clip(0.5 + np.cumsum(rng.normal(0, 0.008, n)), 0.05, 0.95)
    df = pd.DataFrame(
        {"ts": [NOW + timedelta(hours=i) for i in range(n)], "price": p}
    )
    return BacktestData(markets={"c1": m}, prices={"c1": df})


def test_grid_search_orders_results():
    cfg = AppConfig()
    results = grid_search(
        cfg, _data(), MeanReversion, {"entry_z": [1.5, 3.0]},
    )
    assert len(results) == 2
    finals = [r.final_equity for _, r in results]
    assert finals == sorted(finals, reverse=True)
    assert set(results[0][0]) == {"entry_z"}


def test_grid_search_empty_grid_uses_defaults():
    cfg = AppConfig()
    results = grid_search(cfg, _data(), MeanReversion, {})
    assert len(results) == 1
    assert results[0][0] == {}


def test_walk_forward_produces_oos_folds():
    cfg = AppConfig()
    wf = walk_forward(
        cfg, _data(), MeanReversion, {"entry_z": [1.5, 2.5]},
        n_folds=3, train_frac=0.6,
    )
    assert len(wf.folds) >= 2
    for f in wf.folds:
        assert "best_params" in f and "oos_final_equity" in f
    # OOS equity curve is chained across folds and starts near initial capital
    if len(wf.oos_equity):
        assert abs(wf.oos_equity.iloc[0] - cfg.risk.initial_capital) < cfg.risk.initial_capital * 0.1
