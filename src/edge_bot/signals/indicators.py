"""Pure-math signal indicators.

All functions take plain sequences (lists/tuples/numpy arrays) and return floats.
Designed to be unit-testable without any I/O or async.
"""

from __future__ import annotations

import math
from collections.abc import Sequence


def window_delta_usd(current_price: float, open_price: float) -> float:
    """Absolute USD delta of current vs window-open spot price."""
    return float(current_price) - float(open_price)


def window_delta_pct(current_price: float, open_price: float) -> float:
    if open_price <= 0:
        return 0.0
    return (current_price - open_price) / open_price


def zscore(values: Sequence[float], current: float | None = None) -> float:
    """Z-score of `current` relative to `values`. If `current` is None, use last value.

    Returns 0.0 when sample size < 8 or std is zero (insufficient stability).
    """
    arr = [float(v) for v in values]
    if not arr:
        return 0.0
    target = float(current) if current is not None else arr[-1]
    if len(arr) < 8:
        return 0.0
    mean = sum(arr) / len(arr)
    var = sum((v - mean) ** 2 for v in arr) / len(arr)
    std = math.sqrt(var)
    if std == 0.0:
        return 0.0
    return (target - mean) / std


def rsi(closes: Sequence[float], period: int = 14) -> float:
    """Relative Strength Index. Returns 50.0 (neutral) when not enough data."""
    arr = [float(v) for v in closes]
    if len(arr) < period + 1:
        return 50.0
    gains: list[float] = []
    losses: list[float] = []
    for i in range(1, len(arr)):
        diff = arr[i] - arr[i - 1]
        gains.append(max(diff, 0.0))
        losses.append(max(-diff, 0.0))
    gains_window = gains[-period:]
    losses_window = losses[-period:]
    avg_gain = sum(gains_window) / period
    avg_loss = sum(losses_window) / period
    if avg_loss == 0.0:
        return 100.0 if avg_gain > 0.0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def atr(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], period: int = 5) -> float:
    """Simple ATR (mean true range over `period` bars). Returns 0 if not enough data."""
    h = [float(v) for v in highs]
    lo = [float(v) for v in lows]
    c = [float(v) for v in closes]
    n = min(len(h), len(lo), len(c))
    if n < 2:
        return 0.0
    trs: list[float] = []
    for i in range(1, n):
        tr = max(
            h[i] - lo[i],
            abs(h[i] - c[i - 1]),
            abs(lo[i] - c[i - 1]),
        )
        trs.append(tr)
    if not trs:
        return 0.0
    window = trs[-period:] if len(trs) >= period else trs
    return sum(window) / len(window)


def micro_momentum_aligned(closes_1m: Sequence[float], direction_up: bool) -> bool:
    """True when last 2 of 1m closes confirm direction (last > prev for UP, < for DOWN)."""
    if len(closes_1m) < 2:
        return False
    last_close = float(closes_1m[-1])
    prev_close = float(closes_1m[-2])
    return (direction_up and last_close > prev_close) or ((not direction_up) and last_close < prev_close)


def book_skew(yes_price: float, no_price: float) -> float:
    """Skew toward YES side (0..1). Higher = more crowded on YES."""
    yp = max(0.0, float(yes_price))
    np_ = max(0.0, float(no_price))
    s = yp + np_
    if s <= 0:
        return 0.5
    return yp / s


def kelly_fraction(win_prob: float, payoff_ratio: float) -> float:
    """Standard Kelly fraction. payoff_ratio = win_amount / loss_amount.

    Returns 0.0 when inputs make no sense (degenerate Kelly).
    """
    p = max(0.0, min(1.0, float(win_prob)))
    b = max(1e-6, float(payoff_ratio))
    q = 1.0 - p
    return max(0.0, (b * p - q) / b)
