"""edge-bot command-line interface."""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
from pathlib import Path

import typer
from rich.console import Console

from .backtest.engine import fetch_btc_1m_klines, run_backtest, run_backtest_from_klines
from .core.config import load_config
from .core.orchestrator import Orchestrator
from .notifications.telegram import TelegramReporter, build_status_report
from .storage.journal import TradeJournal
from .utils.logging import setup_logging

app = typer.Typer(help="Polymarket BTC 5m Edge Bot")
console = Console()


@app.command()
def run(
    mode: str = typer.Option(None, "--mode", help="Override EDGE_MODE (paper/live)"),
    log_level: str = typer.Option(None, "--log-level"),
    max_ticks: int = typer.Option(0, "--max-ticks", min=0, help="Run a bounded number of ticks, then exit"),
) -> None:
    """Start the trading orchestrator."""
    cfg = load_config()
    if mode:
        cfg.mode = mode.lower()
    if log_level:
        cfg.ops.log_level = log_level.upper()
    setup_logging(level=cfg.ops.log_level, audit_path=cfg.storage.audit_log)
    console.print(f"[bold green]Starting edge-bot in {cfg.mode.upper()} mode[/bold green]")
    orch = Orchestrator(cfg)
    if max_ticks > 0:
        orch.run_ticks(max_ticks)
    else:
        orch.run()


@app.command()
def report(
    limit: int = typer.Option(20, "--limit", help="Number of recent trades to show"),
    db: Path = typer.Option(None, "--db", help="Path to journal SQLite db"),
) -> None:
    """Print a summary report of recent trades."""
    cfg = load_config()
    db_path = db or cfg.storage.journal_db
    if not db_path.exists():
        console.print(f"[yellow]No journal at {db_path}[/yellow]")
        raise typer.Exit(code=1)
    journal = TradeJournal(db_path)
    console.print("[bold]Main strategy summary:[/bold]")
    console.print_json(json.dumps(journal.summary(hedge=False), default=str))
    console.print("\n[bold]Hedge summary:[/bold]")
    console.print_json(json.dumps(journal.summary(hedge=True), default=str))
    console.print("\n[bold]Net summary:[/bold]")
    console.print_json(json.dumps(journal.summary(), default=str))
    console.print(f"\n[bold]Recent {limit} trades:[/bold]")
    for row in journal.list_recent(limit):
        console.print_json(json.dumps(row, default=str))


@app.command("signal-report")
def signal_report(
    limit: int = typer.Option(20, "--limit", help="Number of rows to show per section"),
    db: Path = typer.Option(None, "--db", help="Path to journal SQLite db"),
) -> None:
    """Analyze all observed entry/skip points, including profitable misses."""
    cfg = load_config()
    db_path = db or cfg.storage.journal_db
    if not db_path.exists():
        console.print(f"[yellow]No journal at {db_path}[/yellow]")
        raise typer.Exit(code=1)
    journal = TradeJournal(db_path)
    console.print("[bold]Signal observation summary:[/bold]")
    console.print_json(json.dumps(journal.signal_summary(), default=str))
    console.print(f"\n[bold]Top {limit} skip/entry reasons:[/bold]")
    for row in journal.signal_reason_counts(limit=limit):
        console.print_json(json.dumps(row, default=str))
    console.print(f"\n[bold]Top {limit} profitable skipped observations:[/bold]")
    for row in journal.list_profitable_skipped_observations(limit=limit):
        console.print_json(json.dumps(row, default=str))


@app.command()
def dump_config() -> None:
    """Print the resolved configuration (without auth secrets)."""
    cfg = load_config()
    safe = {
        "mode": cfg.mode,
        "symbol": cfg.symbol,
        "data": dataclasses.asdict(cfg.data),
        "signal": dataclasses.asdict(cfg.signal),
        "risk": dataclasses.asdict(cfg.risk),
        "hedge": dataclasses.asdict(cfg.hedge),
        "telegram": {
            "enabled": cfg.telegram.enabled,
            "bot_token_configured": bool(cfg.telegram.bot_token),
            "chat_id_configured": bool(cfg.telegram.chat_id),
            "report_interval_sec": cfg.telegram.report_interval_sec,
            "send_on_start": cfg.telegram.send_on_start,
        },
        "exit": dataclasses.asdict(cfg.exit_),
        "storage": {
            "runtime_dir": str(cfg.storage.runtime_dir),
            "journal_db": str(cfg.storage.journal_db),
            "audit_log": str(cfg.storage.audit_log),
            "dashboard_path": str(cfg.storage.dashboard_path),
        },
        "ops": dataclasses.asdict(cfg.ops),
        "auth_has_private_key": cfg.auth.can_sign,
        "auth_has_api_creds": cfg.auth.has_full_creds,
    }
    console.print_json(json.dumps(safe, default=str))


