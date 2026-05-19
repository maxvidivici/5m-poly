from edge_bot.core.config import SignalConfig
from edge_bot.signals.composite import FeatureSet, evaluate


def baseline_features(**overrides) -> FeatureSet:
    base = FeatureSet(
        current_btc=100120.0,
        window_open_btc=100000.0,
        closes_1m=(100000.0, 100050.0, 100090.0, 100110.0, 100120.0),
        highs_5m=(100200.0, 100250.0, 100300.0, 100280.0, 100250.0),
        lows_5m=(99900.0, 100000.0, 100100.0, 100150.0, 100100.0),
        closes_5m=(100100.0, 100150.0, 100200.0, 100200.0, 100120.0),
        historical_deltas_pct=(0.05, -0.1, 0.08, -0.04, 0.06, -0.07, 0.05, -0.05, 0.04, -0.06),
        seconds_left=45.0,
        clob_up_ask=0.85,
        clob_down_ask=0.15,
        clob_up_bid=0.83,
        clob_down_bid=0.13,
        top_ask_notional_usd=100.0,
    )
    for k, v in overrides.items():
        object.__setattr__(base, k, v)
    return base


def cfg() -> SignalConfig:
    return SignalConfig()


def test_entry_passes_on_clean_setup() -> None:
    d = evaluate(baseline_features(), cfg())
    assert d.enter is True
    assert d.side == "UP"
    assert d.confidence >= 0.45


def test_skip_when_btc_move_too_small() -> None:
    d = evaluate(baseline_features(current_btc=100040.0), cfg())
    assert d.enter is False
    assert "btc_move" in d.reason


def test_skip_when_too_early() -> None:
    d = evaluate(baseline_features(seconds_left=180.0), cfg())
    assert d.enter is False
    assert "too_early" in d.reason


def test_skip_when_too_late() -> None:
    d = evaluate(baseline_features(seconds_left=5.0), cfg())
    assert d.enter is False
    assert "too_late" in d.reason


def test_skip_when_ask_below_threshold() -> None:
    d = evaluate(baseline_features(clob_up_ask=0.55), cfg())
    assert d.enter is False
    assert "side_ask" in d.reason


def test_skip_when_spread_too_wide() -> None:
    d = evaluate(baseline_features(clob_up_bid=0.40), cfg())
    assert d.enter is False
    assert "spread" in d.reason


def test_zscore_overheat_is_soft_confidence_penalty() -> None:
    bad_hist = (0.0,) * 8 + (0.001,) * 4
    soft_cfg = SignalConfig(zscore_hard_block_enabled=False)
    d = evaluate(baseline_features(historical_deltas_pct=bad_hist), soft_cfg)
    clean = evaluate(baseline_features(), soft_cfg)

    assert d.features["zscore_score"] < clean.features["zscore_score"]
    assert "zscore" not in d.reason


def test_zscore_hard_block_rejects_extreme_overheat() -> None:
    bad_hist = (0.0,) * 8 + (0.001,) * 4

    d = evaluate(baseline_features(historical_deltas_pct=bad_hist), cfg())

    assert d.enter is False
    assert d.features["zscore_hard_block_abs"] == cfg().zscore_hard_block_abs
    assert "zscore_abs" in d.reason


def test_rsi_extreme_is_soft_bonus_not_hard_block() -> None:
    closes = tuple(100000.0 + i * 10.0 for i in range(16))

    d = evaluate(baseline_features(closes_1m=closes), cfg())

    assert d.enter is True
    assert d.features["rsi"] > cfg().rsi_max
    assert d.features["rsi_score"] == 0.0


def test_atr_overheat_is_soft_confidence_penalty_when_hard_block_disabled() -> None:
    feats = baseline_features(
        highs_5m=(100200.0, 100250.0, 100300.0, 100280.0, 100900.0),
        lows_5m=(99900.0, 100000.0, 100100.0, 100150.0, 99900.0),
        closes_5m=(100100.0, 100150.0, 100200.0, 100200.0, 100120.0),
    )

    d = evaluate(feats, SignalConfig(atr_hard_block_enabled=False))

    assert d.enter is True
    assert d.features["atr_score"] < 1.0
    assert "range" not in d.reason


def test_atr_hard_block_rejects_extreme_volatility() -> None:
    feats = baseline_features(
        highs_5m=(100200.0, 100250.0, 100300.0, 100280.0, 100900.0),
        lows_5m=(99900.0, 100000.0, 100100.0, 100150.0, 99900.0),
        closes_5m=(100100.0, 100150.0, 100200.0, 100200.0, 100120.0),
    )

    d = evaluate(feats, cfg())

    assert d.enter is False
    assert d.features["atr_pct"] >= cfg().atr_hard_block_pct
    assert "atr_pct" in d.reason


