"""edge-bot command-line interface."""

from __future__ import annotations

from collections import defaultdict
import dataclasses
import datetime as dt
import json
from pathlib import Path
from typing import Any

import typer
from rich.console import Console

from .analysis.regime import bucket_trade_features, regime_tags_from_features
from .backtest.engine import fetch_btc_1m_klines, run_backtest, run_backtest_from_klines
from .core.config import load_config
from .core.orchestrator import Orchestrator
from .data.polymarket import GammaClient
from .notifications.telegram import TelegramReporter, build_status_report
from .storage.journal import TradeJournal
from .utils.clock import now_ts
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


@app.command("fill-report")
def fill_report(
    limit: int = typer.Option(20, "--limit", help="Number of recent fill simulation rows to show"),
    db: Path = typer.Option(None, "--db", help="Path to journal SQLite db"),
) -> None:
    """Analyze orderbook-depth fill simulations and market regime distribution."""
    cfg = load_config()
    db_path = db or cfg.storage.journal_db
    if not db_path.exists():
        console.print(f"[yellow]No journal at {db_path}[/yellow]")
        raise typer.Exit(code=1)
    journal = TradeJournal(db_path)

    console.print("[bold]Fill simulation summary by target:[/bold]")
    for row in journal.fill_simulation_summary(
        hedge=False,
        min_fill_ratio=cfg.live_readiness.min_fill_ratio,
    ):
        console.print_json(json.dumps(row, default=str))

    console.print(f"\n[bold]Recent {limit} fill simulation rows:[/bold]")
    for row in journal.list_recent_fill_simulations(limit=limit, hedge=False):
        console.print_json(json.dumps(row, default=str))

    trades = journal.list_trade_analysis(hedge=False)
    console.print("\n[bold]PnL by regime tag:[/bold]")
    for row in _summarize_regime_tags(trades, cfg.signal):
        console.print_json(json.dumps(row, default=str))

    sections = (
        ("PnL by side", lambda row, _features: str(row.get("side") or "unknown")),
        (
            "PnL by delta bucket",
            lambda _row, features: bucket_trade_features(features).get("delta_bucket", "unknown")
            if features
            else "unknown",
        ),
        (
            "PnL by ask bucket",
            lambda _row, features: bucket_trade_features(features).get("ask_bucket", "unknown")
            if features
            else "unknown",
        ),
        (
            "PnL by seconds_left bucket",
            lambda _row, features: bucket_trade_features(features).get("seconds_left_bucket", "unknown")
            if features
            else "unknown",
        ),
        (
            "PnL by liquidity bucket",
            lambda _row, features: bucket_trade_features(features).get("liquidity_bucket", "unknown")
            if features
            else "unknown",
        ),
    )
    for title, label_fn in sections:
        console.print(f"\n[bold]{title}:[/bold]")
        for row in _summarize_trade_groups(trades, label_fn):
            console.print_json(json.dumps(row, default=str))


