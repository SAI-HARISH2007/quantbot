"""QuantBot dashboard server: REST + WebSocket + static frontend.

Runs the paper trading engine in-process (unless --observe) so the UI
reflects live state with no polling database roundtrips. Every runner event
fans out to connected WebSocket clients; REST endpoints serve history and
analytics from the store.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from quantbot.analytics.metrics import compute_report, periods_per_year
from quantbot.config import AppConfig
from quantbot.data.storage import Store
from quantbot.experiments.tracker import ExperimentTracker
from quantbot.lifecycle import evaluate_all
from quantbot.reporting.daily import build_daily_report, write_daily_report

logger = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).parent / "static"


class Hub:
    """WebSocket fan-out + rolling in-memory state for instant snapshots."""

    def __init__(self) -> None:
        self.clients: set[WebSocket] = set()
        self.markets: dict[str, dict] = {}
        self.status: dict = {}
        self.events: deque[dict] = deque(maxlen=400)   # signals/orders/fills/alerts
        self.logs: deque[dict] = deque(maxlen=300)
        self.thinking: deque[dict] = deque(maxlen=60)

    async def emit(self, etype: str, data: dict) -> None:
        msg = {"type": etype, "data": data,
               "ts": datetime.now(timezone.utc).isoformat()}
        if etype == "market":
            self.markets[data["condition_id"]] = data
        elif etype == "status":
            self.status = data
        elif etype in ("signal", "order", "fill", "alert", "decision", "trade_closed"):
            self.events.appendleft(msg)
        elif etype == "log":
            self.logs.appendleft(msg)
        elif etype == "thinking":
            self.thinking.appendleft(msg)
        dead = []
        for ws in self.clients:
            try:
                await ws.send_text(json.dumps(msg, default=str))
            except Exception:  # noqa: BLE001
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)

    def snapshot(self) -> dict:
        return {
            "type": "snapshot",
            "data": {
                "status": self.status,
                "markets": list(self.markets.values()),
                "events": list(self.events)[:150],
                "logs": list(self.logs)[:100],
                "thinking": list(self.thinking)[:40],
            },
        }


class HubLogHandler(logging.Handler):
    """Forwards log records into the hub (thread-safe via loop scheduling)."""

    def __init__(self, hub: Hub, loop: asyncio.AbstractEventLoop):
        super().__init__(level=logging.INFO)
        self.hub = hub
        self.loop = loop

    def emit(self, record: logging.LogRecord) -> None:
        try:
            etype = "alert" if record.levelno >= logging.WARNING else "log"
            data = {"level": record.levelname.lower(), "logger": record.name,
                    "message": record.getMessage()}
            asyncio.run_coroutine_threadsafe(self.hub.emit(etype, data), self.loop)
        except Exception:  # noqa: BLE001
            pass


def create_app(
    cfg: AppConfig,
    store: Store,
    with_paper: bool = True,
    top: int = 10,
    poll: float = 30.0,
    resume: bool = True,
) -> FastAPI:
    hub = Hub()
    runner_ref: dict = {"runner": None}

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        loop = asyncio.get_running_loop()
        handler = HubLogHandler(hub, loop)
        logging.getLogger("quantbot").addHandler(handler)
        tasks: list[asyncio.Task] = []
        if with_paper:
            from quantbot.cli import _default_fair_value
            from quantbot.runner.paper import PaperRunner
            from quantbot.strategies.registry import build_strategies

            markets = sorted(
                store.load_markets(active_only=True), key=lambda m: -m.liquidity
            )[:top]
            strategies = build_strategies(cfg.strategies)
            runner = PaperRunner(
                cfg, store, strategies, fair_value=_default_fair_value(),
                poll_seconds=poll, sink=hub.emit, resume=resume,
            )
            runner_ref["runner"] = runner

            async def _run_forever() -> None:
                # crash recovery: if the loop dies, log, wait, resume state
                while True:
                    try:
                        await runner.run(markets)
                        break  # clean exit
                    except asyncio.CancelledError:
                        raise
                    except Exception:  # noqa: BLE001
                        logger.exception("paper runner crashed; restarting in 30s")
                        await hub.emit("alert", {
                            "level": "critical",
                            "message": "Paper runner crashed — auto-restarting in 30s.",
                        })
                        await asyncio.sleep(30)

            tasks.append(asyncio.create_task(_run_forever()))

        async def _daily_report_scheduler() -> None:
            last_day = datetime.now(timezone.utc).date()
            while True:
                await asyncio.sleep(60)
                today = datetime.now(timezone.utc).date()
                if today != last_day:
                    try:
                        path = write_daily_report(store, day=last_day)
                        await hub.emit("alert", {
                            "level": "info",
                            "message": f"Daily report generated: {path.name}",
                        })
                    except Exception:  # noqa: BLE001
                        logger.exception("daily report generation failed")
                    last_day = today

        tasks.append(asyncio.create_task(_daily_report_scheduler()))
        try:
            yield
        finally:
            for t in tasks:
                t.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.gather(*tasks)
            logging.getLogger("quantbot").removeHandler(handler)

    app = FastAPI(title="QuantBot", lifespan=lifespan)

    # ------------------------------------------------------------- ws
    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket) -> None:
        await ws.accept()
        hub.clients.add(ws)
        await ws.send_text(json.dumps(hub.snapshot(), default=str))
        try:
            while True:
                await ws.receive_text()  # keepalive pings from client
        except WebSocketDisconnect:
            hub.clients.discard(ws)

    # ------------------------------------------------------------- rest
    @app.get("/api/summary")
    def summary() -> dict:
        runner = runner_ref["runner"]
        status = runner.status() if runner else hub.status or {}
        eq = store.load_equity_history(status.get("run_id"))
        perf = {}
        if len(eq) > 2:
            import pandas as pd

            s = pd.Series(
                eq["equity"].to_numpy(),
                index=pd.DatetimeIndex(pd.to_datetime(eq["ts"], utc=True)),
            )
            now = s.index[-1]
            for label, delta in (("daily", "1D"), ("weekly", "7D"), ("monthly", "30D")):
                past = s[s.index >= now - pd.Timedelta(delta)]
                if len(past) > 1 and past.iloc[0] > 0:
                    perf[label] = float(past.iloc[-1] / past.iloc[0] - 1.0)
        return {
            "status": status,
            "performance": perf,
            "risk_limits": {
                "max_total_exposure": cfg.risk.max_total_exposure,
                "max_position_per_market": cfg.risk.max_position_per_market,
                "max_drawdown_pct": cfg.risk.max_drawdown_pct,
                "kelly_fraction": cfg.risk.kelly_fraction,
                "max_kelly_stake_pct": cfg.risk.max_kelly_stake_pct,
            },
        }

    @app.get("/api/equity")
    def equity(run_id: Optional[str] = None) -> list[dict]:
        runner = runner_ref["runner"]
        rid = run_id or (runner.run_id if runner else None)
        df = store.load_equity_history(rid)
        return df.to_dict(orient="records")  # type: ignore[return-value]

    @app.get("/api/metrics")
    def metrics(run_id: Optional[str] = None) -> dict:
        runner = runner_ref["runner"]
        rid = run_id or (runner.run_id if runner else None)
        eq = store.load_equity_history(rid)
        if len(eq) < 3:
            return {}
        import pandas as pd

        s = pd.Series(
            eq["equity"].to_numpy(),
            index=pd.DatetimeIndex(pd.to_datetime(eq["ts"], utc=True)),
        )
        s = s[~s.index.duplicated(keep="last")]
        closes = store.load_trade_reports(rid, limit=1000)
        trades = pd.DataFrame(closes) if closes else None
        if trades is not None and len(trades):
            trades["notional"] = trades["size"] * trades["entry_price"]
        rep = compute_report(s, trades, initial_capital=float(s.iloc[0]))
        d = rep.to_dict()
        # rolling sharpe (24h window) for the chart
        rets = s.pct_change().dropna()
        if len(rets) > 10:
            ppy = periods_per_year(s.index)
            roll = (rets.rolling("24h").mean() / rets.rolling("24h").std()) * (ppy ** 0.5)
            d["rolling_sharpe"] = [
                {"ts": str(t), "v": (None if pd.isna(v) else float(v))}
                for t, v in roll.items()
            ][-500:]
        return d

    @app.get("/api/decisions")
    def decisions(limit: int = 100, run_id: Optional[str] = None) -> list[dict]:
        return store.load_decisions(run_id, limit=limit)

    @app.get("/api/decisions/{decision_id}")
    def decision(decision_id: str) -> dict:
        for d in store.load_decisions(limit=2000):
            if d["decision_id"] == decision_id:
                return d
        return {}

    @app.get("/api/trades")
    def trades(limit: int = 100, run_id: Optional[str] = None) -> list[dict]:
        return store.load_trade_reports(run_id, limit=limit)

    @app.get("/api/strategies")
    def strategies() -> list[dict]:
        enabled = [s.name for s in cfg.strategies if s.enabled]
        statuses = evaluate_all(store, ExperimentTracker(), enabled)
        # leaderboard from close reports
        closes = store.load_trade_reports(limit=1000)
        agg: dict[str, dict] = {}
        for r in closes:
            a = agg.setdefault(r["strategy"], {"trades": 0, "pnl": 0.0, "wins": 0, "fees": 0.0})
            a["trades"] += 1
            a["pnl"] += r["pnl"]
            a["fees"] += r["fees"]
            a["wins"] += 1 if r["pnl"] > 0 else 0
        for s in statuses:
            a = agg.get(s["name"], {})
            s["trades"] = a.get("trades", 0)
            s["pnl"] = a.get("pnl", 0.0)
            s["fees"] = a.get("fees", 0.0)
            s["win_rate"] = a["wins"] / a["trades"] if a.get("trades") else None
            s["enabled"] = s["name"] in enabled
        return statuses

    @app.get("/api/experiments")
    def experiments() -> list[dict]:
        return ExperimentTracker().list_runs()[-50:]

    @app.get("/api/report/daily")
    def daily_report(day: Optional[str] = None) -> dict:
        from datetime import date as _date

        d = _date.fromisoformat(day) if day else None
        return build_daily_report(store, d)

    @app.get("/api/health")
    def health() -> dict:
        runner = runner_ref["runner"]
        now = datetime.now(timezone.utc)
        stale = None
        if runner and runner.last_cycle_at:
            stale = (now - runner.last_cycle_at).total_seconds()
        mkts = store.load_markets(active_only=True)
        return {
            "ok": runner is not None and (stale is None or stale < poll * 4),
            "mode": "paper" if with_paper else "observe",
            "runner_alive": runner is not None,
            "seconds_since_last_cycle": stale,
            "kill_switch": runner.risk.halted if runner else None,
            "markets_tracked": len(mkts),
            "ws_clients": len(hub.clients),
            "server_time": now.isoformat(),
        }

    # ------------------------------------------------------------- static
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    return app