def test_high_vol_edge_guard_blocks_weak_statistical_move() -> None:
    feats = baseline_features(
        current_btc=99920.0,
        window_open_btc=100000.0,
        closes_1m=(100000.0, 99970.0, 99940.0, 99925.0, 99920.0),
        highs_5m=(100260.0, 100280.0, 100300.0, 100290.0, 100280.0),
        lows_5m=(100020.0, 100040.0, 100060.0, 100050.0, 100040.0),
        closes_5m=(100100.0, 100120.0, 100110.0, 100105.0, 99920.0),
        historical_deltas_pct=(-0.12, -0.07, -0.10, -0.06, -0.11, -0.08, -0.09, -0.07, -0.10, -0.08),
        clob_up_ask=0.31,
        clob_down_ask=0.76,
        clob_up_bid=0.30,
        clob_down_bid=0.75,
        seconds_left=68.0,
    )

    d = evaluate(feats, cfg())

    assert d.enter is False
    assert d.features["atr_pct"] >= cfg().high_vol_edge_guard_atr_pct
    assert d.features["delta_strong_ratio"] < cfg().high_vol_min_delta_strong_ratio
    assert abs(d.features["zscore"]) < cfg().high_vol_min_abs_zscore
    assert "high_vol_weak_edge" in d.reason


def test_high_vol_edge_guard_allows_significant_zscore_move() -> None:
    feats = baseline_features(
        current_btc=99925.0,
        window_open_btc=100000.0,
        closes_1m=(100000.0, 99975.0, 99950.0, 99935.0, 99925.0),
        highs_5m=(100260.0, 100280.0, 100300.0, 100290.0, 100280.0),
        lows_5m=(100020.0, 100040.0, 100060.0, 100050.0, 100040.0),
        closes_5m=(100100.0, 100120.0, 100110.0, 100105.0, 99925.0),
        historical_deltas_pct=(-0.02, -0.04, -0.03, -0.05, -0.04, -0.03, -0.02, -0.04, -0.03, -0.05),
        clob_up_ask=0.20,
        clob_down_ask=0.81,
        clob_up_bid=0.19,
        clob_down_bid=0.80,
        seconds_left=92.0,
    )

    d = evaluate(feats, SignalConfig(zscore_hard_block_enabled=False))

    assert d.enter is True
    assert d.features["atr_pct"] >= cfg().high_vol_edge_guard_atr_pct
    assert d.features["delta_strong_ratio"] < cfg().high_vol_min_delta_strong_ratio
    assert abs(d.features["zscore"]) >= cfg().high_vol_min_abs_zscore


def test_down_direction_entry() -> None:
    feats = baseline_features(
        current_btc=99880.0,
        clob_up_ask=0.15,
        clob_down_ask=0.85,
        clob_up_bid=0.13,
        clob_down_bid=0.83,
    )
    d = evaluate(feats, cfg())
    assert d.enter is True
    assert d.side == "DOWN"


def test_adaptive_delta_entry_when_market_confirms_direction() -> None:
    feats = baseline_features(
        current_btc=99933.0,
        closes_1m=(100000.0, 99980.0, 99955.0, 99940.0, 99933.0),
        clob_up_ask=0.30,
        clob_down_ask=0.70,
        clob_up_bid=0.28,
        clob_down_bid=0.68,
        seconds_left=61.0,
    )

    d = evaluate(feats, cfg())

    assert d.enter is True
    assert d.side == "DOWN"
    assert d.features["delta_hard_min_usd"] < 70.0
    assert d.confidence >= 0.72


def test_soft_delta_rejects_when_market_confirmation_is_weak() -> None:
    feats = baseline_features(
        current_btc=99910.0,
        closes_1m=(100000.0, 99980.0, 99955.0, 99935.0, 99910.0),
        highs_5m=(100300.0, 100320.0, 100340.0, 100330.0, 100310.0),
        lows_5m=(99700.0, 99720.0, 99740.0, 99730.0, 99710.0),
        closes_5m=(100000.0, 100010.0, 100000.0, 100005.0, 99910.0),
        clob_up_ask=0.31,
        clob_down_ask=0.69,
        clob_up_bid=0.29,
        clob_down_bid=0.67,
    )

    d = evaluate(feats, cfg())

    assert d.enter is False
    assert d.features["soft_delta_zone"] == 1.0
    assert "soft_delta_side_ask" in d.reason


def test_adaptive_delta_rejects_small_move_in_high_volatility() -> None:
    feats = baseline_features(
        current_btc=99933.0,
        closes_1m=(100000.0, 99980.0, 99955.0, 99940.0, 99933.0),
        highs_5m=(100300.0, 100320.0, 100340.0, 100330.0, 100310.0),
        lows_5m=(99700.0, 99720.0, 99740.0, 99730.0, 99710.0),
        closes_5m=(100000.0, 100010.0, 100000.0, 100005.0, 99933.0),
        clob_up_ask=0.30,
        clob_down_ask=0.83,
        clob_up_bid=0.28,
        clob_down_bid=0.81,
    )

    d = evaluate(feats, cfg())

    assert d.enter is False
    assert d.features["delta_soft_min_usd"] > 67.0
    assert "below_soft" in d.reason


def test_rejects_below_soft_delta_floor_even_with_strong_market_confirmation() -> None:
    feats = baseline_features(
        current_btc=99946.0,
        closes_1m=(100000.0, 99980.0, 99965.0, 99955.0, 99946.0),
        clob_up_ask=0.17,
        clob_down_ask=0.83,
        clob_up_bid=0.15,
        clob_down_bid=0.81,
    )

    d = evaluate(feats, cfg())

    assert d.enter is False
    assert "below_soft" in d.reason
