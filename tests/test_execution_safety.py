import pytest

from edge_bot.core.config import AppConfig
from edge_bot.execution.router import make_executor


class DummyClob:
    pass


def test_live_mode_requires_explicit_real_money_ack() -> None:
    cfg = AppConfig(mode="live")

    with pytest.raises(RuntimeError, match="LIVE_TRADING_ACK"):
        make_executor(cfg, DummyClob())  # type: ignore[arg-type]

