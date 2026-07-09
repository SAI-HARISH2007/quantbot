# Research Notes & Methodology

## Ground rules

1. **No assumed edges.** Any claim about a trader, a strategy, or a market
   inefficiency is a hypothesis until validated on observable data.
2. **Out-of-sample or it didn't happen.** In-sample backtest results select
   hypotheses; only walk-forward OOS results (with bootstrap CIs) can
   promote them.
3. **Costs first.** Every result is reported net of spread, slippage, and
   fees; sensitivity to the assumed spread is part of the result.

## Hypothesis registry

| ID | Hypothesis | Strategy | Status |
|----|-----------|----------|--------|
| H1 | Crypto threshold markets misprice vs digital-option fair value at short horizons | `fair_value_deviation` | implemented, needs data |
| H2 | Single-market price spikes mean-revert | `mean_reversion` | implemented, needs data |
| H3 | Prediction markets underreact; drift continues | `momentum` | implemented, needs data |
| H4 | Top-of-book imbalance predicts next mid move (Cont–Kukanov–Stoikov) | `obi` | implemented, needs live book recordings |
| H5 | ask(YES)+ask(NO) < 1 windows exist and are capturable | `complement_arb` | implemented, monitor in paper |
| H6 | Vol estimator choice (c2c/EWMA/Parkinson/GK) materially changes digital-option calibration | vol sweep | pending |
| H7 | Favourite–longshot bias: extreme-priced contracts are systematically mispriced | pending strategy | pending |

H2 and H3 are deliberately opposing hypotheses. Per market-category and
horizon, walk-forward decides which (if either) survives. Both surviving on
disjoint regimes is an acceptable outcome; both surviving on the same data
is a red flag for harness bugs.

## Evaluation protocol

For every strategy run the platform produces: Sharpe, Sortino, Calmar,
max drawdown, profit factor, win rate, expectancy, avg holding time,
exposure, turnover, trade distribution, and a stationary-block-bootstrap
95% CI on Sharpe (`analytics/bootstrap.py`).

**Promotion criteria (paper → live):**
- Walk-forward OOS Sharpe CI excludes 0 (≥ 95%)
- ≥ 100 OOS trades
- Max drawdown within configured tolerance
- ≥ 2 weeks live paper trading with performance consistent with OOS
- Result robust to ±50% assumed-spread perturbation

**Kill criteria (live → off):** drawdown kill switch (automatic), or paper/
live performance falling outside the OOS bootstrap distribution.

## Calibration measurement (fair value models)

For each resolved market, log every model's P(YES) forecasts over time and
score with Brier score / log loss vs the actual outcome. Compare models on
identical forecast sets; retain only models that beat the market's own
price as a predictor (the market price is the benchmark to beat — usually
it is well calibrated, which is exactly why unconditional edges are rare).

## Data collection strategy

Public endpoints give: market metadata (Gamma), price history at 1-minute
fidelity (CLOB `/prices-history`), and *current* books only. Historical
full books are not available, therefore:

- `quantbot collect books` runs a snapshot recorder (REST) and
  `MarketStream` (WS) to accumulate our own book dataset — the OBI and
  execution-cost research (H4, spread models) depends on it.
- Until then, backtests use an assumed-spread synthetic book, and OBI is
  validated only in paper mode against real books.

## Literature applied

- Cont, Kukanov & Stoikov (2014), *The price impact of order book events* —
  OBI signal design.
- Politis & Romano (1994), *The stationary bootstrap* — Sharpe CIs on
  autocorrelated PnL.
- Parkinson (1980), Garman & Klass (1980) — range-based vol estimators.
- Kelly (1956) + Thorp's fractional-Kelly practice — sizing under estimation
  error.
- Wolfers & Zitzewitz (2004), *Prediction markets* — favourite–longshot
  bias and calibration benchmarks (H7).

## Findings log

**2026-07-09 — first live-data backtests (14d × ~200 markets, 30-min bars):**

1. *Signal TTL vs bar width.* Signals with a 60s TTL silently expired before
   the next 30-min bar, producing zero trades. Fixed: default TTL 3600s;
   microstructure strategies override downward. Lesson encoded in the
   engine's no-look-ahead design — signals execute on the *next* event.
2. *Longshot costs dominate.* Naive mean reversion traded almost exclusively
   1–8c longshots (buying "dips" that were actually information), and a flat
   2c synthetic spread implies >100% round-trip cost at those prices —
   every one of 90 trades lost. Two changes: (a) synthetic spread now capped
   at 30% of distance-to-bound (real books tighten near 0/1); (b)
   mean-reversion/momentum gained a tradable price band [0.10, 0.90]
   (default; a research parameter — H7 studies the tails deliberately).
3. *Universe composition matters more than parameters.* A top-liquidity
   slice of Gamma markets was ~70% longshots; strategy results are
   meaningless without controlling for the price distribution of the
   traded universe. Reports should stratify by entry-price bucket.
4. *H2 first verdict — not confirmed.* Full-period backtest (215 markets,
   14d, 30-min bars): +5.7%, Sharpe 2.72, but bootstrap CI [-5.0, +10.2]
   includes 0. Walk-forward (3 folds, entry_z ∈ {1.5, 2.5}): OOS profit
   factor 0.997, expectancy -$0.02/trade — the in-sample gain does not
   survive out-of-sample. Much of the full-period PnL came from positions
   held to settlement (avg holding 313h), which short OOS folds truncate;
   this points at *settlement capture*, not intraday reversion, as the
   candidate effect to isolate next (and it needs resolved-outcome data,
   not last-price proxies, before it can be believed).

## Open research queue

1. Record 2+ weeks of books on top-50 liquid markets; fit an empirical
   spread/depth model; replace the assumed-spread backtest fill.
2. Vol-estimator sweep (H6) once ≥ 30 crypto threshold markets resolve.
3. Cross-market consistency: multi-strike threshold markets on the same
   asset/date imply a distribution; test monotonicity violations as an arb
   family (extends `complement_arb`).
4. Event-driven strategies around scheduled announcements (CPI, FOMC) —
   requires an economic calendar connector.
5. Online recalibration of ensemble weights from rolling Brier scores
   (simple online learning before any RL is considered; RL only if it beats
   the online-weights baseline OOS).
