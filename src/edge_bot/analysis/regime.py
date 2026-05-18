"""Market-regime tagging and report buckets."""

from __future__ import annotations

from typing import Any


def regime_tags_from_features(features: dict[str, Any], signal_cfg: object | None = None) -> list[str]:
    delta = _float(features.get("delta_usd"))
    abs_delta = abs(delta)
    soft = _float(features.get("delta_soft_min_usd"), _cfg(signal_cfg, "window_delta_usd_soft_min", 55.0))
    side = "UP" if delta >= 0 else "DOWN"

    tags: list[str] = []
    if abs_delta < soft:
        tags.append("sideways")
    elif side == "UP":
        tags.append("trend_up")
    else:
        tags.append("trend_down")

    atr_pct = _float(features.get("atr_pct"))
    atr_min = _cfg(signal_cfg, "atr_score_min_pct", 0.06)
    atr_high = _cfg(signal_cfg, "atr_score_max_pct", 0.22)
    if atr_pct > 0 and atr_pct < atr_min:
        tags.append("low_volatility")
    elif atr_pct >= atr_high:
        tags.append("high_volatility")
    else:
        tags.append("normal_volatility")

    seconds_left = _float(features.get("seconds_left"))
    side_ask = _float(features.get("side_ask"))
    spread = _float(features.get("spread"))
    if seconds_left <= 30.0 or side_ask >= 0.90 or spread >= 0.02:
        tags.append("late_reversal_risk")

    if _float(features.get("soft_delta_zone")) >= 0.5:
        tags.append("soft_delta_entry")
    if _float(features.get("micro_momentum_aligned")) >= 0.5:
        tags.append("momentum_aligned")
    else:
        tags.append("momentum_not_aligned")

    return tags


def bucket_trade_features(features: dict[str, Any]) -> dict[str, str]:
    abs_delta = abs(_float(features.get("delta_usd")))
    side_ask = _float(features.get("side_ask"))
    seconds_left = _float(features.get("seconds_left"))
    liquidity = _float(features.get("top_ask_notional_usd"))
    return {
        "delta_bucket": _bucket(abs_delta, ((55, "<55"), (65, "55-65"), (75, "65-75"), (100, "75-100")), "100+"),
        "ask_bucket": _bucket(side_ask, ((0.68, "<0.68"), (0.75, "0.68-0.75"), (0.85, "0.75-0.85"), (0.95, "0.85-0.95")), "0.95+"),
        "seconds_left_bucket": _bucket(seconds_left, ((10, "<10"), (30, "10-30"), (60, "30-60"), (90, "60-90"), (120, "90-120")), "120+"),
        "liquidity_bucket": _bucket(liquidity, ((25, "<25"), (50, "25-50"), (100, "50-100")), "100+"),
    }


def _bucket(value: float, boundaries: tuple[tuple[float, str], ...], fallback: str) -> str:
    for ceiling, label in boundaries:
        if value < ceiling:
            return label
    return fallback


def _cfg(cfg: object | None, name: str, default: float) -> float:
    if cfg is None:
        return default
    return _float(getattr(cfg, name, default), default)


def _float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default
