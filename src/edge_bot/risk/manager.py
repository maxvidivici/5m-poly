"""Risk manager with Kelly sizing, daily/hourly caps and cooldown."""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field

from ..core.config import RiskConfig
from ..signals.indicators import kelly_fraction


@dataclass(slots=True)
class RiskState:
    equity: float
    daily_pnl: float = 0.0
    daily_trades: int = 0
    hourly_trades: int = 0
    consecutive_losses: int = 0
    last_loss_ts: float = 0.0
    day_started_at: float = field(default_factory=time.time)
    hour_started_at: float = field(default_factory=time.time)
    open_positions: int = 0
    recent_outcomes: deque[bool] = field(default_factory=lambda: deque(maxlen=20))


@dataclass(slots=True)
class GateDecision:
    allowed: bool
    reason: str
    size_usd: float = 0.0


class RiskManager:
    def __init__(self, cfg: RiskConfig) -> None:
        self.cfg = cfg
        self.state = RiskState(equity=cfg.start_equity_usd)

    # ---------------------- internal rollover ----------------------

    def _roll_windows(self, now: float | None = None) -> None:
        now = now or time.time()
        if now - self.state.day_started_at >= 86400.0:
            self.state.day_started_at = now
            self.state.daily_pnl = 0.0
            self.state.daily_trades = 0
        if now - self.state.hour_started_at >= 3600.0:
            self.state.hour_started_at = now
            self.state.hourly_trades = 0

    # ---------------------- public API ----------------------

    def can_trade(self, *, now: float | None = None) -> GateDecision:
        self._roll_windows(now)

        if self.state.equity < self.cfg.min_balance_usd:
            return GateDecision(
                False, f"equity_${self.state.equity:.2f}_below_min_${self.cfg.min_balance_usd:.2f}"
            )

        daily_loss_limit = self.cfg.start_equity_usd * (self.cfg.daily_loss_cap_pct / 100.0)
        if -self.state.daily_pnl >= daily_loss_limit:
            return GateDecision(False, f"daily_loss_cap_${daily_loss_limit:.2f}_hit")

        if self.state.daily_trades >= self.cfg.max_trades_per_day:
            return GateDecision(False, "max_trades_per_day_hit")
        if self.state.hourly_trades >= self.cfg.max_trades_per_hour:
            return GateDecision(False, "max_trades_per_hour_hit")

        if self.state.consecutive_losses >= self.cfg.max_consecutive_losses:
            elapsed = (now or time.time()) - self.state.last_loss_ts
            if elapsed < self.cfg.cooldown_after_losses_sec:
                remaining = self.cfg.cooldown_after_losses_sec - elapsed
                return GateDecision(False, f"cooldown_active_{int(remaining)}s_left")
            self.state.consecutive_losses = 0

        return GateDecision(True, "ok")

    def size_position(self, *, confidence: float, side_ask: float) -> float:
        """Calculate position size in USD using fractional Kelly.

        side_ask is the current marketable ask of our side (0..1). The implied
        payoff_ratio = (1 - side_ask) / side_ask: cost `side_ask` per share to
        win `1 - side_ask` of additional profit per share.
        """
        if side_ask <= 0.0 or side_ask >= 1.0:
            return 0.0
        if confidence - side_ask < self.cfg.min_probability_edge:
            return 0.0
        payoff_ratio = (1.0 - side_ask) / side_ask
        k = kelly_fraction(win_prob=confidence, payoff_ratio=payoff_ratio)
        kelly_size = self.state.equity * k * self.cfg.kelly_fraction
        cap_pct = self.state.equity * (self.cfg.max_position_pct / 100.0)
        size = min(kelly_size, cap_pct, self.cfg.max_position_usd)
        if size < self.cfg.min_position_usd:
            return 0.0
        return round(size, 2)

    def record_trade_open(self, *, now: float | None = None) -> None:
        self._roll_windows(now)
        self.state.daily_trades += 1
        self.state.hourly_trades += 1
        self.state.open_positions += 1

    def record_trade_close(self, pnl_usd: float, *, now: float | None = None) -> None:
        self._roll_windows(now)
        self.state.open_positions = max(0, self.state.open_positions - 1)
        self.state.daily_pnl += pnl_usd
        self.state.equity += pnl_usd
        won = pnl_usd > 0
        self.state.recent_outcomes.append(won)
        if won:
            self.state.consecutive_losses = 0
        else:
            self.state.consecutive_losses += 1
            self.state.last_loss_ts = now or time.time()

    def snapshot(self) -> dict[str, float | int]:
        wins = sum(1 for o in self.state.recent_outcomes if o)
        total = len(self.state.recent_outcomes) or 1
        return {
            "equity": round(self.state.equity, 2),
            "daily_pnl": round(self.state.daily_pnl, 2),
            "daily_trades": self.state.daily_trades,
            "hourly_trades": self.state.hourly_trades,
            "consecutive_losses": self.state.consecutive_losses,
            "open_positions": self.state.open_positions,
            "recent_winrate_pct": round(100.0 * wins / total, 1),
        }
