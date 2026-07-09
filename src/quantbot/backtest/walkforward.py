"""Walk-forward analysis: the anti-overfitting backbone.

Split the timeline into K folds. For each fold, optimize strategy params on
the training window, then evaluate out-of-sample on the test window. The
concatenated out-of-sample equity is the only performance number that counts
for promotion decisions.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import pandas as pd

from quantbot.backtest.engine import BacktestData, BacktestEngine, BacktestResult, grid_search
from quantbot.config import AppConfig
from quantbot.fairvalue.base import FairValueModel
from quantbot.strategies.base import Strategy

logger = logging.getLogger(__name__)


def _slice_data(data: BacktestData, start: datetime, end: datetime) -> BacktestData:
    prices = {}
    for cid, df in data.prices.items():
        ts = pd.to_datetime(df["ts"], utc=True)
        sub = df[(ts >= start) & (ts < end)]
        if len(sub) >= 10:
            prices[cid] = sub.reset_index(drop=True)
    candles = {}
    for sym, df in data.candles.items():
        ts = pd.to_datetime(df["ts"], utc=True)
        candles[sym] = df[(ts >= start) & (ts < end)].reset_index(drop=True)
    return BacktestData(markets=data.markets, prices=prices, candles=candles)


@dataclass
class WalkForwardResult:
    folds: list[dict] = field(default_factory=list)
    oos_equity: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    oos_trades: pd.DataFrame = field(default_factory=pd.DataFrame)


def walk_forward(
    cfg: AppConfig,
    data: BacktestData,
    strategy_cls: type[Strategy],
    param_grid: dict[str, list],
    n_folds: int = 4,
    train_frac: float = 0.6,
    fair_value: Optional[FairValueModel] = None,
) -> WalkForwardResult:
    all_ts = sorted(
        t
        for df in data.prices.values()
        for t in pd.to_datetime(df["ts"], utc=True)
    )
    if len(all_ts) < 100:
        raise ValueError("not enough data for walk-forward (need >= 100 points)")
    t0, t1 = all_ts[0], all_ts[-1]
    fold_len = (t1 - t0) / n_folds
    result = WalkForwardResult()
    equity_parts: list[pd.Series] = []
    trade_parts: list[pd.DataFrame] = []

    for k in range(n_folds):
        fold_start = t0 + k * fold_len
        fold_end = t0 + (k + 1) * fold_len
        split = fold_start + train_frac * fold_len
        train = _slice_data(data, fold_start, split)
        test = _slice_data(data, split, fold_end)
        if not train.prices or not test.prices:
            logger.warning("fold %d skipped: insufficient data", k)
            continue
        ranked = grid_search(cfg, train, strategy_cls, param_grid, fair_value=fair_value)
        best_params, best_train = ranked[0]
        engine = BacktestEngine(cfg, [strategy_cls(**best_params)], fair_value=fair_value)
        oos: BacktestResult = engine.run(test)
        result.folds.append(
            {
                "fold": k,
                "train_start": fold_start.isoformat(),
                "test_start": split.isoformat(),
                "test_end": fold_end.isoformat(),
                "best_params": best_params,
                "train_final_equity": best_train.final_equity,
                "oos_final_equity": oos.final_equity,
                "oos_n_trades": len(oos.trades),
            }
        )
        if len(oos.equity):
            # chain fold returns multiplicatively
            rets = oos.equity.pct_change().fillna(0.0)
            equity_parts.append(rets)
        if len(oos.trades):
            trade_parts.append(oos.trades)

    if equity_parts:
        chained = pd.concat(equity_parts)
        result.oos_equity = cfg.risk.initial_capital * (1.0 + chained).cumprod()
    if trade_parts:
        result.oos_trades = pd.concat(trade_parts, ignore_index=True)
    return result
