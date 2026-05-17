from pathlib import Path

from edge_bot.core.config import AppConfig, ExitConfig, StorageConfig
from edge_bot.core.orchestrator import OpenPosition, Orchestrator
from edge_bot.storage.journal import OpenTradeRecord


class DummyGamma:
    def resolve_current_btc_5m_market(self, **_kwargs):
        return None


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


def test_expired_paper_position_settles_even_when_gamma_market_missing(tmp_path) -> None:
    orch = Orchestrator(make_cfg(tmp_path))
    orch.gamma = DummyGamma()  # type: ignore[assignment]
    orch.price_feed = DummyPriceFeed()  # type: ignore[assignment]

    orch.open_positions["t1"] = OpenPosition(
        trade_id="t1",
        side="UP",
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
            side="UP",
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

    orch._tick()

    assert orch.open_positions == {}
    summary = orch.journal.summary()
    assert summary["n_trades"] == 1
    assert summary["wins"] == 1
    assert summary["total_pnl_usd"] == 1.0
