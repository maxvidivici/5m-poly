from pathlib import Path

from edge_bot.core.config import AppConfig, ExitConfig, StorageConfig
from edge_bot.core.orchestrator import OpenPosition, Orchestrator
from edge_bot.data.polymarket import MarketResolution
from edge_bot.storage.journal import OpenTradeRecord


class DummyGamma:
    def __init__(self, winning_side: str | None = "DOWN") -> None:
        self.winning_side = winning_side

    def resolve_current_btc_5m_market(self, **_kwargs):
        return None

    def resolve_market_resolution(self, slug: str, **_kwargs):
        if self.winning_side is None:
            return MarketResolution(slug, False, None, "https://data.chain.link/streams/btc-usd", "")
        return MarketResolution(
            slug,
            True,
            self.winning_side,
            "https://data.chain.link/streams/btc-usd",
            "resolved",
            up_price=1.0 if self.winning_side == "UP" else 0.0,
            down_price=1.0 if self.winning_side == "DOWN" else 0.0,
        )


class DummyPriceFeed:
    def current_price(self) -> float:
        return 100_100.0

    def window_open_price(self, _window_start: int) -> float:
        return 100_000.0


def make_cfg(tmp_path: Path) -> AppConfig:
    return AppConfig(
        mode="paper",
        exit_=ExitConfig(paper_settle_on_expiry=True),
        storage=StorageConfig(
            runtime_dir=tmp_path,
            journal_db=tmp_path / "journal.sqlite3",
            audit_log=tmp_path / "audit.jsonl",
            dashboard_path=tmp_path / "dashboard.txt",
        ),
    )


def _seed_open_position(orch: Orchestrator, *, side: str = "UP") -> None:
    orch.open_positions["t1"] = OpenPosition(
        trade_id="t1",
        side=side,
        token_id="up-token",
        entry_price=0.80,
        shares=5.0,
        cost_usd=4.0,
        market_end_ts=0.0,
        market_slug="btc-updown-5m-1779036600",
    )
    orch.journal.record_open(
        OpenTradeRecord(
            trade_id="t1",
            mode="paper",
            market_slug="btc-updown-5m-1779036600",
            side=side,
            is_hedge=False,
            parent_trade_id=None,
            entry_ts=0.0,
            entry_price=0.80,
            shares=5.0,
            cost_usd=4.0,
            confidence=0.7,
            features={},
        )
    )
    orch.risk.record_trade_open()


def test_expired_paper_position_uses_official_outcome_not_spot_price(tmp_path) -> None:
    orch = Orchestrator(make_cfg(tmp_path))
    orch.gamma = DummyGamma(winning_side="DOWN")  # type: ignore[assignment]
    orch.price_feed = DummyPriceFeed()  # spot says UP, official says DOWN
    _seed_open_position(orch, side="UP")

    orch._tick()

    assert orch.open_positions == {}
    summary = orch.journal.summary()
    assert summary["n_trades"] == 1
    assert summary["losses"] == 1
    assert summary["total_pnl_usd"] == -4.0


def test_expired_paper_position_waits_for_official_resolution(tmp_path) -> None:
    orch = Orchestrator(make_cfg(tmp_path))
    orch.gamma = DummyGamma(winning_side=None)  # type: ignore[assignment]
    _seed_open_position(orch, side="UP")

    orch._tick()

    assert "t1" in orch.open_positions
    assert orch.journal.summary()["n_trades"] == 0
