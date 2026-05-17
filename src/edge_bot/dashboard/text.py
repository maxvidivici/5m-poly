"""Text dashboard writer."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..utils.clock import iso_z


def render_dashboard(
    *,
    out_path: Path,
    mode: str,
    market: dict[str, Any] | None,
    last_signal: dict[str, Any] | None,
    risk: dict[str, Any],
    journal_summary: dict[str, Any],
    extras: dict[str, Any] | None = None,
) -> None:
    lines = [
        "==============================================",
        f" edge-bot dashboard   ({mode.upper()})",
        f" updated: {iso_z()}",
        "==============================================",
    ]
    if market:
        lines += [
            "",
            "[market]",
            f"  slug: {market.get('slug')}",
            f"  seconds_left: {market.get('seconds_left'):.1f}"
            if market.get("seconds_left") is not None
            else "  seconds_left: ?",
            f"  up_ask: {market.get('up_ask')}",
            f"  down_ask: {market.get('down_ask')}",
            f"  spread: {market.get('spread')}",
        ]
    if last_signal:
        lines += [
            "",
            "[signal]",
            f"  enter: {last_signal.get('enter')}",
            f"  side: {last_signal.get('side')}",
            f"  confidence: {last_signal.get('confidence', 0):.3f}",
            f"  reason: {last_signal.get('reason')}",
        ]
        feats = last_signal.get("features") or {}
        for k in ("delta_usd", "delta_pct", "side_ask", "zscore", "rsi", "atr"):
            if k in feats:
                lines.append(f"  {k}: {feats[k]:.4f}")
    if risk:
        lines += [
            "",
            "[risk]",
            f"  equity_usd: {risk.get('equity'):.2f}",
            f"  daily_pnl_usd: {risk.get('daily_pnl'):.2f}",
            f"  daily_trades: {risk.get('daily_trades')}",
            f"  hourly_trades: {risk.get('hourly_trades')}",
            f"  consecutive_losses: {risk.get('consecutive_losses')}",
            f"  recent_winrate_pct: {risk.get('recent_winrate_pct')}",
            f"  open_positions: {risk.get('open_positions')}",
        ]
    if journal_summary:
        lines += [
            "",
            "[journal net]",
            f"  n_trades: {journal_summary.get('n_trades')}",
            f"  wins: {journal_summary.get('wins')}",
            f"  losses: {journal_summary.get('losses')}",
            f"  total_pnl_usd: {journal_summary.get('total_pnl_usd')}",
            f"  avg_pnl_usd: {journal_summary.get('avg_pnl_usd')}",
        ]
    if extras:
        lines.append("")
        lines.append("[extras]")
        for k, v in extras.items():
            lines.append(f"  {k}: {v}")
    lines.append("")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")
