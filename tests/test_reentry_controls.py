from pathlib import Path

from edge_bot.core.config import AppConfig, RiskConfig, SignalConfig, StorageConfig
from edge_bot.core.orchestrator import OpenPosition, Orchestrator
from edge_bot.data.polymarket import MarketSnapshot


def make_orch(
    tmp_path: Path,
    risk: RiskConfig | None = None,
    signal: SignalConfig | None = None,
) -> Orchestrator:
    cfg = AppConfig(
        mode="paper",
        signal=signal or SignalConfig(),
        risk=risk or RiskConfig(),
        storage=StorageConfig(
            runtime_dir=tmp_path,
            journal_db=tmp_path / "journal.sqlite3",
            audit_log=tmp_path / "audit.jsonl",
            dashboard_path=tmp_path / "dashboard.txt",
        ),
    )
    return Orchestrator(cfg)


def addon_risk(**overrides) -> RiskConfig:
    values = {
        "max_entries_per_market": 2,
        "addon_enabled": True,
        "addon_size_usd": 3.0,
        "addon_min_seconds_left": 45.0,
        "addon_min_side_ask": 0.82,
        "addon_min_delta_strong_ratio": 1.0,
        "addon_min_abs_zscore": 1.0,
        "addon_max_spread": 0.02,
    }
    values.update(overrides)
    return RiskConfig(**values)


def addon_decision(**features):
    base_features = {
        "seconds_left": 90.0,
        "side_ask": 0.84,
        "delta_strong_ratio": 1.05,
        "zscore": -1.2,
        "spread": 0.01,
        "top_ask_notional_usd": 50.0,
    }
    base_features.update(features)
    return {"side": "UP", "features": base_features}

def market() -> MarketSnapshot:
    return MarketSnapshot(
        slug="btc-updown-5m-1779036600",
        end_ts=1_779_036_900,
        seconds_left=45.0,
        up_token_id="up-token",
        down_token_id="down-token",
        gamma_up_price=0.80,
        gamma_down_price=0.20,
        resolution_source="https://data.chain.link/streams/btc-usd",
    )


def pos(i: int, *, side: str = "UP", opened_ts: float = 0.0, cost: float = 2.0) -> OpenPosition:
    return OpenPosition(
        trade_id=f"t{i}",
        side=side,
        token_id="up-token" if side == "UP" else "down-token",
        entry_price=0.80,
        shares=2.5,
        cost_usd=cost,
        market_end_ts=1_779_036_900,
        market_slug="btc-updown-5m-1779036600",
        opened_ts=opened_ts,
    )


def test_strict_addon_reentry_allowed_under_market_limits(tmp_path) -> None:
    orch = make_orch(tmp_path, addon_risk())
    orch.open_positions["t1"] = pos(1, opened_ts=0.0, cost=3.0)

    gate = orch._can_open_for_market(market(), addon_decision())

    assert gate["allowed"] is True
    assert gate["entry_kind"] == "addon"
    assert gate["size_usd"] == 3.0
    assert gate["parent_trade_id"] == "t1"


def test_reentry_blocks_fourth_entry(tmp_path) -> None:
    orch = make_orch(tmp_path)
    for i in range(3):
        orch.open_positions[f"t{i}"] = pos(i, opened_ts=0.0)

    gate = orch._can_open_for_market(market(), {"side": "UP"})

    assert gate["allowed"] is False
    assert gate["reason"] == "max_entries_per_market_hit"


def test_reentry_blocks_opposite_side(tmp_path) -> None:
    orch = make_orch(tmp_path)
    orch.open_positions["t1"] = pos(1, side="UP", opened_ts=0.0)

    gate = orch._can_open_for_market(market(), {"side": "DOWN"})

    assert gate["allowed"] is False
    assert gate["reason"] == "opposite_side_position_open"


def test_reentry_blocks_market_exposure_cap(tmp_path) -> None:
    orch = make_orch(tmp_path)
    orch.open_positions["t1"] = pos(1, opened_ts=0.0, cost=8.0)

    gate = orch._can_open_for_market(market(), {"side": "UP"})

    assert gate["allowed"] is False
    assert gate["reason"] == "max_market_exposure_hit"


def test_reentry_blocks_when_addon_disabled(tmp_path) -> None:
    orch = make_orch(tmp_path, RiskConfig(max_entries_per_market=2, addon_enabled=False))
    orch.open_positions["t1"] = pos(1, opened_ts=0.0)

    gate = orch._can_open_for_market(market(), addon_decision())

    assert gate["allowed"] is False
    assert gate["reason"] == "addon_disabled"


def test_reentry_blocks_weak_addon_signal(tmp_path) -> None:
    orch = make_orch(tmp_path, addon_risk())
    orch.open_positions["t1"] = pos(1, opened_ts=0.0)

    gate = orch._can_open_for_market(market(), addon_decision(side_ask=0.81))

    assert gate["allowed"] is False
    assert gate["reason"].startswith("addon_side_ask_")


def test_first_main_blocks_borderline_weak_signal(tmp_path) -> None:
    orch = make_orch(tmp_path)

    gate = orch._can_open_for_market(
        market(),
        addon_decision(side_ask=0.71, delta_strong_ratio=0.59),
    )

    assert gate["allowed"] is False
    assert str(gate["reason"]).startswith("borderline_weak_main_")


def test_first_main_allows_borderline_signal_with_enough_delta_ratio(tmp_path) -> None:
    orch = make_orch(tmp_path)

    gate = orch._can_open_for_market(
        market(),
        addon_decision(side_ask=0.71, delta_strong_ratio=0.60),
    )

    assert gate["allowed"] is True
    assert gate["reason"] == "ok"


def test_borderline_weak_main_guard_does_not_replace_addon_rules(tmp_path) -> None:
    orch = make_orch(tmp_path, addon_risk())
    orch.open_positions["t1"] = pos(1, opened_ts=0.0)

    gate = orch._can_open_for_market(
        market(),
        addon_decision(side_ask=0.71, delta_strong_ratio=0.59),
    )

    assert gate["allowed"] is False
    assert str(gate["reason"]).startswith("addon_side_ask_")


def test_borderline_weak_main_guard_can_be_disabled(tmp_path) -> None:
    orch = make_orch(tmp_path, signal=SignalConfig(borderline_weak_main_guard_enabled=False))

    gate = orch._can_open_for_market(
        market(),
        addon_decision(side_ask=0.71, delta_strong_ratio=0.59),
    )

    assert gate["allowed"] is True
    assert gate["reason"] == "ok"
