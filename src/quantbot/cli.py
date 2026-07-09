"""quantbot CLI — every workflow is reachable from here.

    quantbot markets sync          # discover & store Polymarket markets
    quantbot collect history       # pull PM price history
    quantbot collect crypto        # pull BTC/ETH candles
    quantbot backtest run <strat>  # single backtest + report
    quantbot backtest walkforward <strat>
    quantbot paper run             # live paper trading
    quantbot report runs           # list experiment runs
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from quantbot.config import AppConfig, load_config
from quantbot.logging_setup import setup_logging

app = typer.Typer(help="Polymarket quantitative trading platform")
markets_app = typer.Typer(help="Market discovery")
collect_app = typer.Typer(help="Data collection")
backtest_app = typer.Typer(help="Backtesting & walk-forward")
paper_app = typer.Typer(help="Paper trading")
report_app = typer.Typer(help="Reports & experiments")
app.add_typer(markets_app, name="markets")
app.add_typer(collect_app, name="collect")
app.add_typer(backtest_app, name="backtest")
app.add_typer(paper_app, name="paper")
app.add_typer(report_app, name="report")

console = Console()
_CONFIG_OPT = typer.Option(None, "--config", "-c", help="Path to YAML config")


def _boot(config: Optional[Path]) -> AppConfig:
    cfg = load_config(config)
    setup_logging(cfg.log_level)
    return cfg


def _store(cfg: AppConfig):
    from quantbot.data.storage import Store

    return Store(cfg.storage)


def _default_fair_value():
    from quantbot.fairvalue.digital_option import DigitalOptionModel
    from quantbot.fairvalue.ensemble import EnsembleFairValue
    from quantbot.fairvalue.market_models import MicropriceModel, TimeWeightedModel

    return EnsembleFairValue(
        [DigitalOptionModel(), MicropriceModel(), TimeWeightedModel()]
    )


# ---------------------------------------------------------------- markets
@markets_app.command("sync")
def markets_sync(
    config: Optional[Path] = _CONFIG_OPT,
    min_liquidity: float = typer.Option(1000.0, help="Skip thin markets"),
    pages: int = typer.Option(10, help="Gamma pages to scan (100 markets each)"),
):
    """Discover active Polymarket binary markets and store metadata."""
    cfg = _boot(config)
    from quantbot.data.collector import Collector

    async def _run():
        c = Collector(cfg, _store(cfg))
        n = await c.sync_markets(min_liquidity=min_liquidity, max_pages=pages)
        await c.close()
        console.print(f"[green]synced {n} markets[/green]")

    asyncio.run(_run())


@markets_app.command("list")
def markets_list(config: Optional[Path] = _CONFIG_OPT, limit: int = 20):
    """Show stored markets, most liquid first."""
    cfg = _boot(config)
    ms = sorted(_store(cfg).load_markets(active_only=True), key=lambda m: -m.liquidity)
    t = Table(title="Active markets")
    for col in ("question", "liquidity", "volume", "end date"):
        t.add_column(col)
    for m in ms[:limit]:
        t.add_row(
            m.question[:70], f"{m.liquidity:,.0f}", f"{m.volume:,.0f}",
            str(m.end_date.date()) if m.end_date else "-",
        )
    console.print(t)


# ---------------------------------------------------------------- collect
@collect_app.command("history")
def collect_history(
    config: Optional[Path] = _CONFIG_OPT,
    days: int = 30,
    fidelity: int = typer.Option(10, help="Bar width in minutes"),
):
    """Pull Polymarket price history for all stored markets."""
    cfg = _boot(config)
    from quantbot.data.collector import Collector

    async def _run():
        c = Collector(cfg, _store(cfg))
        n = await c.collect_price_history(days=days, fidelity_minutes=fidelity)
        await c.close()
        console.print(f"[green]collected {n} price points[/green]")

    asyncio.run(_run())


@collect_app.command("crypto")
def collect_crypto(config: Optional[Path] = _CONFIG_OPT, days: int = 30, interval: str = "1m"):
    """Pull BTC/ETH candles from Binance."""
    cfg = _boot(config)
    from quantbot.data.collector import Collector

    async def _run():
        c = Collector(cfg, _store(cfg))
        n = await c.collect_crypto(days=days, interval=interval)
        await c.close()
        console.print(f"[green]collected {n} candles[/green]")

    asyncio.run(_run())


@collect_app.command("books")
def collect_books(
    config: Optional[Path] = _CONFIG_OPT,
    interval: float = 30.0,
    duration: float = typer.Option(0, help="Seconds to record (0 = forever)"),
    top: int = typer.Option(20, help="Record books for top-N liquid markets"),
):
    """Continuously snapshot order books (needed by OBI research)."""
    cfg = _boot(config)
    from quantbot.data.collector import Collector

    async def _run():
        store = _store(cfg)
        ms = sorted(store.load_markets(active_only=True), key=lambda m: -m.liquidity)[:top]
        tokens = [t for m in ms for t in (m.yes_token_id, m.no_token_id)]
        c = Collector(cfg, store)
        await c.run_book_recorder(tokens, interval_seconds=interval, duration_seconds=duration)
        await c.close()

    asyncio.run(_run())


# ---------------------------------------------------------------- backtest
def _load_backtest_data(cfg: AppConfig):
    from quantbot.backtest.engine import BacktestData

    store = _store(cfg)
    markets = {m.condition_id: m for m in store.load_markets()}
    prices = {}
    for cid, m in markets.items():
        df = store.load_price_history(m.yes_token_id)
        if len(df) >= 30:
            prices[cid] = df
    candles = {s: store.load_candles(s) for s in cfg.crypto.symbols}
    candles = {s: df for s, df in candles.items() if len(df)}
    return BacktestData(markets=markets, prices=prices, candles=candles)


def _print_report(rep) -> None:
    t = Table(title="Performance")
    t.add_column("metric")
    t.add_column("value", justify="right")
    for k, v in rep.to_dict().items():
        if k == "extra":
            continue
        t.add_row(k, f"{v:.4f}" if isinstance(v, float) else str(v))
    console.print(t)


@backtest_app.command("run")
def backtest_run(
    strategy: str = typer.Argument(..., help="Strategy name (see strategies.registry)"),
    config: Optional[Path] = _CONFIG_OPT,
    params: str = typer.Option("{}", help='Strategy params as JSON, e.g. \'{"entry_z": 2.5}\''),
    fair_value: bool = typer.Option(True, help="Enable the fair-value ensemble"),
):
    """Run one backtest over all stored data and print the full report."""
    cfg = _boot(config)
    from quantbot.analytics.bootstrap import sharpe_confidence_interval
    from quantbot.analytics.metrics import compute_report, periods_per_year
    from quantbot.backtest.engine import BacktestEngine
    from quantbot.experiments.tracker import ExperimentTracker
    from quantbot.strategies.registry import REGISTRY

    if strategy not in REGISTRY:
        console.print(f"[red]unknown strategy; options: {sorted(REGISTRY)}[/red]")
        raise typer.Exit(1)
    p = json.loads(params)
    data = _load_backtest_data(cfg)
    if not data.prices:
        console.print("[red]no data — run `quantbot markets sync` and `quantbot collect history` first[/red]")
        raise typer.Exit(1)
    console.print(f"backtesting [bold]{strategy}[/bold] on {len(data.prices)} markets…")
    engine = BacktestEngine(
        cfg, [REGISTRY[strategy](**p)],
        fair_value=_default_fair_value() if fair_value else None,
    )
    res = engine.run(data)
    rep = compute_report(res.equity, res.trades, cfg.risk.initial_capital)
    if len(res.equity) > 10:
        rets = res.equity.pct_change().dropna()
        ppy = periods_per_year(res.equity.index)
        _, lo, hi = sharpe_confidence_interval(rets, ppy)
        rep.sharpe_ci_low, rep.sharpe_ci_high = lo, hi
    _print_report(rep)
    run_id = ExperimentTracker().log_run("backtest", strategy, p, rep.to_dict())
    console.print(f"logged as run [cyan]{run_id}[/cyan]")


@backtest_app.command("walkforward")
def backtest_walkforward(
    strategy: str = typer.Argument(...),
    config: Optional[Path] = _CONFIG_OPT,
    grid: str = typer.Option("{}", help='Param grid JSON, e.g. \'{"entry_z": [1.5, 2, 2.5]}\''),
    folds: int = 4,
    fair_value: bool = True,
):
    """Walk-forward: optimize in-sample, report out-of-sample only."""
    cfg = _boot(config)
    from quantbot.analytics.metrics import compute_report
    from quantbot.backtest.walkforward import walk_forward
    from quantbot.experiments.tracker import ExperimentTracker
    from quantbot.strategies.registry import REGISTRY

    if strategy not in REGISTRY:
        console.print(f"[red]unknown strategy; options: {sorted(REGISTRY)}[/red]")
        raise typer.Exit(1)
    param_grid = {k: v if isinstance(v, list) else [v] for k, v in json.loads(grid).items()}
    if not param_grid:
        # itertools.product of zero lists yields one empty combo -> strategy defaults
        console.print("[yellow]empty grid — evaluating strategy defaults only[/yellow]")
    data = _load_backtest_data(cfg)
    wf = walk_forward(
        cfg, data, REGISTRY[strategy], param_grid,
        n_folds=folds, fair_value=_default_fair_value() if fair_value else None,
    )
    for f in wf.folds:
        console.print(f)
    if len(wf.oos_equity):
        rep = compute_report(wf.oos_equity, wf.oos_trades, cfg.risk.initial_capital)
        console.print("[bold]Out-of-sample (the only number that matters):[/bold]")
        _print_report(rep)
        ExperimentTracker().log_run(
            "walkforward", strategy, {"grid": param_grid, "folds": folds}, rep.to_dict()
        )
    else:
        console.print("[red]no out-of-sample results — likely insufficient data[/red]")


# ---------------------------------------------------------------- paper
@paper_app.command("run")
def paper_run(
    config: Optional[Path] = _CONFIG_OPT,
    top: int = typer.Option(10, help="Trade the top-N liquid markets"),
    poll: float = typer.Option(30.0, help="Poll interval seconds"),
    duration: float = typer.Option(0, help="Stop after N seconds (0 = forever)"),
):
    """Live paper trading against real books with virtual money."""
    cfg = _boot(config)
    from quantbot.runner.paper import PaperRunner
    from quantbot.strategies.registry import build_strategies

    store = _store(cfg)
    ms = sorted(store.load_markets(active_only=True), key=lambda m: -m.liquidity)[:top]
    if not ms:
        console.print("[red]no markets stored — run `quantbot markets sync` first[/red]")
        raise typer.Exit(1)
    strategies = build_strategies(cfg.strategies)
    if not strategies:
        console.print("[red]no strategies enabled in config[/red]")
        raise typer.Exit(1)
    runner = PaperRunner(
        cfg, store, strategies, fair_value=_default_fair_value(), poll_seconds=poll
    )
    asyncio.run(runner.run(ms, duration_seconds=duration))


# ---------------------------------------------------------------- report
@report_app.command("runs")
def report_runs(strategy: Optional[str] = None):
    """List logged experiment runs."""
    from quantbot.experiments.tracker import ExperimentTracker

    runs = ExperimentTracker().list_runs(strategy)
    t = Table(title="Experiment runs")
    for col in ("run_id", "kind", "strategy", "sharpe", "total_return", "n_trades"):
        t.add_column(col)
    for r in runs:
        m = r.get("metrics", {})
        t.add_row(
            r["run_id"], r["kind"], r["strategy"],
            f"{m.get('sharpe', 0):.2f}", f"{m.get('total_return', 0):.2%}",
            str(m.get("n_trades", 0)),
        )
    console.print(t)


@report_app.command("fills")
def report_fills(run_id: str, config: Optional[Path] = _CONFIG_OPT):
    """Show fills for a paper run."""
    cfg = _boot(config)
    df = _store(cfg).load_fills(run_id)
    console.print(df.to_string() if len(df) else "no fills")


if __name__ == "__main__":
    app()
