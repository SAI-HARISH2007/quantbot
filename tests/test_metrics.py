from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from quantbot.analytics.bootstrap import prob_sharpe_positive, sharpe_confidence_interval
from quantbot.analytics.metrics import compute_report, max_drawdown, periods_per_year


def _equity(values: list[float], hours: float = 24.0) -> pd.Series:
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    idx = pd.DatetimeIndex([t0 + timedelta(hours=hours * i) for i in range(len(values))])
    return pd.Series(values, index=idx)


def test_max_drawdown():
    eq = _equity([100, 120, 90, 110, 80])
    assert abs(max_drawdown(eq) - (1 - 80 / 120)) < 1e-9


def test_periods_per_year_daily():
    eq = _equity([1.0] * 10, hours=24)
    assert abs(periods_per_year(eq.index) - 365.25) < 1.0


def test_report_on_steady_gains():
    eq = _equity([100 * 1.01**i for i in range(100)])
    rep = compute_report(eq)
    assert rep.total_return > 1.0
    assert rep.sharpe > 5  # deterministic gains -> huge Sharpe
    assert rep.max_drawdown < 1e-9
    assert rep.n_periods == 100


def test_report_trades_block():
    eq = _equity([100, 101, 102])
    trades = pd.DataFrame(
        {"pnl": [10.0, -5.0, 15.0, -2.0], "holding_hours": [1, 2, 3, 4],
         "notional": [50, 50, 50, 50]}
    )
    rep = compute_report(eq, trades)
    assert rep.n_trades == 4
    assert abs(rep.win_rate - 0.5) < 1e-9
    assert abs(rep.profit_factor - 25 / 7) < 1e-9
    assert abs(rep.expectancy - 4.5) < 1e-9


def test_bootstrap_ci_brackets_point_estimate():
    rng = np.random.default_rng(1)
    rets = pd.Series(rng.normal(0.001, 0.01, 300))
    point, lo, hi = sharpe_confidence_interval(rets, ppy=365, n_boot=300)
    assert lo <= point <= hi


def test_prob_sharpe_positive_detects_edge():
    rng = np.random.default_rng(2)
    good = pd.Series(rng.normal(0.003, 0.01, 400))
    bad = pd.Series(rng.normal(-0.003, 0.01, 400))
    assert prob_sharpe_positive(good, 365, n_boot=300) > 0.9
    assert prob_sharpe_positive(bad, 365, n_boot=300) < 0.1
