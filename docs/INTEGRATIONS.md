# Integrations — TradingView MCP (technical analysis layer)

## What it provides

[tradingview-mcp](https://github.com/atilaahmettaner/tradingview-mcp)
(vendored at `~/vendor/tradingview-mcp`, MIT) exposes ~36 analysis tools
over TradingView-derived public data: per-symbol technical ratings and
indicators (RSI, MACD, Bollinger, ADX, EMA/SMA…), market screeners
(gainers/losers/ratings), Bollinger-squeeze scans, sentiment/news, and
multi-asset snapshots. **No TradingView account or API key is required.**

## How it is connected (two paths, one boundary)

```
                       ┌──────────────────────────────┐
  deterministic engine │ quantbot.analysis.technical  │  in-process calls to the
  (runner, decisions)──▶ TradingViewProvider          │  server's *service layer*
                       │  (behind a Protocol)         │  (tradingview_ta + screener)
                       └──────────────────────────────┘
  AI copilot           ┌──────────────────────────────┐
  (Claude sessions) ───▶ .mcp.json → tradingview-mcp  │  real MCP server, own
                       │  server (36 tools, uv env)   │  isolated environment
                       └──────────────────────────────┘
```

* **Engine path** — `src/quantbot/analysis/technical.py` defines
  `TechnicalContextProvider` (a Protocol). The default implementation calls
  the vendored package's service functions **in-process** (reliable,
  cache-TTL'd, testable). The MCP *protocol* dependency conflicts with
  QuantBot's FastAPI stack, so QuantBot installs the package `--no-deps`
  plus only its data libraries (`tradingview-ta`, `tradingview-screener==3.0.0`)
  — the MCP server itself runs in its own `uv` environment.
* **Copilot path** — `.mcp.json` registers the full MCP server for Claude
  Code sessions in this repo, so the AI assistant can answer "what looks
  strongest right now?" with live screener data.

## What it is used for

* Every engine cycle refreshes: per-symbol snapshots (recommendation, RSI,
  MACD histogram, Bollinger width, ADX) for the configured crypto symbols,
  plus `bollinger_squeeze` and `top_gainers` scans.
* Snapshots are attached to **decision records** (`technical_context`
  field) for any market whose underlying symbol matches — so every signal
  remains explainable with the technical evidence that existed at the time.
* Strategies receive it read-only via `MarketView.extra["technical"]`.
  Whether technical agreement improves any strategy is hypothesis **H8**
  in `docs/RESEARCH.md` — to be tested, not assumed.
* The dashboard's **Market context** panel shows live snapshots and ranked
  scan output; everything is logged at INFO level.

## What it is NOT allowed to do

* It cannot place, size, modify, or cancel orders — providers have no
  reference to the broker, risk engine, or portfolio.
* It cannot veto or approve trades — risk verdicts come only from
  `RiskManager`.
* It does not participate in the promotion pipeline.
* Provider failures degrade to "no context" and never halt trading (unlike
  the market-data failsafe, which protects execution-critical feeds).

## How to disable it safely

`configs/default.yaml` → `analysis.enabled: false` (or env
`QUANTBOT_ANALYSIS__ENABLED=false`). The engine runs identically; the
dashboard panel simply goes quiet. Uninstalling the packages has the same
effect (the factory degrades to a NullProvider with a warning).

## How to test locally

```bash
pytest tests/test_analysis.py           # unit tests, no network
python - <<'EOF'                        # live smoke test
import asyncio
from quantbot.analysis.technical import build_provider
p = build_provider(True)
print(asyncio.run(p.snapshot("BTCUSDT", "1h")))
print(asyncio.run(p.scan("bollinger_squeeze", timeframe="4h", limit=3)))
EOF
```

## Replacing the provider

Implement `TechnicalContextProvider` (two async methods: `snapshot`,
`scan` returning the normalized row schema `{symbol, change_pct, rsi, bbw,
close}`), register it in `build_provider`, and set `analysis.provider` in
config. Nothing else in the codebase references TradingView.

## Setup from scratch (documented install path for this codebase)

```bash
# 1. vendor the repo (outside the project tree)
git clone https://github.com/atilaahmettaner/tradingview-mcp ~/vendor/tradingview-mcp
# 2. engine path: data libs only (avoids the mcp[cli] dependency conflict)
pip install tradingview-ta "tradingview-screener==3.0.0" requests
pip install --no-deps -e ~/vendor/tradingview-mcp
# 3. copilot path (optional): uv manages the server's own isolated env
#    .mcp.json in the repo root is already configured; requires `uv`.
```
