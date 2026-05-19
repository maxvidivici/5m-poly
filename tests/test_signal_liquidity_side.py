from edge_bot.core.config import AppConfig, DataConfig, StorageConfig
from edge_bot.core.orchestrator import Orchestrator
from edge_bot.data.polymarket import ClobWsTop, MarketSnapshot, OrderbookSnapshot
from edge_bot.data.price_feed import Candle


class DummyPriceFeed:
    def fetch_candles_1m(self, n: int = 6) -> list[Candle]:
        return [
            Candle(0, 100_000, 100_000, 100_000, 100_000, 0),
            Candle(60, 100_040, 100_040, 100_040, 100_040, 0),
            Candle(120, 100_080, 100_080, 100_080, 100_080, 0),
            Candle(180, 100_110, 100_110, 100_110, 100_110, 0),
            Candle(240, 100_120, 100_120, 100_120, 100_120, 0),
        ]

    def fetch_candles_5m(self, n: int = 12) -> list[Candle]:
        return [Candle(i * 300, 100_000, 100_150, 99_950, 100_020 + i, 0) for i in range(12)]

    def current_price(self) -> float:
        return 100_120.0

    def window_open_price(self, _window_start: int) -> float:
        return 100_000.0


class DummyClob:
    def orderbook(self, token_id: str) -> OrderbookSnapshot:
        if token_id == "up-token":
            return OrderbookSnapshot(
                best_bid=0.83,
                best_ask=0.85,
                best_bid_size=100.0,
                best_ask_size=1.0,
                top_ask_notional_usd=0.85,
                spread=0.02,
            )
        return OrderbookSnapshot(
            best_bid=0.13,
            best_ask=0.15,
            best_bid_size=100.0,
            best_ask_size=1_000.0,
            top_ask_notional_usd=150.0,
            spread=0.02,
        )


def test_signal_uses_liquidity_of_directional_side(tmp_path) -> None:
    cfg = AppConfig(
        mode="paper",
        storage=StorageConfig(
            runtime_dir=tmp_path,
            journal_db=tmp_path / "journal.sqlite3",
            audit_log=tmp_path / "audit.jsonl",
            dashboard_path=tmp_path / "dashboard.txt",
        ),
    )
    orch = Orchestrator(cfg)
    orch.price_feed = DummyPriceFeed()  # type: ignore[assignment]
    orch.clob = DummyClob()  # type: ignore[assignment]

    decision = orch._evaluate_signal(
        MarketSnapshot(
            slug="btc-updown-5m-1779036600",
            end_ts=1_779_036_900,
            seconds_left=30.0,
            up_token_id="up-token",
            down_token_id="down-token",
            gamma_up_price=0.85,
            gamma_down_price=0.15,
            resolution_source="https://data.chain.link/streams/btc-usd",
        )
    )

    assert decision["enter"] is False
    assert "top_ask" in decision["reason"]


class DummyClobWs:
    def __init__(self) -> None:
        self.subscribed: tuple[str, ...] = ()
        self.tops = {
            "up-token": ClobWsTop(
                asset_id="up-token",
                best_bid=0.88,
                best_ask=0.90,
                best_bid_size=1_000.0,
                best_ask_size=1_000.0,
                top_ask_notional_usd=900.0,
                spread=0.02,
                event_type="price_change",
                exchange_ts=1_779_036_800.0,
                received_ts=1_779_036_800.0,
            )
        }

    def subscribe(self, asset_ids: tuple[str, ...]) -> None:
        self.subscribed = asset_ids

    def snapshot(self, asset_id: str) -> ClobWsTop | None:
        return self.tops.get(asset_id)

    def close(self) -> None:
        pass


