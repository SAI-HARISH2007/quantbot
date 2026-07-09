"""End-of-day report generator.

Builds a structured JSON + human-readable Markdown report for one UTC day
from the store's decisions, fills, close reports, and equity history. The
dashboard renders the JSON; the Markdown lands in reports/ for the archive.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from quantbot.data.storage import Store

logger = logging.getLogger(__name__)


def build_daily_report(store: Store, day: Optional[date] = None) -> dict:
    day = day or datetime.now(timezone.utc).date()
    start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    end = start + timedelta(days=1)

    eq = store.load_equity_history()
    day_eq = eq[(eq["ts"] >= start.isoformat()) & (eq["ts"] < end.isoformat())] if len(eq) else eq
    equity_start = float(day_eq["equity"].iloc[0]) if len(day_eq) else None
    equity_end = float(day_eq["equity"].iloc[-1]) if len(day_eq) else None
    day_return = (
        equity_end / equity_start - 1.0 if equity_start and equity_end else None
    )
    max_dd = 0.0
    if len(day_eq) > 1:
        peak = day_eq["equity"].cummax()
        max_dd = float((1 - day_eq["equity"] / peak).max())

    fills = store.load_all_fills(since_iso=start.isoformat())
    fills = fills[fills["ts"] < end.isoformat()] if len(fills) else fills

    closes = [
        r for r in store.load_trade_reports(limit=1000)
        if start.isoformat() <= r["ts"] < end.isoformat()
    ]
    best = max(closes, key=lambda r: r["pnl"], default=None)
    worst = min(closes, key=lambda r: r["pnl"], default=None)

    decisions = [
        d for d in store.load_decisions(limit=2000)
        if start.isoformat() <= d["ts"] < end.isoformat()
    ]
    rejected = [d for d in decisions if d["outcome"] == "rejected"]
    reject_reasons: dict[str, int] = {}
    for d in rejected:
        reject_reasons[d.get("risk_reason", "?")] = reject_reasons.get(
            d.get("risk_reason", "?"), 0) + 1
    # "missed opportunities": rejected purely by exposure limits with real edge
    missed = sorted(
        (d for d in rejected if d.get("risk_reason") in
         ("per_market_limit", "total_exposure_limit") and (d.get("signal_edge") or 0) > 0.02),
        key=lambda d: -(d.get("signal_edge") or 0),
    )[:5]

    strat: dict[str, dict] = {}
    for r in closes:
        s = strat.setdefault(r["strategy"], {"trades": 0, "pnl": 0.0, "wins": 0})
        s["trades"] += 1
        s["pnl"] += r["pnl"]
        s["wins"] += 1 if r["pnl"] > 0 else 0
    leaderboard = sorted(
        ({"strategy": k, **v, "win_rate": v["wins"] / v["trades"] if v["trades"] else 0}
         for k, v in strat.items()),
        key=lambda s: -s["pnl"],
    )

    risk_events = [d for d in rejected if d.get("risk_reason") == "kill_switch_active"]
    suggestions: list[str] = []
    if reject_reasons.get("below_min_notional", 0) > 10:
        suggestions.append(
            "Many signals sized below the $5 minimum — edges are too small for "
            "current capital/limits; this is the risk layer working, not a bug.")
    if missed:
        suggestions.append(
            "Exposure caps rejected signals with real edge — do NOT raise caps "
            "reactively; review whether concentration in few markets is the cause.")
    if closes and sum(r["fees"] for r in closes) > abs(sum(r["pnl"] for r in closes)) * 0.5:
        suggestions.append("Fees+slippage consumed >50% of gross PnL — strategies may be overtrading.")
    if not decisions:
        suggestions.append("No decisions today — check that the paper runner and data feeds are alive.")

    return {
        "date": day.isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "portfolio": {
            "equity_start": equity_start,
            "equity_end": equity_end,
            "day_return": day_return,
            "max_intraday_drawdown": max_dd,
        },
        "activity": {
            "decisions": len(decisions),
            "orders_filled": len(fills),
            "trades_closed": len(closes),
            "signals_rejected": len(rejected),
            "reject_reasons": reject_reasons,
        },
        "leaderboard": leaderboard,
        "best_trade": best,
        "worst_trade": worst,
        "missed_opportunities": [
            {"question": d["market_question"], "strategy": d["strategy"],
             "edge": d["signal_edge"], "reason": d["risk_reason"]}
            for d in missed
        ],
        "risk_events": {
            "kill_switch_rejections": len(risk_events),
        },
        "system_health": {
            "equity_points_recorded": len(day_eq),
            "data_gap_detected": len(day_eq) == 0,
        },
        "suggestions": suggestions,
    }


def render_markdown(report: dict) -> str:
    p = report["portfolio"]
    a = report["activity"]
    lines = [
        f"# Daily Report — {report['date']}",
        "",
        "## Portfolio",
        f"- Equity: {p['equity_start']} → {p['equity_end']}"
        + (f" ({p['day_return']:+.2%})" if p["day_return"] is not None else ""),
        f"- Max intraday drawdown: {p['max_intraday_drawdown']:.2%}",
        "",
        "## Activity",
        f"- Decisions evaluated: {a['decisions']}  |  Fills: {a['orders_filled']}  |  "
        f"Closed trades: {a['trades_closed']}  |  Rejected: {a['signals_rejected']}",
        f"- Rejection reasons: {a['reject_reasons']}",
        "",
        "## Strategy leaderboard",
    ]
    for s in report["leaderboard"] or []:
        lines.append(
            f"- **{s['strategy']}**: {s['trades']} trades, PnL ${s['pnl']:.2f}, "
            f"win rate {s['win_rate']:.0%}")
    if report["best_trade"]:
        b = report["best_trade"]
        lines += ["", f"**Best trade:** {b['strategy']} on \"{b['market_question'][:60]}\" "
                      f"→ ${b['pnl']:.2f}"]
    if report["worst_trade"]:
        w = report["worst_trade"]
        lines += [f"**Worst trade:** {w['strategy']} on \"{w['market_question'][:60]}\" "
                  f"→ ${w['pnl']:.2f}"]
    if report["missed_opportunities"]:
        lines += ["", "## Missed opportunities (rejected by exposure limits)"]
        lines += [f"- {m['strategy']}: edge {m['edge']:.3f} on \"{m['question'][:60]}\" "
                  f"({m['reason']})" for m in report["missed_opportunities"]]
    lines += ["", "## Suggestions"]
    lines += [f"- {s}" for s in (report["suggestions"] or ["None — clean day."])]
    return "\n".join(lines)


def write_daily_report(store: Store, out_dir: Path = Path("reports"),
                       day: Optional[date] = None) -> Path:
    report = build_daily_report(store, day)
    out_dir.mkdir(exist_ok=True)
    (out_dir / f"{report['date']}.json").write_text(json.dumps(report, indent=2, default=str))
    path = out_dir / f"{report['date']}.md"
    path.write_text(render_markdown(report))
    logger.info("daily report written: %s", path)
    return path
