# How to Use QuantBot — A Complete Beginner's Guide

> **Read this first.** This guide assumes you have *no* trading experience.
> It explains every concept you need as it comes up. The single most
> important rule of this entire document:
>
> **QuantBot trades with pretend money (paper trading) by default, and you
> should keep it that way until every criterion in Section 9 is met — which
> realistically takes months. There is no rush. Markets will still be there.**

---

## Table of contents

1. [What is QuantBot and how does it work?](#1-what-is-quantbot)
2. [The architecture, in simple language](#2-architecture)
3. [How the research pipeline works](#3-research-pipeline)
4. [Collecting market data](#4-collecting-data)
5. [Running backtests](#5-backtests)
6. [Understanding the evaluation metrics](#6-metrics)
7. [Starting paper trading](#7-paper-trading)
8. [Monitoring paper-trading performance](#8-monitoring)
9. [The criteria that must be met before live trading](#9-live-criteria)
10. [Configuring risk management safely](#10-risk-config)
11. [Connecting a real account (when you're ready — not now)](#11-connecting-live)
12. [Common beginner mistakes](#12-mistakes)
13. [Your progression path: beginner → confident operator](#13-progression)
14. [Troubleshooting](#14-troubleshooting)
15. [Best practices for operating and maintaining the system](#15-best-practices)
16. [Glossary](#16-glossary)

---

<a name="1-what-is-quantbot"></a>
## 1. What is QuantBot and how does it work?

### What is Polymarket?

Polymarket is a **prediction market**. People trade contracts on real-world
questions like *"Will Bitcoin be above $65,000 on July 31?"*. Each contract
has two sides:

- A **YES** share pays **$1** if the event happens, **$0** if it doesn't.
- A **NO** share pays **$1** if the event *doesn't* happen, **$0** if it does.

Prices are always between $0 and $1, and the price is effectively the
market's opinion of the **probability**. If YES trades at $0.30, the market
collectively believes there's about a 30% chance the event happens.

### What does QuantBot do?

QuantBot is a research system that tries to find moments when the market's
price is *wrong* — when a contract's price temporarily differs from what it
"should" be worth — and simulates trading on those moments. It:

1. **Collects data** — Polymarket prices and order books, plus Bitcoin and
   Ethereum prices from crypto exchanges (some Polymarket questions are
   about crypto prices, so the actual BTC price helps compute what the
   contract *should* cost).
2. **Estimates fair value** — mathematical models compute what a contract
   is probably worth.
3. **Generates signals** — when the market price is far from fair value (or
   other patterns appear), a strategy proposes a trade.
4. **Applies risk controls** — every proposed trade passes through position
   sizing and safety limits before anything happens.
5. **Simulates the trade** — in backtests (on historical data) or paper
   trading (live data, pretend money).
6. **Measures everything** — every experiment produces statistics that tell
   you honestly whether a strategy works.

### What QuantBot is NOT

- ❌ It is **not** a money-printing machine. Most trading strategies fail.
- ❌ It does **not** guarantee profits. Our own first experiment (see
  `docs/RESEARCH.md`) showed a strategy that *looked* profitable but wasn't
  statistically real. That's normal — and finding that out **before**
  risking money is exactly what the platform is for.
- ❌ It does **not** trade real money unless you take several deliberate,
  documented steps to enable it (Section 11). It cannot happen by accident.

---

<a name="2-architecture"></a>
## 2. The architecture, in simple language

Think of QuantBot as a factory assembly line:

```
┌────────────┐   ┌────────────┐   ┌────────────┐   ┌────────────┐
│ Data       │──▶│ Fair value │──▶│ Strategies │──▶│ Risk       │
│ collectors │   │ models     │   │ (signals)  │   │ manager    │
└────────────┘   └────────────┘   └────────────┘   └─────┬──────┘
                                                         │approve/reject
┌────────────┐   ┌────────────┐   ┌────────────┐   ┌─────▼──────┐
│ Reports &  │◀──│ Portfolio  │◀──│ Trade log  │◀──│ Execution  │
│ metrics    │   │ (your $)   │   │            │   │ (simulated)│
└────────────┘   └────────────┘   └────────────┘   └────────────┘
```

- **Data collectors** (`src/quantbot/data/`) — download prices and order
  books and save them to disk (a small database plus data files).
- **Fair value models** (`src/quantbot/fairvalue/`) — several independent
  "opinions" about what a contract is worth, blended together. When the
  models disagree with each other, the system automatically becomes *less*
  confident and trades less. Uncertainty = caution.
- **Strategies** (`src/quantbot/strategies/`) — five different trading
  ideas (explained in Section 5). Each one is a *hypothesis*, not a fact.
- **Risk manager** (`src/quantbot/risk/`) — the safety officer. It decides
  how big each trade can be and can veto any trade. It also contains the
  **kill switch**: if the account loses 10% from its peak, all trading
  stops automatically.
- **Execution** (`src/quantbot/execution/`) — places the (simulated)
  orders. The paper broker uses *real live prices* but *pretend money*.
- **Portfolio** — tracks cash, positions, and profit/loss.
- **Reports** — every run is recorded permanently so you can review it.

The crucial design property: **the exact same strategy code runs in
backtests, paper trading, and (eventually) live trading.** What you test is
what you get.

---

<a name="3-research-pipeline"></a>
## 3. How the research pipeline works

This is the heart of the system, and the part that protects your money.

### The problem: it's easy to fool yourself

If you test 100 random strategies on past data, a few will look brilliant
*by pure luck* — like flipping coins and declaring the person who got 8
heads in a row a "coin-flipping genius." This trap is called
**overfitting**, and it is the #1 reason people lose money with trading
bots.

### The defense: a gauntlet every strategy must survive

```
 Idea ──▶ Backtest ──▶ Walk-forward test ──▶ Paper trading ──▶ (maybe) Live
          (history)     (harder history      (live prices,      (real money,
                         test)                fake money)        tiny amounts)
```

1. **Backtest** — replay history and see how the strategy would have done.
   Passing this means almost nothing by itself. It only earns the strategy
   the right to the next test.
2. **Walk-forward test** — the anti-overfitting weapon. The system splits
   history into pieces: it *tunes* the strategy on one piece, then tests it
   on the *next* piece it has never seen — repeated several times. Only the
   never-seen ("out-of-sample") results count. A lucky strategy falls apart
   here. (Ours did! Mean reversion looked great in the plain backtest and
   scored roughly zero in walk-forward. The system worked as designed.)
3. **Statistical confidence** — the system computes a *confidence interval*
   (Section 6) around the results. If the interval includes "no profit at
   all," the strategy is unproven, full stop.
4. **Paper trading** — the strategy trades live markets with fake money for
   weeks. This catches everything history can't: real order books, real
   delays, real weirdness.
5. **Live** — only after all of the above, with strict criteria (Section 9)
   and tiny amounts.

**Your job as operator** is simply to never skip a stage. The tools make
each stage one command.

---

<a name="4-collecting-data"></a>
## 4. Collecting market data

### One-time setup

```bash
cd quantbot
python3 -m venv .venv
source .venv/bin/activate      # you must do this in every new terminal
pip install -e ".[dev]"
pytest                          # all tests should pass — takes ~1 minute
```

> **What is a venv?** A private folder of Python packages for this project,
> so it can't conflict with anything else on your computer. The
> `source .venv/bin/activate` line "enters" it; you'll see `(.venv)` in
> your prompt.

### Step 1 — discover markets

```bash
quantbot markets sync --min-liquidity 2000 --pages 8
quantbot markets list
```

This downloads the list of active Polymarket markets and stores those with
at least $2,000 of **liquidity** (money available to trade against — thin
markets are avoided because trading them is expensive and results are
unreliable). `markets list` shows what you got.

### Step 2 — collect price history

```bash
quantbot collect history --days 30 --fidelity 10
```

Downloads up to 30 days of price history for every stored market, in
10-minute bars. Takes a few minutes. Re-running is safe — it never
duplicates data.

### Step 3 — collect crypto prices

```bash
quantbot collect crypto --days 30
```

Downloads Bitcoin and Ethereum prices from Binance (public data — **no
account needed**). The fair-value models need these for crypto-related
markets.

### Step 4 (optional but valuable) — record order books

```bash
quantbot collect books --interval 30 --top 20
```

This runs continuously (stop with Ctrl+C), photographing the **order book**
(the live list of all buy and sell offers) of the 20 most liquid markets
every 30 seconds. Polymarket doesn't provide historical order books, so the
only way to get this data is to record it yourself. The order-book–based
strategy (OBI) can only be properly researched with weeks of these
recordings — start recording early, let it run in the background.

### How often should you collect?

Run steps 1–3 every few days (or daily) while you're actively researching.
Data accumulates; more history = more trustworthy backtests.

---

<a name="5-backtests"></a>
## 5. Running backtests

### The strategies available

| Name | The idea, in plain words |
|---|---|
| `fair_value_deviation` | "The price is far from what our models say it's worth — bet it comes back." |
| `mean_reversion` | "The price just spiked violently for no visible reason — bet it settles back." |
| `momentum` | "The price has been steadily drifting one way — bet the drift continues." |
| `obi` | "There are far more buyers than sellers queued up right now — bet the price ticks up." (needs order-book recordings) |
| `complement_arb` | "YES + NO can be bought together for less than $1 total, which guarantees a profit at resolution — grab it." (rare, but risk-free when it appears) |

### Run one

```bash
quantbot backtest run mean_reversion --params '{"entry_z": 2.0}'
```

You'll get a table of metrics (explained in Section 6) and the run is saved
permanently to `experiments/runs/`.

### Run the one that actually matters: walk-forward

```bash
quantbot backtest walkforward mean_reversion \
    --grid '{"entry_z": [1.5, 2.0, 2.5]}' --folds 4
```

The `--grid` says "try these different settings"; the system tunes on old
data and grades on unseen data, fold by fold. **The final "Out-of-sample"
table is the only result you should believe.**

### Reading the two results together

| Plain backtest says | Walk-forward says | Conclusion |
|---|---|---|
| Profitable | Profitable | Worth advancing to paper trading |
| Profitable | Flat/losing | **Overfit. Discard or rework.** (This is the most common outcome.) |
| Losing | — | Discard. |
| Insanely profitable (Sharpe > 5, tiny drawdown) | anything | **Suspect a bug** before celebrating. Real edges are modest. |

---

<a name="6-metrics"></a>
## 6. Understanding the evaluation metrics

Every backtest prints this table. Here's what each number means and what
"good" looks like. Don't memorize this — come back to it whenever you read
a report.

| Metric | Plain-English meaning | Rule of thumb |
|---|---|---|
| `total_return` | How much the account grew over the whole test. `0.05` = +5%. | Positive, obviously — but *alone it means nothing* without the risk numbers below. |
| `sharpe` | **The single most important number.** Return earned *per unit of risk taken*. A high return achieved with wild swings scores low; a modest, steady return scores high. | < 0: losing. 0–1: weak. 1–2: decent. 2–3: good. > 4–5 in a backtest: probably a bug or overfitting. |
| `sharpe_ci_low` / `sharpe_ci_high` | The **confidence interval**: statistics' honest answer to "could this just be luck?" The true Sharpe is probably between these two numbers. | **If `sharpe_ci_low` ≤ 0, the strategy is unproven.** The range includes "actually loses money." This single check protects you more than any other. |
| `sortino` | Like Sharpe, but only counts *downward* swings as risk. | Should be ≥ Sharpe. Similar scale. |
| `max_drawdown` | The worst peak-to-valley loss during the test. `0.08` = at the worst moment, the account was down 8% from its best point. | **This is the number to feel in your gut**: could you watch that loss happen with real money and not panic? Under 0.10 is comfortable; over 0.20 is scary. |
| `calmar` | Yearly return divided by max drawdown — "was the pain worth it?" | > 1 is respectable. |
| `profit_factor` | Total $ won ÷ total $ lost. | > 1.3 is meaningful. 0.9–1.1 is basically a coin flip. Exactly 1.0 = break-even. |
| `win_rate` | Fraction of trades that made money. | **Beginners over-focus on this.** A 25% win rate can be very profitable (small losses, big wins) and a 90% win rate can be ruinous (tiny wins, catastrophic losses). Read it together with `profit_factor` and `expectancy`. |
| `expectancy` | Average profit per trade in dollars. | Positive, and large enough to survive costs. `+$0.10` per trade is noise; it dies to slippage. |
| `n_trades` | Number of completed trades. | **Under ~100 trades, none of the statistics are trustworthy.** Small samples lie. |
| `avg_holding_hours` | How long positions stay open on average. | Just context — but if it's near the market's lifetime, profits may come from holding to resolution, not from the "signal." |
| `turnover` | Total traded volume ÷ starting capital. High turnover = costs matter enormously. | Context for cost sensitivity. |
| `max drawdown` occurring at the very end | — | Worse than it looks: the test ended while losing. |

**The three-question test for any result:**
1. Is `sharpe_ci_low` above 0? (Is it statistically real?)
2. Is `n_trades` ≥ 100? (Is the sample big enough?)
3. Is `max_drawdown` something you could genuinely tolerate?

If any answer is no → the strategy stays in research. No exceptions.

---

<a name="7-paper-trading"></a>
## 7. Starting paper trading

Paper trading = the bot trades **live, real markets** with **pretend
money**. It's the dress rehearsal. Real prices, real order books, real
timing — zero financial risk.

```bash
quantbot paper run --top 10 --poll 30
```

- `--top 10` — trade the 10 most liquid markets.
- `--poll 30` — check the markets every 30 seconds.
- It uses the strategies enabled in `configs/default.yaml`.
- Stop anytime with **Ctrl+C**. Nothing bad happens; positions are pretend.

Every run gets an ID like `paper_20260709_081010`, printed at start. All
simulated fills are stored under that ID. The log line
`equity: $9998.69 (peak $10000.00)` is your pretend account value, updated
each cycle.

To keep it running after you close the terminal (Linux/WSL):

```bash
nohup quantbot paper run --top 10 > paper.out 2>&1 &
```

(Or use `tmux`/`systemd` — see `docs/DEPLOYMENT.md`.)

**Let it run for weeks, not hours.** Two weeks is the *minimum* before the
results mean anything; a month is better.

---

<a name="8-monitoring"></a>
## 8. Monitoring paper-trading performance

Build a simple routine:

### Daily (2 minutes)
```bash
# Is it still running?
ps aux | grep "quantbot paper"

# What has it done lately? (equity lines + any trades)
tail -50 logs/quantbot.log | grep -E "equity|BUY|SELL|rejected|ERROR"
```
Check: equity value sane? Any repeated ERROR lines? Any
`KILL SWITCH` message? (If the kill switch fired, trading has already
stopped safely — your job is just to understand why before restarting.)

### Weekly (15 minutes)
```bash
# All simulated trades for your run
quantbot report fills paper_20260709_081010

# Past experiments for comparison
quantbot report runs
```
Ask yourself:
- Is the number of trades roughly what backtests predicted?
- Are fills happening at sane prices?
- Is equity drifting down slowly? (That's costs eating you — normal to see,
  important to measure.)
- Do any single markets dominate the activity? (Concentration = hidden risk.)

### What "success" looks like after 2–4 weeks
Paper performance should be *consistent with* the walk-forward results —
not identical, but in the same ballpark. If paper trading is much worse
than the backtest, believe the paper result. It's the more honest test.

---

<a name="9-live-criteria"></a>
## 9. The exact criteria before considering live trading

**All of these. Not most. All.** This list also lives in
`docs/RESEARCH.md`; it is the platform's constitution.

- [ ] **Walk-forward proof**: out-of-sample Sharpe confidence interval
      entirely above 0 (`sharpe_ci_low > 0`).
- [ ] **Sample size**: ≥ 100 out-of-sample trades.
- [ ] **Paper duration**: ≥ 2 consecutive weeks of paper trading (4+ is
      better), with ≥ 100 paper trades.
- [ ] **Paper consistency**: paper results statistically consistent with
      walk-forward results (not dramatically worse).
- [ ] **Drawdown tolerance**: worst paper/backtest drawdown is one you have
      *personally decided* you could tolerate in real money — written down,
      in advance.
- [ ] **Cost robustness**: the strategy stays profitable when you re-run
      the backtest with doubled slippage/spread assumptions.
- [ ] **You understand the strategy**: you can explain in two sentences
      *why* this strategy should make money and *who is losing* that money
      to you. "The bot says so" is not an answer.
- [ ] **Legal check**: you have verified Polymarket is legal to use in your
      country/jurisdiction (see Section 11 — this is *your* responsibility).
- [ ] **Affordable loss**: the money you'd deposit is money you could lose
      **entirely** — 100% of it — without affecting your life. Trading
      capital is not savings.
- [ ] **Time**: at least 3 months have passed since you started using the
      platform. Familiarity is a safety feature.

Even then: start with the smallest possible amounts (Section 11), and treat
the first month of live trading as another test, not as income.

**If a strategy doesn't meet these criteria, the correct action is to say
"it's unproven" and keep researching. As of this writing, that is the
status of every strategy in this repository — including the ones that
looked good at first glance. That's not failure; that's the system doing
its job.**

---

<a name="10-risk-config"></a>
## 10. Configuring risk management safely

All knobs live in `configs/default.yaml` under `risk:`. The defaults are
deliberately conservative. Here's what each one means:

```yaml
risk:
  initial_capital: 10000.0        # paper money to start with
  max_position_per_market: 200.0  # max $ in any single market
  max_total_exposure: 2000.0      # max $ at risk across ALL markets
  max_drawdown_pct: 0.10          # lose 10% from peak -> all trading halts
  kelly_fraction: 0.10            # bet 10% of the mathematically "optimal" bet
  max_kelly_stake_pct: 0.02       # never more than 2% of equity on one trade
  min_edge: 0.02                  # ignore opportunities smaller than 2 cents
  min_order_notional: 5.0         # Polymarket's minimum order size
```

### The concepts behind them

**Position sizing and the Kelly criterion.** There's a famous formula
(Kelly, 1956) for the mathematically optimal bet size when you have an
edge. The catch: it's only optimal if your estimate of the edge is
*perfect* — and it never is. Betting full Kelly with an overestimated edge
is a mathematically reliable way to go broke. Professionals bet a
*fraction* of Kelly. We default to 10%, **and** cap every trade at 2% of
the account, **and** cap each market at $200, **and** cap total exposure at
$2,000. Four independent brakes.

**The kill switch (`max_drawdown_pct`).** If the account drops 10% below
its highest point, the risk manager refuses all new trades and logs
`KILL SWITCH`. This is the "circuit breaker" — it turns a bad week into a
pause instead of a disaster. It does not restart on its own; you have to
investigate and restart deliberately. **Never widen this number in response
to it firing.** That is exactly the moment it's protecting you.

### Safe ranges if you ever adjust

| Setting | Safe range | Never exceed |
|---|---|---|
| `kelly_fraction` | 0.05 – 0.25 | 0.5 |
| `max_kelly_stake_pct` | 0.01 – 0.05 | 0.10 |
| `max_total_exposure` | 10–30% of capital | 50% |
| `max_drawdown_pct` | 0.05 – 0.15 | 0.20 |

Change **one** setting at a time, re-run the backtests, and write down why
you changed it. If you don't have a reason you could defend to a skeptic,
revert it.

---

<a name="11-connecting-live"></a>
## 11. Connecting a real account (when you eventually decide — not now)

**Do not do this section until Section 9 is fully satisfied.** It's
documented here so the process is never a mystery, not as an invitation.

### First: is it legal for you?

Polymarket restricts users from certain jurisdictions (historically
including the US for trading, with rules that change over time), and other
countries have their own regulations on prediction markets and
crypto. **Before creating any account, verify the current rules for your
country.** This is your responsibility and genuinely matters.

### What you'd actually need (no traditional brokerage involved)

Polymarket is not a stock market — you don't need a Demat/brokerage
account. It runs on the Polygon blockchain and uses **USDC**, a
"stablecoin" (a crypto token designed to always be worth $1). You'd need:

1. A **crypto wallet** (e.g., MetaMask) — this generates a **private key**,
   which is the master password to your funds. Anyone who has it, owns your
   money. It can never be reset. Guard it accordingly.
2. A small amount of **USDC on the Polygon network**, bought on a crypto
   exchange available in your country and withdrawn to your wallet
   (network: Polygon).
3. A Polymarket account connected to that wallet.

(The Binance data feeds QuantBot uses need **no account** — public data.)

### How QuantBot connects (the three deliberate locks)

```bash
# Lock 1: install the live-trading library (not installed by default)
pip install -e ".[live]"

# Lock 2: provide the wallet key as an environment variable (never in a file
# inside the repo, never committed to git)
export QUANTBOT_PM_PRIVATE_KEY=0x...
```

**Lock 3** is intentional friction: the live broker in
`src/quantbot/execution/live.py` refuses to start unless constructed with
`allow_live=True` in code, and no CLI flag exists to do it. Enabling live
trading is a deliberate code change you make with your own hands — it
cannot happen through a typo in a config file.

### The safe first-live protocol

1. Deposit a **tiny** amount — $50–100. Treat it as tuition, already spent.
2. Set `max_total_exposure` to that deposit and `max_position_per_market`
   to $10–20.
3. Run the *same* strategies that passed paper trading. Change nothing else.
4. For the first month, compare every live fill against what the paper
   broker would have predicted. Live doing worse than paper = stop and
   investigate.
5. Scale up slowly (2× at most per month), only while performance holds.

---

<a name="12-mistakes"></a>
## 12. Common beginner mistakes and how to avoid them

1. **Skipping straight to live trading.** The entire industry's graveyard
   is people who skipped validation. The platform makes the right path the
   easy path — follow it.
2. **Trusting a beautiful backtest.** A backtest is an audition, not proof.
   Walk-forward and paper trading are the proof. (Our mean-reversion
   strategy: backtest +5.7%, walk-forward ≈ 0. Case closed.)
3. **Cranking up risk after wins.** Winning streaks happen by chance.
   Position sizes are set by math (Section 10), not by mood.
4. **Loosening risk limits after losses** ("I just need to win it back").
   This is the single most destructive behavior in trading. The kill switch
   exists to interrupt exactly this spiral.
5. **Judging results on 10 trades.** Under ~100 trades, statistics are
   noise. Patience is a statistical requirement, not a virtue.
6. **Trying 50 strategy variants and picking the best.** The more variants
   you try, the more certain the "best" one is a fluke. Walk-forward
   defends against this — but only if you don't tune *on* the walk-forward
   results themselves. Decide, test once, accept the verdict.
7. **Ignoring costs.** The spread (gap between buy and sell price) is a
   tax on every single trade. Strategies that trade often must clear it
   every time. Always check `turnover` and re-test with doubled costs.
8. **Confusing win rate with profitability.** See Section 6. Expectancy
   and profit factor are what matter.
9. **Running code you don't understand with money you can't lose.** You
   don't need to understand every line — but you should understand every
   *decision* (this guide covers them all).
10. **Not writing things down.** Every config change, every experiment,
    every decision: one line in a personal log. The experiment tracker
    records the numbers; you record the *why*.
11. **Sharing your private key or committing it to git.** Instant,
    irreversible loss. Environment variables only, and never screenshot it.
12. **Believing "this time is different."** When paper and live diverge,
    or a strategy stops working — believe the data, not the hope.

---

<a name="13-progression"></a>
## 13. Recommended progression: beginner → confident operator

**Stage 1 — Learn the machine (weeks 1–2).** Install, run `pytest`, sync
markets, collect data, browse `markets list`. Open Polymarket in a browser
and watch a market's order book while `collect books` records it — connect
what you see to what the bot stores. Read Sections 1–6 of this guide twice.
*Goal: commands feel routine.*

**Stage 2 — Backtesting apprentice (weeks 3–4).** Run backtests on every
strategy. Deliberately overfit once: tune `entry_z` until the plain
backtest looks amazing, then run walk-forward and watch it collapse. This
one exercise will teach you more than any book. Start the book recorder
running permanently. *Goal: you can read a metrics table and spot the trap.*

**Stage 3 — Paper trader (months 2–3).** Start a paper run and leave it.
Build the daily/weekly monitoring habit (Section 8). Compare paper results
to walk-forward expectations. Iterate on research — new parameters, new
ideas, better data — while paper trading accumulates evidence. *Goal: a
month of clean paper history and a monitoring routine you actually follow.*

**Stage 4 — Decision point (month 3+).** Take the Section 9 checklist and
grade every strategy honestly. Most likely outcome: nothing qualifies yet.
That's a *successful* outcome — you now know, at zero cost, what doesn't
work. Keep researching, or decide the edge isn't there. If something
genuinely qualifies: Section 11, tiny money, treating month one as another
experiment. *Goal: a decision you can defend with numbers.*

Throughout: never let real-money steps outpace your understanding. The
platform will wait.

---

<a name="14-troubleshooting"></a>
## 14. Troubleshooting

**`quantbot: command not found`**
The venv isn't active. Run `source .venv/bin/activate` (you'll see
`(.venv)` in the prompt). Every new terminal needs this.

**`no data — run quantbot markets sync ...` when backtesting**
You haven't collected data yet, or the database is empty. Run the Section 4
steps in order.

**`markets sync` returns very few markets**
Lower `--min-liquidity` (e.g. 1000) and raise `--pages`.

**Backtest completes with `n_trades: 0`**
Usually fine, not broken: the strategies have guards (price bands, entry
thresholds) and simply found nothing worth trading in your data window.
Collect more days of history, more markets, or relax a strategy parameter
*in research* (e.g. lower `entry_z`) to confirm the pipeline fires.

**HTTP errors / timeouts during collection**
Polymarket or Binance rate-limiting or a network blip. The clients already
retry; just re-run the command — collection is resumable and never
duplicates data.

**Paper run shows only `order rejected (below_min_notional)`**
The account is small relative to the caps, or edges are tiny. Harmless —
the risk layer is refusing trades that are too small to matter.

**`KILL SWITCH: drawdown ... trading halted` in logs**
Working as intended. Trading has stopped. Read Section 10 before doing
anything. Investigate *what lost money* (`quantbot report fills <run_id>`),
then start a fresh paper run when you understand it.

**Paper runner seems frozen**
Check `logs/quantbot.log` — if the last lines are websocket/network
retries, it will recover on its own. If truly dead, Ctrl+C and restart; a
new run ID begins, old data is preserved.

**Everything is slow on Windows/WSL**
Files under `/mnt/c/...` are slow in WSL. It works, just patiently. For a
big speedup, keep a virtualenv on the Linux side (e.g.
`python3 -m venv ~/.venvs/quantbot`) and use that to run commands; the
project files can stay where they are.

**`pytest` failures after you edited code**
The tests are the safety net — a failure means a real behavior changed.
`git diff` to see what you changed; `git checkout -- <file>` to undo.

**Where is everything stored?**
Markets/fills/books: `data/quantbot.db` (SQLite). Price history/candles:
`data/parquet/`. Experiments: `experiments/runs/*.json`. Logs:
`logs/quantbot.log`. Delete `data/` and you start clean (samples in
`data/samples/` are committed and survive).

---

<a name="15-best-practices"></a>
## 15. Best practices for operating and maintaining the system

**Version control everything except secrets.** Commit config changes with
a message saying *why*. Never commit `.env`, keys, or `data/` (the
`.gitignore` already protects these).

**One change at a time.** Config, strategy parameter, or code — change one
thing, re-run tests (`pytest`), re-run the relevant backtest, then decide.
Two simultaneous changes = you learn nothing from the result.

**Keep a decision log.** A plain text file: date, what you changed, why,
what you expected, what happened. Six months from now this file is worth
more than any strategy.

**Back up the data directory.** `data/` accumulates irreplaceable
order-book recordings (nobody else has them). Copy it somewhere weekly:
`cp -r data/ ~/backups/quantbot-data-$(date +%F)/`

**Check the logs even when things work.** Once a week, skim
`grep -E "ERROR|WARNING" logs/quantbot.log | tail -50`. Silent, repeated
warnings are how small problems become big ones.

**Update dependencies deliberately, not automatically.** When you do:
`pip install -e ".[dev]" --upgrade`, then `pytest`, then a short paper run
before trusting it.

**Re-validate after any Polymarket change.** If collection starts erroring
or numbers look odd, the API may have changed. The connector tests
(`pytest tests/test_data.py`) and a small `markets sync` are the health
check.

**Respect the kill switch and the checklist.** The system's value is that
it makes discipline automatic. Every manual override you invent removes a
layer of protection that was put there deliberately.

**When in doubt, stay in paper.** The cost of paper trading for another
month: $0. The cost of going live a month too early: potentially all of it.

---

<a name="16-glossary"></a>
## 16. Glossary

| Term | Meaning |
|---|---|
| **Backtest** | Replaying a strategy on historical data to see how it *would have* done. |
| **Confidence interval (CI)** | A statistical range for the "true" value of a measurement. If a Sharpe CI includes 0, the profit might be pure luck. |
| **Drawdown** | The drop from an account's highest point to a later low. "Max drawdown" is the worst one. |
| **Edge** | The amount by which the odds are genuinely in your favor. The thing every strategy claims to have and few actually do. |
| **Equity** | Total account value: cash + current value of all positions. |
| **Fair value** | What a contract is *probably actually worth*, per a model — as opposed to its current price. |
| **Fill** | A completed trade (your order matched with someone else's). |
| **Kelly criterion** | Formula for the mathematically optimal bet size given an edge. Dangerous at full strength; always used fractionally. |
| **Kill switch** | Automatic halt of all trading when losses exceed a preset limit. |
| **Liquidity** | How much money is available to trade against. High liquidity = easy to enter/exit at fair prices. |
| **Order book** | The live list of all outstanding buy offers (bids) and sell offers (asks) for a contract. |
| **Overfitting** | Tuning a strategy until it perfectly fits past data — and thereby learns the past's random noise instead of anything real. |
| **Paper trading** | Trading live markets with simulated money. |
| **Position** | The contracts you currently hold. |
| **Resolution** | The moment a prediction market's question is answered and contracts pay out $1 or $0. |
| **Sharpe ratio** | Return per unit of risk. The standard scorecard for strategies. |
| **Signal** | A strategy's proposal to trade, before risk checks. |
| **Slippage** | The difference between the price you expected and the price you actually got. |
| **Spread** | The gap between the best buying price and best selling price. A cost you pay on every round trip. |
| **USDC** | A crypto token pegged to $1, used as the currency on Polymarket. |
| **Walk-forward test** | Repeatedly tuning on one slice of history and testing on the *next, unseen* slice. The strongest defense against overfitting. |

---

*Final word: this platform's first real accomplishment was proving that its
own most promising strategy wasn't statistically real — before any money
was involved. That is the system working. Let it keep working for you: be
patient, follow the stages, and let the numbers make the decisions.*
