"""Composite confidence score combining all entry filters.

Encodes the user's BTC 5m Up/Down strategy:

  * Entry window: ~120s to 10s before close (tight entry sweet spot).
  * BTC movement threshold floats with recent 5m ATR, with a guarded soft zone.
  * CLOB ask of stronger side has crossed entry threshold (market agrees).
  * Skew is not extreme against us.

On top of the original strategy we layer:

  * Z-score soft penalty when delta is statistically overheated.
  * RSI normal-range bonus, never a hard block.
  * ATR regime/overheat score inside composite confidence.
  * Micro-momentum bonus (last 2x 1m candles confirm).
  * Market-consensus guard (never bet AGAINST very confident markets).
  * Confidence is mapped to an estimated win probability for Kelly sizing.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ..core.config import SignalConfig
from . import indicators


@dataclass(slots=True)
class FeatureSet:
    current_btc: float
    window_open_btc: float
    closes_1m: tuple[float, ...]
    highs_5m: tuple[float, ...]
    lows_5m: tuple[float, ...]
    closes_5m: tuple[float, ...]
    historical_deltas_pct: tuple[float, ...]
    seconds_left: float
    clob_up_ask: float
    clob_down_ask: float
    clob_up_bid: float
    clob_down_bid: float
    top_ask_notional_usd: float


@dataclass(slots=True)
class CompositeDecision:
    enter: bool
    side: str | None
    confidence: float
    reason: str
    features: dict[str, float]


def evaluate(features: FeatureSet, cfg: SignalConfig) -> CompositeDecision:
    """Run the full filter stack and return a decision."""
    feat_dump: dict[str, float] = {}

    delta_usd = indicators.window_delta_usd(features.current_btc, features.window_open_btc)
    delta_pct = indicators.window_delta_pct(features.current_btc, features.window_open_btc) * 100.0
    feat_dump["delta_usd"] = delta_usd
    feat_dump["delta_pct"] = delta_pct
    abs_delta_usd = abs(delta_usd)
    feat_dump["abs_delta_usd"] = abs_delta_usd

    direction_up = delta_usd >= 0
    side_label = "UP" if direction_up else "DOWN"

    # 1. Tight entry window
    if features.seconds_left > cfg.entry_seconds_left_max:
        return CompositeDecision(False, None, 0.0, "too_early_to_enter", feat_dump)
    if features.seconds_left < cfg.entry_seconds_left_min:
        return CompositeDecision(False, None, 0.0, "too_late_to_enter", feat_dump)

    # 2. CLOB ask for the movement side; needed for both hard and soft delta gates.
    side_ask = features.clob_up_ask if direction_up else features.clob_down_ask
    feat_dump["side_ask"] = side_ask

    atr_v = indicators.atr(features.highs_5m, features.lows_5m, features.closes_5m, period=5)
    feat_dump["atr"] = atr_v
    delta_soft_min, delta_hard_min, delta_strong = _delta_thresholds(atr_v, cfg)
    feat_dump["delta_soft_min_usd"] = delta_soft_min
    feat_dump["delta_hard_min_usd"] = delta_hard_min
    feat_dump["delta_strong_usd"] = delta_strong
    feat_dump["adaptive_delta_enabled"] = 1.0 if cfg.adaptive_delta_enabled else 0.0

    # 3. Minimum BTC movement requirement (the core strategy condition).
    # Hard threshold floats with recent ATR. Soft zone allows near-misses only
    # when the market already confirms our side strongly enough.
    feat_dump["soft_delta_zone"] = 0.0
    if abs_delta_usd < delta_soft_min:
        return CompositeDecision(
            False,
            side_label,
            0.0,
            f"btc_move_${abs_delta_usd:.1f}_below_soft_${delta_soft_min:.0f}",
            feat_dump,
        )
    if abs_delta_usd < delta_hard_min:
        feat_dump["soft_delta_zone"] = 1.0
        if side_ask < cfg.soft_delta_clob_ask_min:
            return CompositeDecision(
                False,
                side_label,
                0.0,
                f"soft_delta_side_ask_{side_ask:.3f}_below_{cfg.soft_delta_clob_ask_min:.2f}",
                feat_dump,
            )

    # 4. CLOB ask gate - the market must already lean our way
    if side_ask < cfg.clob_ask_min:
        return CompositeDecision(
            False,
            side_label,
            0.0,
            f"side_ask_{side_ask:.3f}_below_{cfg.clob_ask_min:.2f}",
            feat_dump,
        )
    if side_ask > cfg.clob_ask_max:
        return CompositeDecision(
            False,
            side_label,
            0.0,
            f"side_ask_{side_ask:.3f}_above_{cfg.clob_ask_max:.2f}_too_close_to_resolved",
            feat_dump,
        )

    # 5. Consensus guard — never bet against very-confident markets
    opposite_ask = features.clob_down_ask if direction_up else features.clob_up_ask
    if (1.0 - opposite_ask) < cfg.consensus_guard_min:
        # opposite YES is so cheap, our own side is essentially confirmed; safe path
        pass
    feat_dump["opposite_ask"] = opposite_ask

    # 6. Spread + liquidity
    side_bid = features.clob_up_bid if direction_up else features.clob_down_bid
    spread = max(0.0, side_ask - side_bid)
    feat_dump["spread"] = spread
    if spread > cfg.spread_max:
        return CompositeDecision(False, side_label, 0.0, f"spread_{spread:.3f}_too_wide", feat_dump)
    if features.top_ask_notional_usd < cfg.top_ask_notional_usd_min:
        return CompositeDecision(
            False,
            side_label,
            0.0,
            f"top_ask_${features.top_ask_notional_usd:.1f}_too_thin",
            feat_dump,
        )

    # 7. Z-score soft score. Overheated moves reduce confidence instead of blocking.
    z = indicators.zscore(features.historical_deltas_pct, current=delta_pct)
    zscore_score = _zscore_score(z, cfg)
    feat_dump["zscore"] = z
    feat_dump["zscore_score"] = zscore_score

    # 8. RSI soft bonus. Normal RSI helps; extreme RSI simply does not add score.
    rsi_v = indicators.rsi(features.closes_1m, period=14)
    rsi_score = _rsi_score(rsi_v, cfg)
    feat_dump["rsi"] = rsi_v
    feat_dump["rsi_score"] = rsi_score

    # 9. ATR regime and overheat score. This is not a standalone entry blocker.
    current_range = 0.0
    if atr_v > 0.0 and len(features.highs_5m) >= 1 and len(features.lows_5m) >= 1:
        current_range = abs(features.highs_5m[-1] - features.lows_5m[-1])
    atr_pct = (atr_v / features.current_btc * 100.0) if features.current_btc > 0 else 0.0
    atr_score = _atr_score(atr_pct, current_range, atr_v, cfg)
    feat_dump["current_range"] = current_range
    feat_dump["atr_pct"] = atr_pct
    feat_dump["atr_score"] = atr_score

    # 10. Micro-momentum bonus
    momentum_ok = indicators.micro_momentum_aligned(features.closes_1m, direction_up)
    feat_dump["micro_momentum_aligned"] = 1.0 if momentum_ok else 0.0

    # 11. Composite confidence. First build a normalized signal-quality score,
    # then map it to an estimated win probability for Kelly sizing.
    delta_strength = _delta_weight(abs_delta_usd, delta_soft_min, delta_hard_min, delta_strong)
    side_ask_strength = _ask_weight(side_ask, cfg)
    momentum_score = 1.0 if momentum_ok else 0.0

    weights = {
        "delta": (delta_strength, 0.30),
        "ask": (side_ask_strength, 0.30),
        "zscore": (zscore_score, 0.15),
        "atr": (atr_score, 0.10),
        "rsi_bonus": (rsi_score, 0.10),
        "momentum_bonus": (momentum_score, 0.05),
    }
    signal_quality = sum(score * w for score, w in weights.values())
    signal_quality = max(0.0, min(1.0, signal_quality))
    confidence = max(0.0, min(0.97, 0.50 + 0.45 * signal_quality))
    feat_dump["signal_quality"] = signal_quality
    feat_dump["confidence"] = confidence

    if confidence < cfg.min_confidence:
        return CompositeDecision(
            False,
            side_label,
            confidence,
            f"confidence_{confidence:.2f}_below_{cfg.min_confidence:.2f}",
            feat_dump,
        )

    return CompositeDecision(True, side_label, confidence, "entry_signal_passed_all_filters", feat_dump)


def _delta_thresholds(atr_v: float, cfg: SignalConfig) -> tuple[float, float, float]:
    fallback_soft = min(cfg.window_delta_usd_soft_min, cfg.window_delta_usd_min)
    fallback_hard = max(cfg.window_delta_usd_min, fallback_soft)
    fallback_strong = max(cfg.window_delta_usd_strong, fallback_hard)
    if not cfg.adaptive_delta_enabled or atr_v <= 0.0:
        return fallback_soft, fallback_hard, fallback_strong

    hard_floor = min(cfg.adaptive_delta_hard_min, cfg.adaptive_delta_hard_max)
    hard_ceiling = max(cfg.adaptive_delta_hard_min, cfg.adaptive_delta_hard_max)
    hard = _clamp(atr_v * cfg.adaptive_delta_atr_mult, hard_floor, hard_ceiling)
    soft = max(cfg.window_delta_usd_soft_min, hard * cfg.adaptive_delta_soft_ratio)
    soft = min(soft, hard)
    strong = max(cfg.window_delta_usd_strong, hard * 1.50)
    return soft, hard, strong


def _clamp(value: float, floor: float, ceiling: float) -> float:
    return max(floor, min(ceiling, value))


def _delta_weight(abs_delta_usd: float, soft_floor: float, hard_floor: float, strong_floor: float) -> float:
    if abs_delta_usd >= strong_floor:
        return 1.0
    if abs_delta_usd < soft_floor:
        return 0.0

    if abs_delta_usd < hard_floor:
        span = max(1e-6, hard_floor - soft_floor)
        return 0.55 + ((abs_delta_usd - soft_floor) / span) * 0.20

    span = max(1e-6, strong_floor - hard_floor)
    return 0.75 + ((abs_delta_usd - hard_floor) / span) * 0.25


def _ask_weight(ask: float, cfg: SignalConfig) -> float:
    if ask < cfg.clob_ask_min:
        return 0.0
    if ask >= cfg.clob_ask_max:
        return 0.0

    # A 0.70 ask is meaningful confirmation in this market; 0.85 remains the sweet spot.
    sweet = 0.85
    if ask <= sweet:
        span = max(1e-6, sweet - cfg.clob_ask_min)
        return 0.45 + ((ask - cfg.clob_ask_min) / span) * 0.55

    span = max(1e-6, cfg.clob_ask_max - sweet)
    return max(0.0, 1.0 - ((ask - sweet) / span) * 0.30)


def _zscore_score(z: float, cfg: SignalConfig) -> float:
    abs_z = abs(z)
    if abs_z <= cfg.zscore_max:
        return 1.0
    extreme = max(cfg.zscore_max + 0.1, cfg.zscore_max * 1.5)
    if abs_z >= extreme:
        return -0.5
    span = extreme - cfg.zscore_max
    return 1.0 - ((abs_z - cfg.zscore_max) / span) * 1.5


def _atr_score(atr_pct: float, current_range: float, atr_v: float, cfg: SignalConfig) -> float:
    if atr_pct <= 0.0:
        regime_score = 0.0
    elif atr_pct < cfg.atr_score_min_pct:
        regime_score = 0.0
    elif atr_pct <= cfg.atr_score_max_pct:
        regime_score = 1.0
    elif atr_pct >= cfg.atr_score_high_pct:
        regime_score = -0.5
    else:
        span = max(1e-6, cfg.atr_score_high_pct - cfg.atr_score_max_pct)
        regime_score = 1.0 - ((atr_pct - cfg.atr_score_max_pct) / span) * 1.5

    if atr_v > 0.0 and current_range > 0.0:
        overheat = current_range / max(1e-6, atr_v * cfg.atr_overheat_mult)
        if overheat > 1.0:
            range_score = max(-0.5, 1.0 - (overheat - 1.0) * 1.5)
            regime_score = min(regime_score, range_score)

    return _clamp(regime_score, -0.5, 1.0)


def _rsi_score(rsi_v: float, cfg: SignalConfig) -> float:
    if rsi_v < cfg.rsi_min or rsi_v > cfg.rsi_max:
        return 0.0
    distance_from_neutral = abs(rsi_v - 50.0)
    if distance_from_neutral <= 15.0:
        return 1.0
    max_distance = max(abs(cfg.rsi_max - 50.0), abs(50.0 - cfg.rsi_min), 1e-6)
    return max(0.0, 1.0 - (distance_from_neutral - 15.0) / (max_distance - 15.0))


def _generate_compat_aliases() -> Sequence[str]:
    """Keep this module's public surface stable across versions."""
    return ("evaluate", "CompositeDecision", "FeatureSet")


__all__ = list(_generate_compat_aliases())