@app.command("reconcile-official")
def reconcile_official(
    apply: bool = typer.Option(False, "--apply", help="Write official Polymarket outcomes into the journal"),
    db: Path = typer.Option(None, "--db", help="Path to journal SQLite db"),
    mode: str = typer.Option("paper", "--mode", help="Journal mode to reconcile"),
    limit: int = typer.Option(0, "--limit", min=0, help="Limit number of trades; 0 means all"),
) -> None:
    """Recalculate paper trades and signal observations from official Polymarket outcomes."""
    cfg = load_config()
    db_path = db or cfg.storage.journal_db
    if not db_path.exists():
        console.print(f"[yellow]No journal at {db_path}[/yellow]")
        raise typer.Exit(code=1)

    journal = TradeJournal(db_path)
    gamma = GammaClient(base_url=cfg.data.gamma_base_url)
    trades = journal.list_trades_for_reconcile(mode=mode)
    if limit > 0:
        trades = trades[:limit]

    resolution_cache: dict[str, object] = {}

    def resolution_for(slug: str):
        if slug not in resolution_cache:
            resolution_cache[slug] = gamma.resolve_market_resolution(
                slug,
                required_resolution_source=cfg.data.required_resolution_source,
            )
        return resolution_cache[slug]

    checked = 0
    pending = 0
    changed = 0
    pnl_changed = 0
    official_wins = 0
    official_losses = 0
    old_pnl = 0.0
    official_pnl = 0.0
    changed_rows: list[dict] = []
    ts = now_ts()

    for row in trades:
        resolution = resolution_for(str(row["market_slug"]))
        if resolution is None or not resolution.resolved or resolution.winning_side is None:
            pending += 1
            continue
        checked += 1
        won = row["side"] == resolution.winning_side
        proceeds = round(float(row["shares"]) if won else 0.0, 4)
        pnl = proceeds - float(row["cost_usd"])
        old = float(row["pnl_usd"] or 0.0)
        old_pnl += old
        official_pnl += pnl
        if won:
            official_wins += 1
        else:
            official_losses += 1
        pnl_differs = row.get("pnl_usd") is None or abs(pnl - old) > 1e-9
        needs_official_mark = not str(row.get("close_reason") or "").startswith("official_")
        if pnl_differs:
            pnl_changed += 1
        if pnl_differs or needs_official_mark:
            changed += 1
            if pnl_differs:
                changed_rows.append(
                {
                    "id": row["id"],
                    "trade_id": row["trade_id"],
                    "market_slug": row["market_slug"],
                    "side": row["side"],
                    "official_winning_side": resolution.winning_side,
                    "old_pnl": round(old, 4),
                    "official_pnl": round(pnl, 4),
                }
                )
            if apply:
                journal.reconcile_trade_official(
                    trade_id=str(row["trade_id"]),
                    winning_side=str(resolution.winning_side),
                    reconciled_ts=ts,
                    resolution_source=str(resolution.resolution_source),
                    raw_status=str(resolution.raw_status),
                )

    signal_markets = journal.signal_markets()
    signal_updated = 0
    if apply:
        for row in signal_markets:
            resolution = resolution_for(str(row["market_slug"]))
            if resolution is None or not resolution.resolved or resolution.winning_side is None:
                continue
            signal_updated += journal.settle_signal_observations(
                market_slug=str(row["market_slug"]),
                winning_side=str(resolution.winning_side),
                settled_ts=ts,
                only_unsettled=False,
            )

    payload = {
        "mode": mode,
        "apply": apply,
        "trades_checked": checked,
        "pending_unresolved": pending,
        "changed_trades": changed,
        "pnl_changed_trades": pnl_changed,
        "official_wins": official_wins,
        "official_losses": official_losses,
        "old_pnl_usd": round(old_pnl, 4),
        "official_pnl_usd": round(official_pnl, 4),
        "difference_usd": round(official_pnl - old_pnl, 4),
        "signal_observations_updated": signal_updated,
        "changed_sample": changed_rows[:30],
    }
    console.print_json(json.dumps(payload, default=str))


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
        "fees": dataclasses.asdict(cfg.fees),
        "live_readiness": dataclasses.asdict(cfg.live_readiness),
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


def _summarize_regime_tags(trades: list[dict[str, Any]], signal_cfg: object) -> list[dict[str, Any]]:
    def labels(row: dict[str, Any], features: dict[str, Any]) -> list[str]:
        raw_tags = features.get("regime_tags")
        if isinstance(raw_tags, list) and raw_tags:
            return [str(tag) for tag in raw_tags]
        if not features:
            return ["unknown"]
        return regime_tags_from_features(features, signal_cfg)

    return _summarize_multi_groups(trades, labels)


def _summarize_trade_groups(trades: list[dict[str, Any]], label_fn) -> list[dict[str, Any]]:
    return _summarize_multi_groups(trades, lambda row, features: [label_fn(row, features)])


def _summarize_multi_groups(trades: list[dict[str, Any]], labels_fn) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "trades": 0,
            "official_settled": 0,
            "wins": 0,
            "losses": 0,
            "pnl_usd": 0.0,
        }
    )
    for row in trades:
        features = _features(row)
        labels = labels_fn(row, features)
        for label in labels:
            key = str(label or "unknown")
            item = groups[key]
            item["trades"] += 1
            pnl = row.get("pnl_usd")
            if pnl is None:
                continue
            pnl_v = _float(pnl)
            item["official_settled"] += 1
            item["pnl_usd"] += pnl_v
            if pnl_v > 0:
                item["wins"] += 1
            else:
                item["losses"] += 1

    result: list[dict[str, Any]] = []
    for label, item in groups.items():
        settled = int(item["official_settled"])
        wins = int(item["wins"])
        result.append(
            {
                "group": label,
                "trades": int(item["trades"]),
                "official_settled": settled,
                "wins": wins,
                "losses": int(item["losses"]),
                "winrate_pct": round((wins / settled * 100.0), 2) if settled else 0.0,
                "pnl_usd": round(float(item["pnl_usd"]), 4),
            }
        )
    result.sort(key=lambda r: (int(r["trades"]), float(r["pnl_usd"])), reverse=True)
    return result


def _features(row: dict[str, Any]) -> dict[str, Any]:
    try:
        raw = json.loads(str(row.get("features_json") or "{}"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


if __name__ == "__main__":
    app()
