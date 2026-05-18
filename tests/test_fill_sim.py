from edge_bot.data.polymarket import OrderbookLevel
from edge_bot.execution.fill_sim import simulate_buy_fill


def test_fill_simulation_takes_only_allowed_price_levels() -> None:
    asks = (
        OrderbookLevel(price=0.80, size=18.0 / 0.80),
        OrderbookLevel(price=0.81, size=20.0 / 0.81),
        OrderbookLevel(price=0.82, size=40.0 / 0.82),
    )

    sim = simulate_buy_fill(
        asks,
        target_notional_usd=50.0,
        max_level_price=0.81,
        fee_rate=0.0,
        max_avg_fill_price=None,
    )

    assert sim.fillable is False
    assert sim.actual_notional_usd == 38.0
    assert sim.gross_notional_usd == 38.0
    assert sim.max_level_price_used == 0.81
    assert len(sim.levels_used) == 2


def test_fill_simulation_completes_target_when_depth_is_allowed() -> None:
    asks = (
        OrderbookLevel(price=0.80, size=18.0 / 0.80),
        OrderbookLevel(price=0.81, size=20.0 / 0.81),
        OrderbookLevel(price=0.82, size=40.0 / 0.82),
    )

    sim = simulate_buy_fill(
        asks,
        target_notional_usd=50.0,
        max_level_price=0.82,
        fee_rate=0.0,
        max_avg_fill_price=None,
    )

    assert sim.fillable is True
    assert sim.actual_notional_usd == 50.0
    assert sim.max_level_price_used == 0.82
    assert len(sim.levels_used) == 3


def test_fill_simulation_respects_average_price_cap() -> None:
    asks = (
        OrderbookLevel(price=0.90, size=10.0 / 0.90),
        OrderbookLevel(price=0.94, size=100.0 / 0.94),
    )

    sim = simulate_buy_fill(
        asks,
        target_notional_usd=50.0,
        max_level_price=0.94,
        fee_rate=0.0,
        max_avg_fill_price=0.92,
    )

    assert sim.fillable is False
    assert sim.actual_notional_usd > 10.0
    assert sim.weighted_avg_fill_price == 0.92
