# Architecture

## Principles

1. **Backtest/live parity.** Strategies consume `MarketView` and emit
   `Signal`s — nothing else. The backtest engine, paper runner, and live
   runner all build views the same way; time comes from an injected `Clock`
   (`SimClock` in backtests, `WallClock` live). If a strategy behaves
   differently live than in backtest, that is a bug in the harness, not the
   strategy.
2. **Everything is a hypothesis.** Fair value models and strategies are
   competing implementations behind narrow interfaces
   (`FairValueModel.estimate`, `Strategy.on_view`). The experiment tracker
   records every run (params, metrics, git rev, data window) so comparisons
   are reproducible.
3. **Conservative simulation.** Paper fills walk real books and take an
   extra slippage haircut; backtest fills pay an assumed spread. Simulated
   results must lower-bound live results, never flatter them.
4. **Config-driven.** No tunable lives in code. `AppConfig` merges YAML +
   `QUANTBOT_*` env vars, all typed by pydantic.

## Data flow

```
                 ┌───────────────┐
 Gamma API ─────▶│  GammaClient  │──▶ markets (SQLite)
                 └───────────────┘
                 ┌───────────────┐        ┌──────────────┐
 CLOB REST ─────▶│  ClobClient   │──▶ prices-history ─▶ │   Parquet    │
 CLOB WS ───────▶│  MarketStream │──▶ book snapshots ─▶ │   SQLite     │
                 └───────────────┘        └──────┬───────┘
                 ┌───────────────┐               │
 Binance ───────▶│ BinanceClient │──▶ candles ───┘
                 └───────────────┘
                        │
                        ▼
        ┌──────────────────────────────────┐
        │ FairValueContext (per market)    │
        │  book, pm history, spot, candles │
        └──────────────┬───────────────────┘
                       ▼
        DigitalOptionModel ┐
        MicropriceModel    ├──▶ EnsembleFairValue (inverse-variance)
        TimeWeightedModel  ┘         │
                       ┌─────────────┘
                       ▼
        MarketView ──▶ Strategy.on_view() ──▶ [Signal]
                       ▼
        size_order (fractional Kelly × confidence, hard caps)
                       ▼
        RiskManager.check_order (limits, kill switch)
                       ▼
        Execution: BacktestEngine fill | PaperBroker | LiveBroker
                       ▼
        Portfolio (cash, positions, equity) ──▶ TradeLog ──▶ analytics
```

## Subsystem notes

### Market data
* **GammaClient** paginates `/markets`, parsing only well-formed binary
  YES/NO markets (`clobTokenIds` and `outcomes` arrive as JSON strings —
  verified against the live API).
* **ClobClient** provides `/book`, `/midpoint`, `/prices-history` with
  retry + rate limiting. **MarketStream** subscribes to the public
  `market` WS channel and reconnects with exponential backoff.
* **BinanceClient** paginates klines (1000/bar cap handled); a trade WS
  stream exists for low-latency spot updates.

### Storage
SQLite for relational data (markets, fills, book snapshots) + Parquet for
bulk series (PM price history, candles). Writes are idempotent
(upserts / de-dup on merge), so collectors can be re-run safely.

### Fair value engine
* `DigitalOptionModel`: crypto threshold markets are cash-or-nothing
  digitals; P(YES) = N(d2) under zero-drift GBM. Strike/direction parsed
  from the question; vol from pluggable realized-vol estimators
  (close-to-close, EWMA, Parkinson, Garman-Klass) — the estimator choice is
  itself a research variable. Model uncertainty = sensitivity of N(d2) to a
  30% vol misspecification.
* `MicropriceModel`: size-weighted top-of-book price, uncertainty ∝ spread.
* `TimeWeightedModel`: EWMA of the market's own price, uncertainty from
  residual variance.
* `EnsembleFairValue`: inverse-variance weighting; between-model
  disagreement inflates the ensemble std, which strategies treat as a
  "don't trade" widening.

### Risk
Kelly for binary contracts: `f* = (q − p)/(1 − p)` (long), scaled by
`kelly_fraction` (default 0.25) and signal confidence, capped at
`max_kelly_stake_pct` of equity and per-market/total exposure limits.
A drawdown kill switch halts all new orders at `max_drawdown_pct`.

### Backtesting
Event-driven over merged per-market price streams. Signals execute on the
*next* event for their market (no look-ahead) within a TTL. Fills pay an
assumed spread + slippage (both are sensitivity parameters). Settlement:
final price ≥ 0.90 → YES, ≤ 0.10 → NO, else settle at last mark (flagged).
`grid_search` + `walk_forward` (train/test folds, OOS-only reporting)
guard against overfitting; block-bootstrap CIs quantify uncertainty.

### Execution
`PaperBroker` fills against the *current real* CLOB book, walking levels
and applying slippage/fees. `LiveBroker` (gated behind the `live` extra,
env-var credentials, and `allow_live=True`) posts real orders via
py-clob-client.

## Multi-market architecture (in progress)

Markets are plugins behind `markets/base.py::MarketAdapter` — the engine
speaks only `Instrument` (venue-neutral, with an explicit `PayoffType`) and
the adapter interface (`list_instruments / get_book / get_history /
stream_books`). `AdapterRegistry` maps config names to implementations.

| Venue | Data | Paper execution | Notes |
|---|---|---|---|
| `polymarket` | ✅ complete | ✅ complete | reference adapter; binary payoff |
| `binance_spot` | ✅ complete | ⏳ next | linear payoff; needs linear sizing path |
| perps / forex / equities / ETFs / futures | planned | planned | one connector file each, same contract |

The deliberate hard part is **payoff semantics, not connectivity**: binary
contracts (Kelly-for-binary sizing, complement conversion, $0/$1
settlement) versus linear assets (fractional Kelly on return distributions,
shorting, funding). Sizing/settlement will move behind per-payoff policies
selected by `Instrument.payoff` before any linear venue trades — the risk
framework must never silently apply binary math to a linear instrument.
Options are last: they need a vol surface, not just a feed.

## Known simplifications (tracked, deliberate)

* Backtests use synthetic spreads because historical full books are not
  publicly available; the book recorder (`quantbot collect books`) is
  building the dataset to replace this.
* Live fills are acknowledged at limit price; wiring the CLOB user channel
  for true fill confirmations is required before scaling live size.
* Settlement inference from final price is a proxy; the Gamma API's
  resolved-outcome field should be joined in for markets where available.
