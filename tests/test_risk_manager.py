import time

from edge_bot.core.config import RiskConfig
from edge_bot.risk.manager import RiskManager


def make_rm() -> RiskManager:
    return RiskManager(
        RiskConfig(
            start_equity_usd=100.0,
            max_position_pct=5.0,
            kelly_fraction=0.25,
            min_position_usd=1.0,
            max_position_usd=20.0,
            daily_loss_cap_pct=8.0,
            max_consecutive_losses=3,
            cooldown_after_losses_sec=10.0,
            max_trades_per_hour=4,
            max_trades_per_day=24,
            min_balance_usd=20.0,
        )
    )


def test_can_trade_initially() -> None:
    rm = make_rm()
    g = rm.can_trade()
    assert g.allowed
    assert g.reason == "ok"


def test_size_capped_to_5_pct() -> None:
    rm = make_rm()
    size = rm.size_position(confidence=0.95, side_ask=0.70)
    assert size <= 100.0 * 0.05
    assert size >= 1.0


def test_size_zero_for_no_edge() -> None:
    rm = make_rm()
    # Kelly returns 0 when p == ask (no edge over implied prob)
    assert rm.size_position(confidence=0.5, side_ask=0.5) == 0


def test_size_zero_when_probability_edge_below_minimum() -> None:
    rm = make_rm()
    assert rm.size_position(confidence=0.711, side_ask=0.70) == 0


def test_daily_loss_cap_blocks() -> None:
    rm = make_rm()
    rm.record_trade_open()
    rm.record_trade_close(-9.0)  # 9% loss vs $100 start
    g = rm.can_trade()
    assert not g.allowed
    assert "daily_loss_cap" in g.reason


def test_consecutive_losses_trigger_cooldown() -> None:
    rm = make_rm()
    for _ in range(3):
        rm.record_trade_open()
        rm.record_trade_close(-1.0)
    g = rm.can_trade()
    assert not g.allowed
    assert "cooldown_active" in g.reason


def test_consecutive_losses_recover_after_cooldown() -> None:
    rm = make_rm()
    for _ in range(3):
        rm.record_trade_open()
        rm.record_trade_close(-0.5)
    rm.state.last_loss_ts = time.time() - 11.0
    g = rm.can_trade()
    assert g.allowed


def test_min_balance_blocks() -> None:
    rm = make_rm()
    rm.record_trade_open()
    rm.record_trade_close(-85.0)
    g = rm.can_trade()
    assert not g.allowed
