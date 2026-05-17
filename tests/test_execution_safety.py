import pytest

from edge_bot.core.config import AppConfig
from edge_bot.data.polymarket import OrderbookSnapshot
from edge_bot.execution.router import PaperExecutor, make_executor, taker_fee_per_share


class DummyClob:
    def orderbook(self, _token_id: str) -> OrderbookSnapshot:
        return OrderbookSnapshot(
            best_bid=0.68,
            best_ask=0.70,
            best_bid_size=100.0,
            best_ask_size=100.0,
            top_ask_notional_usd=70.0,
            spread=0.02,
        )


def test_live_mode_requires_explicit_real_money_ack() -> None:
    cfg = AppConfig(mode="live")

    with pytest.raises(RuntimeError, match="LIVE_TRADING_ACK"):
        make_executor(cfg, DummyClob())  # type: ignore[arg-type]


def test_paper_executor_includes_taker_fee_in_entry_cost() -> None:
    exe = PaperExecutor(DummyClob(), taker_fee_rate=0.07)

    res = exe.buy(token_id="token", notional_usd=10.0)

    assert res.success is True
    assert res.cost_usd <= 10.0
    assert res.filled_shares < round(10.0 / 0.70, 4)
    assert res.raw["fee_usd"] > 0


def test_taker_fee_formula_matches_polymarket_shape() -> None:
    assert round(taker_fee_per_share(0.70, 0.07), 4) == 0.0147
