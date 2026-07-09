"""Tests for the trading-desk layer: decisions, lifecycle, daily reports, API."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from quantbot.config import AppConfig, StorageConfig, StrategyConfig
from quantbot.core.decisions import DecisionRecord, TradeCloseReport
from quantbot.data.storage import Store
from quantbot.experiments.tracker import ExperimentTracker
from quantbot.lifecycle import Stage, evaluate_strategy
from quantbot.reporting.daily import build_daily_report, write_daily_report

NOW = datetime(2026, 7, 9, 10, 0, tzinfo=timezone.utc)


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(StorageConfig(root=tmp_path))


def _decision(**over) -> DecisionRecord:
    base = dict(
        run_id="r1", strategy="mean_reversion", condition_id="c1",
        token_id="t1", market_question="Test market?", side="BUY",
        signal_edge=0.05, signal_confidence=0.8, outcome="filled",
        fair_value=0.6, fair_value_std=0.05, ts=NOW,
    )
    base.update(over)
    return DecisionRecord(**base)


def _close_report(**over) -> TradeCloseReport:
    base = dict(
        run_id="r1", token_id="t1", condition_id="c1",
        market_question="Test market?", strategy="mean_reversion",
        entry_ts=NOW - timedelta(hours=5), entry_price=0.5, exit_price=0.6,
        size=100.0, pnl=10.0, fees=0.1, holding_hours=5.0,
        exit_reason="opposite_signal", ts=NOW,
    )
    base.update(over)
    return TradeCloseReport(**base)


def test_decision_roundtrip(store: Store):
    d = _decision()
    store.save_decision(d)
    loaded = store.load_decisions("r1")
    assert len(loaded) == 1
    assert loaded[0]["decision_id"] == d.decision_id
    assert loaded[0]["fair_value"] == 0.6
    assert "exit_policy" in loaded[0]


def test_trade_report_roundtrip(store: Store):
    r = _close_report()
    store.save_trade_report(r)
    loaded = store.load_trade_reports("r1")
    assert len(loaded) == 1 and loaded[0]["pnl"] == 10.0


def test_runner_state_roundtrip(store: Store):
    store.save_runner_state("run_a", {"cash": 9500.0, "positions": {}})
    rid, state = store.load_runner_state()
    assert rid == "run_a" and state["cash"] == 9500.0


def test_equity_history_roundtrip(store: Store):
    for i in range(3):
        store.save_equity_point("r1", NOW + timedelta(minutes=i), 10000 + i, 9000, 500)
    df = store.load_equity_history("r1")
    assert len(df) == 3 and df["equity"].iloc[-1] == 10002


# ---------------- lifecycle ----------------
def test_lifecycle_stages(store: Store, tmp_path: Path):
    tracker = ExperimentTracker(root=tmp_path / "runs")
    # nothing known -> research (or paper if enabled)
    s = evaluate_strategy("momentum", store, tracker, enabled_in_paper=False)
    assert s.stage == Stage.RESEARCH
    s = evaluate_strategy("momentum", store, tracker, enabled_in_paper=True)
    assert s.stage == Stage.PAPER
    # with backtest runs -> backtesting
    tracker.log_run("backtest", "obi", {}, {"sharpe": 1.0})
    s = evaluate_strategy("obi", store, tracker, enabled_in_paper=False)
    assert s.stage == Stage.BACKTESTING
    # never auto-live: even a perfect strategy stops at candidate
    tracker.log_run("walkforward", "obi", {}, {
        "sharpe": 2.0, "sharpe_ci_low": 0.5, "n_trades": 500,
    })
    from quantbot.core.types import Fill, Side
    for i in range(120):
        store.save_fill(
            Fill(order_id=f"o{i}", token_id="t", condition_id="c", side=Side.BUY,
                 price=0.5, size=10, ts=NOW - timedelta(days=20) + timedelta(hours=i * 4),
                 strategy="obi"),
            run_id="paper_x",
        )
    s = evaluate_strategy("obi", store, tracker, enabled_in_paper=True)
    assert s.stage == Stage.CANDIDATE  # NOT live
    assert not [c for c in s.criteria if c.name == "manual_live_approval"][0].met


# ---------------- daily report ----------------
def test_daily_report(store: Store, tmp_path: Path):
    day = NOW.date()
    for i in range(5):
        store.save_equity_point("r1", NOW + timedelta(hours=i), 10000 - i * 20, 9000, 500)
    store.save_decision(_decision())
    store.save_decision(_decision(outcome="rejected", risk_reason="per_market_limit",
                                  signal_edge=0.08))
    store.save_trade_report(_close_report(pnl=25.0))
    store.save_trade_report(_close_report(pnl=-12.0, strategy="obi"))
    rep = build_daily_report(store, day)
    assert rep["activity"]["decisions"] == 2
    assert rep["activity"]["trades_closed"] == 2
    assert rep["best_trade"]["pnl"] == 25.0
    assert rep["worst_trade"]["pnl"] == -12.0
    assert len(rep["missed_opportunities"]) == 1
    assert rep["leaderboard"][0]["strategy"] == "mean_reversion"
    path = write_daily_report(store, out_dir=tmp_path / "reports", day=day)
    assert path.exists() and "Daily Report" in path.read_text()


# ---------------- API ----------------
@pytest.fixture
def client(store: Store) -> TestClient:
    from quantbot.api.server import create_app

    cfg = AppConfig(strategies=[StrategyConfig(name="mean_reversion")])
    app = create_app(cfg, store, with_paper=False)  # observe mode: no runner
    return TestClient(app)


def test_api_endpoints(client: TestClient, store: Store):
    store.save_decision(_decision())
    store.save_trade_report(_close_report())
    for i in range(4):
        store.save_equity_point("r1", NOW + timedelta(hours=i), 10000 + i * 5, 9000, 100)

    assert client.get("/api/health").json()["mode"] == "observe"
    assert len(client.get("/api/decisions").json()) == 1
    d = client.get("/api/decisions").json()[0]
    assert client.get(f"/api/decisions/{d['decision_id']}").json()["strategy"] == "mean_reversion"
    assert len(client.get("/api/trades").json()) == 1
    eq = client.get("/api/equity", params={"run_id": "r1"}).json()
    assert len(eq) == 4
    strategies = client.get("/api/strategies").json()
    names = {s["name"] for s in strategies}
    assert "mean_reversion" in names and all("stage" in s for s in strategies)
    assert client.get("/api/report/daily", params={"day": str(NOW.date())}).json()["date"] == str(NOW.date())
    assert client.get("/api/summary").json()["risk_limits"]["max_drawdown_pct"] == 0.10
    # frontend served
    assert "QuantBot" in client.get("/").text


def test_api_ws_snapshot(client: TestClient):
    with client.websocket_connect("/ws") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "snapshot"
        assert "markets" in msg["data"]
