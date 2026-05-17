"""Hedge engine: tiny opposite-side bet when skew becomes extreme."""

from __future__ import annotations

from dataclasses import dataclass

from ..core.config import HedgeConfig


@dataclass(slots=True)
class HedgeDecision:
    place_hedge: bool
    side: str | None
    notional_usd: float
    limit_price: float
    reason: str


def evaluate_hedge(
    cfg: HedgeConfig,
    *,
    main_side: str,
    main_notional_usd: float,
    seconds_left: float,
    opposite_ask: float,
    skew_against_us: float,
) -> HedgeDecision:
    """Decide if we should hedge against a last-second reversal.

    Logic:
    * Skew on our side must be >= skew_trigger (market is 'too sure').
    * Time remaining must be <= seconds_left_max (only near close).
    * Hedge notional = pct_of_main of main, clamped to [min, max] USD caps.
    """
    if not cfg.enabled:
        return HedgeDecision(False, None, 0.0, 0.0, "hedge_disabled")

    if seconds_left > cfg.seconds_left_max:
        return HedgeDecision(False, None, 0.0, 0.0, "too_early_to_hedge")

    if skew_against_us < cfg.skew_trigger:
        return HedgeDecision(False, None, 0.0, 0.0, f"skew_{skew_against_us:.3f}_below_trigger")

    if opposite_ask <= 0.0 or opposite_ask >= 1.0:
        return HedgeDecision(False, None, 0.0, 0.0, "invalid_opposite_ask")

    raw = main_notional_usd * (cfg.notional_pct_of_main / 100.0)
    notional = max(cfg.notional_usd_min, min(cfg.notional_usd_max, raw))

    hedge_side = "DOWN" if main_side == "UP" else "UP"
    return HedgeDecision(True, hedge_side, round(notional, 2), opposite_ask, "extreme_skew_hedge")
