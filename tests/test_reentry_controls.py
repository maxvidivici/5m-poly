from pathlib import Path

from edge_bot.core.config import AppConfig, StorageConfig
from edge_bot.core.orchestrator import OpenPosition, Orchestrator
from edge_bot.data.polymarket import MarketSnapshot


def make_orch(tmp_path: Path) -> Orchestrator:
    cfg = AppConfig(
        mode="paper",
        storage=StorageConfig(
            runtime_dir=tmp_path,
            journal_db=tmp_path / "journal.sqlite3",
            audit_log=tmp_path / "audit.jsonl",
            dashboard_path=tmp_path / "dashboard.txt",
        ),
    )
    return Orchestrator(cfg)


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


def test_reentry_allowed_under_market_limits(tmp_path) -> None:
    orch = make_orch(tmp_path)
    orch.open_positions["t1"] = pos(1, opened_ts=0.0)

    gate = orch._can_open_for_market(market(), {"side": "UP"})

    assert gate["allowed"] is True


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