@app.command("telegram-test")
def telegram_test() -> None:
    """Send the current journal/risk report to Telegram once."""
    cfg = load_config()
    reporter = TelegramReporter(cfg.telegram)
    if not reporter.configured:
        console.print(
            "[red]Telegram is not configured. Set TELEGRAM_ENABLED=true, "
            "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID.[/red]"
        )
        raise typer.Exit(code=1)

    journal = TradeJournal(cfg.storage.journal_db)
    text = build_status_report(
        mode=cfg.mode,
        equity=cfg.risk.start_equity_usd,
        risk={
            "daily_pnl": 0.0,
            "daily_trades": 0,
            "hourly_trades": 0,
            "consecutive_losses": 0,
        },
        total_summary=journal.summary(hedge=False),
        interval_summary=journal.summary(hedge=False),
        total_hedge_summary=journal.summary(hedge=True),
        interval_hedge_summary=journal.summary(hedge=True),
        total_net_summary=journal.summary(),
        interval_net_summary=journal.summary(),
        open_positions=0,
        last_signal={"reason": "manual_telegram_test", "features": {}},
        signal_summary=journal.signal_summary(),
    )
    result = reporter.send_text(text)
    if not result.ok:
        console.print(f"[red]Telegram send failed: {result.reason} status={result.status_code}[/red]")
        raise typer.Exit(code=1)
    console.print("[green]Telegram test report sent.[/green]")


@app.command()
def backtest(
    days: int = typer.Option(7, "--days", min=1, max=365, help="Number of trailing UTC days to replay"),
    end_utc: str = typer.Option(None, "--end-utc", help="UTC end time, ISO format. Defaults to now."),
) -> None:
    """Replay historical BTC 5m windows without placing orders."""
    cfg = load_config()
    cfg.mode = "backtest"
    if end_utc:
        end_dt = dt.datetime.fromisoformat(end_utc.replace("Z", "+00:00"))
        if end_dt.tzinfo is None:
            end_dt = end_dt.replace(tzinfo=dt.UTC)
    else:
        end_dt = dt.datetime.now(dt.UTC)
    start_dt = end_dt - dt.timedelta(days=days)
    result = run_backtest(cfg, start_ms=int(start_dt.timestamp() * 1000), end_ms=int(end_dt.timestamp() * 1000))
    console.print_json(json.dumps(dataclasses.asdict(result), default=str))


@app.command()
def optimize(
    days: int = typer.Option(30, "--days", min=1, max=365, help="Number of trailing UTC days to replay"),
    top: int = typer.Option(10, "--top", min=1, max=50, help="Number of best parameter sets to print"),
) -> None:
    """Sweep conservative signal thresholds using one cached candle download."""
    base = load_config()
    base.mode = "backtest"
    end_dt = dt.datetime.now(dt.UTC)
    start_dt = end_dt - dt.timedelta(days=days)
    klines = fetch_btc_1m_klines(start_ms=int(start_dt.timestamp() * 1000), end_ms=int(end_dt.timestamp() * 1000))
    rows: list[dict] = []
    for atr_mult in (0.30, 0.35, 0.40):
        for hard_min in (50.0, 55.0, 65.0):
            for hard_max in (85.0, 95.0, 100.0):
                for ask_min in (0.68, 0.70):
                    soft_ask_min = max(0.70, ask_min)
                    for min_confidence in (0.62, 0.68):
                        cfg = dataclasses.replace(base)
                        cfg.signal = dataclasses.replace(
                            base.signal,
                            adaptive_delta_enabled=True,
                            adaptive_delta_atr_mult=atr_mult,
                            adaptive_delta_hard_min=hard_min,
                            adaptive_delta_hard_max=hard_max,
                            clob_ask_min=ask_min,
                            soft_delta_clob_ask_min=soft_ask_min,
                            min_confidence=min_confidence,
                        )
                        result = run_backtest_from_klines(cfg, klines)
                        rows.append(
                            {
                                "atr_mult": atr_mult,
                                "hard_min": hard_min,
                                "hard_max": hard_max,
                                "ask_min": ask_min,
                                "soft_ask_min": soft_ask_min,
                                "min_confidence": min_confidence,
                                "n_trades": result.n_trades,
                                "win_rate_pct": result.win_rate_pct,
                                "total_pnl": result.total_pnl,
                                "final_equity": result.final_equity,
                                "max_drawdown_pct": result.max_drawdown_pct,
                            }
                        )
    rows.sort(key=lambda r: (float(r["total_pnl"]), int(r["n_trades"])), reverse=True)
    console.print_json(json.dumps(rows[:top], default=str))


if __name__ == "__main__":
    app()
