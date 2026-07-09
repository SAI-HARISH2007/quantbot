# quantbot

A research-first, production-grade quantitative trading platform for
[Polymarket](https://polymarket.com) prediction markets. It collects market
and external exchange data, estimates fair value for prediction contracts,
detects short-term pricing inefficiencies, backtests every hypothesis with
walk-forward validation, and paper-trades against real order books before
any live capital is risked.

**Design creed:** no edge is assumed. Every strategy is a hypothesis, every
hypothesis is measured, and only statistically significant improvements
survive (see `docs/RESEARCH.md`).

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# 1. Discover liquid Polymarket markets
quantbot markets sync --min-liquidity 5000
quantbot markets list

# 2. Collect data
quantbot collect history --days 30 --fidelity 10   # Polymarket price history
quantbot collect crypto --days 30                  # BTC/ETH candles (Binance)
quantbot collect books --interval 30 &             # live book recorder (for OBI research)

# 3. Backtest a strategy
quantbot backtest run mean_reversion --params '{"entry_z": 2.0}'

# 4. Walk-forward validation (the number that counts)
quantbot backtest walkforward mean_reversion --grid '{"entry_z": [1.5, 2.0, 2.5]}' --folds 4

# 5. Paper trade with the live dashboard (http://localhost:8000)
quantbot dashboard

# 6. Review experiments
quantbot report runs
```

**New to trading?** Read [HOW_TO_USE_QUANTBOT.md](HOW_TO_USE_QUANTBOT.md) —
a complete beginner's guide to the platform, the research pipeline, the
metrics, and the (deliberately strict) path to live trading.

## The dashboard

`quantbot dashboard` runs the paper-trading engine plus a real-time web UI:
live portfolio/PnL/drawdown, per-model fair values with disagreement,
order-book visualization, a decision feed where **every trade and rejection
is fully explainable** (evidence → sizing math → risk verdict → fill), a
post-trade report for every close, a strategy promotion pipeline
(research → paper → candidate; live is never automatic), auto-generated
daily reports, and crash-safe state resume for weeks-long unattended runs.

## Architecture (one paragraph)

Connectors (`data/`) pull Polymarket Gamma/CLOB and Binance/Coinbase data
into SQLite + Parquet (`data/storage.py`). Fair value models (`fairvalue/`)
— digital-option pricing off the underlying, microprice, EWMA anchors —
combine in an inverse-variance ensemble whose disagreement widens
uncertainty. Strategies (`strategies/`) map market views to signals; risk
(`risk/`) applies fractional-Kelly sizing, exposure limits and a drawdown
kill switch; execution (`execution/`) fills orders against real or synthetic
books with conservative slippage. The same strategy code runs in the
event-driven backtester (`backtest/`), the paper runner (`runner/`), and —
once promoted — live execution. Full details in `docs/ARCHITECTURE.md`.

## Strategies included

| name | hypothesis |
|---|---|
| `fair_value_deviation` | market price mean-reverts to model fair value |
| `mean_reversion` | spike moves in a market's own price revert |
| `momentum` | prediction markets underreact; drift continues |
| `obi` | top-of-book depth imbalance predicts next move (Cont et al. 2014) |
| `complement_arb` | ask(YES)+ask(NO) < $1 is riskless structural arb |

All are plug-and-play via `strategies/registry.py` and configured in
`configs/default.yaml`.

## Promotion pipeline

```
idea → backtest → walk-forward (OOS Sharpe CI must exclude 0)
     → paper trading (≥ 2 weeks, ≥ 100 trades, live books)
     → limited live capital (LiveBroker, explicitly gated)
```

Live trading is deliberately hard to turn on: `pip install ".[live]"`,
wallet keys in env vars, and `allow_live=True`. See `docs/DEPLOYMENT.md`.

## Tests

```bash
pytest            # ~60 unit tests, no network required
```

## Repository layout

```
src/quantbot/
  config.py            typed YAML+env configuration
  core/                domain types, clock abstraction, event bus
  data/                Polymarket (Gamma/CLOB/WS), Binance, storage
  features/            vectorized feature library
  fairvalue/           pricing models + ensemble
  strategies/          plug-and-play strategies + registry
  risk/                Kelly sizing, limits, kill switch
  portfolio/           positions, PnL, equity
  execution/           paper broker, live broker (gated)
  backtest/            event-driven engine, walk-forward, trade log
  analytics/           metrics, block-bootstrap confidence intervals
  experiments/         run tracking (params, metrics, git rev)
  runner/              live paper-trading loop
  cli.py               typer CLI
configs/               YAML configs
docs/                  architecture, research notes, deployment
tests/                 unit tests
```
