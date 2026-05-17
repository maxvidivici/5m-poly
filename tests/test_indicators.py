from edge_bot.signals import indicators


def test_window_delta_usd_and_pct() -> None:
    assert indicators.window_delta_usd(100100, 100000) == 100
    assert abs(indicators.window_delta_pct(100100, 100000) - 0.001) < 1e-6
    assert indicators.window_delta_pct(100, 0) == 0


def test_zscore_returns_zero_for_small_samples() -> None:
    vals = [1.0, 2.0, 3.0]
    assert indicators.zscore(vals, current=2.0) == 0.0


def test_zscore_detects_outlier() -> None:
    vals = [0.1, 0.0, -0.1, 0.2, -0.2, 0.05, -0.05, 0.0]
    z = indicators.zscore(vals, current=5.0)
    assert z > 3.0


def test_rsi_extremes() -> None:
    rising = [10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25]
    assert indicators.rsi(rising, period=14) > 99
    falling = [25, 24, 23, 22, 21, 20, 19, 18, 17, 16, 15, 14, 13, 12, 11, 10]
    assert indicators.rsi(falling, period=14) < 1


def test_rsi_neutral_when_too_short() -> None:
    assert indicators.rsi([1.0, 2.0, 3.0], period=14) == 50.0


def test_atr_increases_with_range() -> None:
    highs = [100, 101, 102, 103, 104, 105]
    lows = [99, 99, 100, 101, 102, 100]
    closes = [99.5, 100.5, 101.0, 102.5, 103.0, 104.0]
    a1 = indicators.atr(highs, lows, closes, period=5)
    highs2 = [100, 105, 110, 115, 120, 125]
    lows2 = [99, 95, 90, 85, 80, 75]
    closes2 = [99.5, 100, 105, 110, 90, 100]
    a2 = indicators.atr(highs2, lows2, closes2, period=5)
    assert a2 > a1


def test_micro_momentum_alignment() -> None:
    closes = [100.0, 100.5, 101.0]
    assert indicators.micro_momentum_aligned(closes, direction_up=True) is True
    assert indicators.micro_momentum_aligned(closes, direction_up=False) is False
    assert indicators.micro_momentum_aligned([100.0], direction_up=True) is False


def test_book_skew_balanced() -> None:
    assert indicators.book_skew(0.5, 0.5) == 0.5
    assert indicators.book_skew(0.9, 0.1) == 0.9
    assert indicators.book_skew(0, 0) == 0.5


def test_kelly_fraction_positive_edge() -> None:
    # Win prob 60%, payoff 1:1 -> kelly = (1*0.6 - 0.4)/1 = 0.2
    assert abs(indicators.kelly_fraction(0.6, 1.0) - 0.2) < 1e-6
    # No edge -> 0
    assert indicators.kelly_fraction(0.4, 1.0) == 0.0
    # Negative edge -> floored at 0
    assert indicators.kelly_fraction(0.3, 1.0) == 0.0
