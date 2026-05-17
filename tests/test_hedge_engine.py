from edge_bot.core.config import HedgeConfig
from edge_bot.hedge.engine import evaluate_hedge


def cfg() -> HedgeConfig:
    return HedgeConfig()


def test_hedge_triggered_at_extreme_skew_near_close() -> None:
    d = evaluate_hedge(
        cfg(),
        main_side="UP",
        main_notional_usd=20.0,
        seconds_left=20.0,
        opposite_ask=0.06,
        skew_against_us=0.96,
    )
    assert d.place_hedge is True
    assert d.side == "DOWN"
    assert 1.0 <= d.notional_usd <= 2.0


def test_hedge_blocked_too_early() -> None:
    d = evaluate_hedge(
        cfg(),
        main_side="UP",
        main_notional_usd=20.0,
        seconds_left=120.0,
        opposite_ask=0.06,
        skew_against_us=0.96,
    )
    assert d.place_hedge is False


def test_hedge_blocked_skew_normal() -> None:
    d = evaluate_hedge(
        cfg(),
        main_side="UP",
        main_notional_usd=20.0,
        seconds_left=20.0,
        opposite_ask=0.30,
        skew_against_us=0.70,
    )
    assert d.place_hedge is False


def test_hedge_disabled() -> None:
    c = HedgeConfig(enabled=False)
    d = evaluate_hedge(
        c, main_side="UP", main_notional_usd=20.0, seconds_left=20.0, opposite_ask=0.06, skew_against_us=0.96
    )
    assert d.place_hedge is False
