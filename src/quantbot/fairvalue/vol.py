"""Realized volatility estimators.

Multiple competing estimators — the research harness decides which produces
better digital-option calibration, we do not assume. All return *annualized*
volatility from a candle DataFrame with columns [open, high, low, close].
"""
from __future__ import annotations

import numpy as np
import pandas as pd

SECONDS_PER_YEAR = 365.25 * 24 * 3600


def _annualize(per_bar_var: float, bar_seconds: float) -> float:
    if per_bar_var <= 0 or bar_seconds <= 0:
        return 0.0
    return float(np.sqrt(per_bar_var * SECONDS_PER_YEAR / bar_seconds))


def infer_bar_seconds(df: pd.DataFrame) -> float:
    if len(df) < 2:
        return 60.0
    return float(pd.Series(df["ts"]).diff().dt.total_seconds().median())


def close_to_close(df: pd.DataFrame, span: int | None = None) -> float:
    """Classic close-to-close estimator; EWMA-weighted if span given."""
    r = np.log(df["close"]).diff().dropna()
    if len(r) < 2:
        return 0.0
    var = float(r.ewm(span=span).var().iloc[-1]) if span else float(r.var())
    return _annualize(var, infer_bar_seconds(df))


def parkinson(df: pd.DataFrame) -> float:
    """Parkinson (1980): uses high/low range; ~5x more efficient than c2c."""
    hl = np.log(df["high"] / df["low"]) ** 2
    if len(hl) < 2:
        return 0.0
    var = float(hl.mean() / (4.0 * np.log(2.0)))
    return _annualize(var, infer_bar_seconds(df))


def garman_klass(df: pd.DataFrame) -> float:
    """Garman-Klass (1980): combines range and close-open information."""
    if len(df) < 2:
        return 0.0
    hl = 0.5 * np.log(df["high"] / df["low"]) ** 2
    co = (2.0 * np.log(2.0) - 1.0) * np.log(df["close"] / df["open"]) ** 2
    var = float((hl - co).mean())
    return _annualize(max(var, 0.0), infer_bar_seconds(df))


ESTIMATORS = {
    "close_to_close": close_to_close,
    "ewma": lambda df: close_to_close(df, span=120),
    "parkinson": parkinson,
    "garman_klass": garman_klass,
}


def estimate_vol(df: pd.DataFrame, method: str = "ewma") -> float:
    if method not in ESTIMATORS:
        raise ValueError(f"unknown vol estimator '{method}'; options: {list(ESTIMATORS)}")
    return ESTIMATORS[method](df)
