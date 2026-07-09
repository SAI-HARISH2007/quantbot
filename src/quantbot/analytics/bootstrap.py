"""Bootstrap inference for performance metrics.

Stationary block bootstrap (Politis & Romano 1994) on the returns series —
IID resampling understates uncertainty when returns are autocorrelated,
which trading PnL almost always is.
"""
from __future__ import annotations

from typing import Callable

import numpy as np
import pandas as pd


def stationary_block_bootstrap(
    returns: np.ndarray,
    stat: Callable[[np.ndarray], float],
    n_boot: int = 2000,
    avg_block: int = 10,
    seed: int = 7,
) -> np.ndarray:
    """Distribution of `stat` under stationary block resampling."""
    rng = np.random.default_rng(seed)
    n = len(returns)
    if n < 5:
        return np.array([stat(returns)] * n_boot)
    p = 1.0 / avg_block
    out = np.empty(n_boot)
    for b in range(n_boot):
        idx = np.empty(n, dtype=int)
        idx[0] = rng.integers(n)
        for i in range(1, n):
            idx[i] = rng.integers(n) if rng.random() < p else (idx[i - 1] + 1) % n
        out[b] = stat(returns[idx])
    return out


def sharpe_confidence_interval(
    returns: pd.Series,
    ppy: float,
    alpha: float = 0.05,
    n_boot: int = 2000,
) -> tuple[float, float, float]:
    """Return (sharpe, ci_low, ci_high). p(sharpe<=0) is 1 - percentile rank of 0."""
    r = returns.dropna().to_numpy()

    def _sharpe(x: np.ndarray) -> float:
        s = x.std()
        return float(x.mean() / s * np.sqrt(ppy)) if s > 0 else 0.0

    point = _sharpe(r)
    dist = stationary_block_bootstrap(r, _sharpe, n_boot=n_boot)
    lo, hi = np.percentile(dist, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    return point, float(lo), float(hi)


def prob_sharpe_positive(returns: pd.Series, ppy: float, n_boot: int = 2000) -> float:
    r = returns.dropna().to_numpy()

    def _sharpe(x: np.ndarray) -> float:
        s = x.std()
        return float(x.mean() / s * np.sqrt(ppy)) if s > 0 else 0.0

    dist = stationary_block_bootstrap(r, _sharpe, n_boot=n_boot)
    return float((dist > 0).mean())