def test_clob_ws_fields_are_observation_only(tmp_path) -> None:
    cfg = AppConfig(
        mode="paper",
        data=DataConfig(clob_ws_enabled=True),
        storage=StorageConfig(
            runtime_dir=tmp_path,
            journal_db=tmp_path / "journal.sqlite3",
            audit_log=tmp_path / "audit.jsonl",
            dashboard_path=tmp_path / "dashboard.txt",
        ),
    )
    orch = Orchestrator(cfg)
    orch.price_feed = DummyPriceFeed()  # type: ignore[assignment]
    orch.clob = DummyClob()  # type: ignore[assignment]
    orch.clob_ws = DummyClobWs()  # type: ignore[assignment]

    decision = orch._evaluate_signal(
        MarketSnapshot(
            slug="btc-updown-5m-1779036600",
            end_ts=1_779_036_900,
            seconds_left=30.0,
            up_token_id="up-token",
            down_token_id="down-token",
            gamma_up_price=0.85,
            gamma_down_price=0.15,
            resolution_source="https://data.chain.link/streams/btc-usd",
        )
    )

    assert decision["enter"] is False
    assert "top_ask" in decision["reason"]
    assert decision["features"]["up_ask"] == 0.85
    assert decision["features"]["clob_ws_up_seen"] == 1.0
    assert decision["features"]["clob_ws_side_ask"] == 0.90
    assert decision["features"]["clob_ws_side_ask_diff_vs_rest"] == 0.05


class DownReversalPriceFeed:
    def fetch_candles_1m(self, n: int = 6) -> list[Candle]:
        return [
            Candle(0, 100_000, 100_020, 99_980, 100_000, 0),
            Candle(60, 99_980, 99_990, 99_900, 99_950, 0),
            Candle(120, 99_940, 99_960, 99_820, 99_930, 0),
            Candle(180, 99_920, 99_940, 99_760, 99_910, 0),
            Candle(240, 99_900, 99_930, 99_800, 99_910, 0),
        ]

    def fetch_candles_5m(self, n: int = 12) -> list[Candle]:
        return [Candle(i * 300, 100_000, 100_150, 99_950, 100_020 + i, 0) for i in range(12)]

    def current_price(self) -> float:
        return 99_910.0

    def window_open_price(self, _window_start: int) -> float:
        return 100_000.0


class DeepDownClob:
    def orderbook(self, token_id: str) -> OrderbookSnapshot:
        if token_id == "down-token":
            return OrderbookSnapshot(
                best_bid=0.77,
                best_ask=0.79,
                best_bid_size=100.0,
                best_ask_size=1_000.0,
                top_ask_notional_usd=790.0,
                spread=0.02,
            )
        return OrderbookSnapshot(
            best_bid=0.19,
            best_ask=0.21,
            best_bid_size=100.0,
            best_ask_size=1_000.0,
            top_ask_notional_usd=210.0,
            spread=0.02,
        )


def test_candle_reversal_fields_are_observation_only(tmp_path) -> None:
    cfg = AppConfig(
        mode="paper",
        storage=StorageConfig(
            runtime_dir=tmp_path,
            journal_db=tmp_path / "journal.sqlite3",
            audit_log=tmp_path / "audit.jsonl",
            dashboard_path=tmp_path / "dashboard.txt",
        ),
    )
    orch = Orchestrator(cfg)
    orch.price_feed = DownReversalPriceFeed()  # type: ignore[assignment]
    orch.clob = DeepDownClob()  # type: ignore[assignment]

    decision = orch._evaluate_signal(
        MarketSnapshot(
            slug="btc-updown-5m-1779036600",
            end_ts=1_779_036_900,
            seconds_left=75.0,
            up_token_id="up-token",
            down_token_id="down-token",
            gamma_up_price=0.21,
            gamma_down_price=0.79,
            resolution_source="https://data.chain.link/streams/btc-usd",
        )
    )

    assert decision["side"] == "DOWN"
    assert decision["features"]["last_1m_lower_wick_ratio"] > 0.70
    assert decision["features"]["wick_against_side_ratio"] == decision["features"]["last_1m_lower_wick_ratio"]
    assert decision["features"]["last_1m_close_against_side"] == 1.0

