"""Vectorized feature library over price/book time series.

Every feature is a pure function DataFrame -> Series, registered by name so
research configs can reference features declaratively.
"""
from __future__ import annotations

from typing import Callable

import numpy as np
import pandas as pd

FeatureFn = Callable[[pd.DataFrame], pd.Series]
REGISTRY: dict[str, FeatureFn] = {}


def feature(name: str) -> Callable[[FeatureFn], FeatureFn]:
    def deco(fn: FeatureFn) -> FeatureFn:
        REGISTRY[name] = fn
        return fn
    return deco


@feature("ret_1")
def ret_1(df: pd.DataFrame) -> pd.Series:
    return df["price"].diff()


@feature("ret_5")
def ret_5(df: pd.DataFrame) -> pd.Series:
    return df["price"].diff(5)


@feature("zscore_20")
def zscore_20(df: pd.DataFrame) -> pd.Series:
    p = df["price"]
    mu = p.rolling(20).mean()
    sd = p.rolling(20).std()
    return (p - mu) / sd.replace(0, np.nan)


@feature("ewma_dev")
def ewma_dev(df: pd.DataFrame) -> pd.Series:
    """Deviation of price from its EWMA — mean-reversion driver."""
    return df["price"] - df["price"].ewm(halflife=12).mean()


@feature("momentum_10")
def momentum_10(df: pd.DataFrame) -> pd.Series:
    return df["price"].diff(10)


@feature("vol_20")
def vol_20(df: pd.DataFrame) -> pd.Series:
    return df["price"].diff().rolling(20).std()


@feature("range_pos_20")
def range_pos_20(df: pd.DataFrame) -> pd.Series:
    """Position of price within its rolling 20-bar range, in [0,1]."""
    lo = df["price"].rolling(20).min()
    hi = df["price"].rolling(20).max()
    rng = (hi - lo).replace(0, np.nan)
    return (df["price"] - lo) / rng


def compute(df: pd.DataFrame, names: list[str]) -> pd.DataFrame:
    """Compute the named features; unknown names raise immediately."""
    out = df.copy()
    for n in names:
        if n not in REGISTRY:
            raise KeyError(f"unknown feature '{n}'; available: {sorted(REGISTRY)}")
        out[n] = REGISTRY[n](df)
    return out
