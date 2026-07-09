"""Performance metrics over an equity curve and trade list.

All ratio metrics are annualized from the observed sampling frequency of the
equity curve. Confidence intervals come from analytics.bootstrap.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

PERIODS_PER_YEAR_FALLBACK = 365.0


@dataclass
class PerformanceReport:
    n_periods: int = 0
    n_trades: int = 0
    total_return: float = 0.0
    cagr: float = 0.0
    sharpe: float = 0.0
    sortino: float = 0.0
    calmar: float = 0.0
    max_drawdown: float = 0.0
    profit_factor: float = 0.0
    win_rate: float = 0.0
    expectancy: float = 0.0
    avg_holding_hours: float = 0.0
    exposure: float = 0.0
    turnover: float = 0.0
    sharpe_ci_low: Optional[float] = None
    sharpe_ci_high: Optional[float] = None
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def periods_per_year(index: pd.DatetimeIndex) -> float:
    if len(index) < 2:
        return PERIODS_PER_YEAR_FALLBACK
    dt = pd.Series(index).diff().dt.total_seconds().median()
    if not dt or dt <= 0:
        return PERIODS_PER_YEAR_FALLBACK
    return 365.25 * 86400 / dt


def max_drawdown(equity: pd.Series) -> float:
    peak = equity.cummax()
    dd = equity / peak - 1.0
    return float(-dd.min()) if len(dd) else 0.0


def sharpe_ratio(returns: pd.Series, ppy: float) -> float:
    if len(returns) < 2 or returns.std() == 0:
        return 0.0
    return float(returns.mean() / returns.std() * np.sqrt(ppy))


def sortino_ratio(returns: pd.Series, ppy: float) -> float:
    downside = returns[returns < 0]
    if len(returns) < 2 or len(downside) == 0 or downside.std() == 0:
        return 0.0
    return float(returns.mean() / downside.std() * np.sqrt(ppy))


def compute_report(
    equity: pd.Series,
    trades: Optional[pd.DataFrame] = None,
    initial_capital: Optional[float] = None,
) -> PerformanceReport:
    """equity: pd.Series indexed by tz-aware timestamps.
    trades: optional DataFrame with columns [pnl, holding_hours, notional]."""
    rep = PerformanceReport()
    if len(equity) < 2:
        return rep
    equity = equity.sort_index()
    rets = equity.pct_change().dropna()
    ppy = periods_per_year(equity.index)
    start_cap = initial_capital or float(equity.iloc[0])

    rep.n_periods = len(equity)
    rep.total_return = float(equity.iloc[-1] / start_cap - 1.0)
    years = max(len(rets) / ppy, 1e-9)
    base = float(equity.iloc[-1] / start_cap)
    rep.cagr = float(base ** (1 / years) - 1.0) if base > 0 else -1.0
    rep.sharpe = sharpe_ratio(rets, ppy)
    rep.sortino = sortino_ratio(rets, ppy)
    rep.max_drawdown = max_drawdown(equity)
    rep.calmar = rep.cagr / rep.max_drawdown if rep.max_drawdown > 1e-9 else 0.0

    if trades is not None and len(trades) > 0 and "pnl" in trades:
        pnl = trades["pnl"].astype(float)
        wins, losses = pnl[pnl > 0], pnl[pnl < 0]
        rep.n_trades = len(trades)
        rep.win_rate = float(len(wins) / len(trades))
        gross_profit, gross_loss = float(wins.sum()), float(-losses.sum())
        rep.profit_factor = (
            gross_profit / gross_loss if gross_loss > 1e-9 else float(gross_profit > 0)
        )
        rep.expectancy = float(pnl.mean())
        if "holding_hours" in trades:
            rep.avg_holding_hours = float(trades["holding_hours"].mean())
        if "notional" in trades:
            rep.turnover = float(trades["notional"].sum() / start_cap)
    return rep
