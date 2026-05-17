"""Telegram reporting utilities."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

from ..core.config import TelegramConfig
from ..utils.clock import iso_z

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class TelegramSendResult:
    ok: bool
    status_code: int | None
    reason: str


class TelegramReporter:
    def __init__(self, cfg: TelegramConfig) -> None:
        self.cfg = cfg

    @property
    def configured(self) -> bool:
        return bool(self.cfg.enabled and self.cfg.bot_token and self.cfg.chat_id)

    def send_text(self, text: str) -> TelegramSendResult:
        if not self.configured:
            return TelegramSendResult(False, None, "telegram_not_configured")

        url = f"https://api.telegram.org/bot{self.cfg.bot_token}/sendMessage"
        payload = {
            "chat_id": self.cfg.chat_id,
            "text": text[:3900],
            "disable_web_page_preview": True,
        }
        try:
            with httpx.Client(timeout=self.cfg.timeout_sec) as client:
                response = client.post(url, json=payload)
            if response.status_code >= 400:
                logger.warning("telegram_send_failed status=%s body=%s", response.status_code, response.text[:300])
                return TelegramSendResult(False, response.status_code, "telegram_http_error")
            return TelegramSendResult(True, response.status_code, "ok")
        except Exception as exc:  # network errors must never stop trading loop
            logger.warning("telegram_send_exception reason=%s", exc)
            return TelegramSendResult(False, None, "telegram_exception")


def build_status_report(
    *,
    mode: str,
    equity: float,
    risk: dict[str, Any],
    total_summary: dict[str, Any],
    interval_summary: dict[str, Any],
    total_hedge_summary: dict[str, Any] | None = None,
    interval_hedge_summary: dict[str, Any] | None = None,
    total_net_summary: dict[str, Any] | None = None,
    interval_net_summary: dict[str, Any] | None = None,
    open_positions: int,
    last_signal: dict[str, Any] | None,
    signal_summary: dict[str, Any] | None = None,
) -> str:
    total_hedge_summary = total_hedge_summary or {}
    interval_hedge_summary = interval_hedge_summary or {}
    total_net_summary = total_net_summary or total_summary
    interval_net_summary = interval_net_summary or interval_summary

    total_trades, total_wins, total_losses, total_pnl, total_wr = _summary_parts(total_summary)
    interval_trades, interval_wins, interval_losses, interval_pnl, interval_wr = _summary_parts(interval_summary)
    hedge_trades, hedge_wins, hedge_losses, hedge_pnl, hedge_wr = _summary_parts(total_hedge_summary)
    interval_hedge_trades, interval_hedge_wins, interval_hedge_losses, interval_hedge_pnl, interval_hedge_wr = (
        _summary_parts(interval_hedge_summary)
    )
    _, _, _, total_net_pnl, _ = _summary_parts(total_net_summary)
    _, _, _, interval_net_pnl, _ = _summary_parts(interval_net_summary)

    reason = "n/a"
    side = "n/a"
    confidence = 0.0
    delta = 0.0
    hard = 0.0
    soft = 0.0
    if last_signal:
        reason = str(last_signal.get("reason") or "n/a")
        side = str(last_signal.get("side") or "n/a")
        confidence = float(last_signal.get("confidence") or 0.0)
        features = last_signal.get("features") or {}
        if isinstance(features, dict):
            delta = float(features.get("delta_usd") or 0.0)
            hard = float(features.get("delta_hard_min_usd") or 0.0)
            soft = float(features.get("delta_soft_min_usd") or 0.0)

    signal_summary = signal_summary or {}
    observations = int(signal_summary.get("observations") or 0)
    markets_seen = int(signal_summary.get("markets_seen") or 0)
    entry_signals = int(signal_summary.get("entry_signals") or 0)
    profitable_skips = int(signal_summary.get("profitable_skipped_observations") or 0)

    return "\n".join(
        [
            f"BTC 5m Polymarket report | {mode.upper()}",
            f"time: {iso_z()}",
            "",
            "Last interval main strategy:",
            f"trades: {interval_trades} | wins/losses: {interval_wins}/{interval_losses} | winrate: {interval_wr:.1f}%",
            f"pnl: ${interval_pnl:.2f}",
            f"hedge: {interval_hedge_trades} trades | wins/losses: {interval_hedge_wins}/{interval_hedge_losses} | winrate: {interval_hedge_wr:.1f}% | pnl: ${interval_hedge_pnl:.2f}",
            f"net pnl: ${interval_net_pnl:.2f}",
            "",
            "Total main strategy:",
            f"trades: {total_trades} | wins/losses: {total_wins}/{total_losses} | winrate: {total_wr:.1f}%",
            f"pnl: ${total_pnl:.2f}",
            f"hedge: {hedge_trades} trades | wins/losses: {hedge_wins}/{hedge_losses} | winrate: {hedge_wr:.1f}% | pnl: ${hedge_pnl:.2f}",
            f"net pnl: ${total_net_pnl:.2f}",
            "",
            "Risk:",
            f"equity: ${equity:.2f} | daily pnl: ${float(risk.get('daily_pnl') or 0.0):.2f}",
            f"daily/hourly trades: {risk.get('daily_trades', 0)}/{risk.get('hourly_trades', 0)} | open: {open_positions}",
            f"consecutive losses: {risk.get('consecutive_losses', 0)}",
            "",
            "Last signal:",
            f"side: {side} | confidence: {confidence:.3f} | reason: {reason}",
            f"delta: ${delta:.1f} | soft/hard: ${soft:.0f}/${hard:.0f}",
            "",
            "Signal log:",
            f"observations: {observations} | markets: {markets_seen} | entry signals: {entry_signals}",
            f"profitable skipped observations: {profitable_skips}",
        ]
    )


def _winrate(wins: int, losses: int) -> float:
    total = wins + losses
    return (wins / total * 100.0) if total > 0 else 0.0


def _summary_parts(summary: dict[str, Any]) -> tuple[int, int, int, float, float]:
    trades = int(summary.get("n_trades") or 0)
    wins = int(summary.get("wins") or 0)
    losses = int(summary.get("losses") or 0)
    pnl = float(summary.get("total_pnl_usd") or 0.0)
    return trades, wins, losses, pnl, _winrate(wins, losses)
