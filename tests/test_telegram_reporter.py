from edge_bot.notifications.telegram import build_status_report


def test_build_status_report_contains_interval_and_signal() -> None:
    text = build_status_report(
        mode="paper",
        equity=101.5,
        risk={"daily_pnl": 1.5, "daily_trades": 2, "hourly_trades": 2, "consecutive_losses": 0},
        total_summary={"n_trades": 4, "wins": 3, "losses": 1, "total_pnl_usd": 2.5},
        interval_summary={"n_trades": 1, "wins": 1, "losses": 0, "total_pnl_usd": 0.75},
        total_hedge_summary={"n_trades": 2, "wins": 0, "losses": 2, "total_pnl_usd": -2.0},
        interval_hedge_summary={"n_trades": 0, "wins": 0, "losses": 0, "total_pnl_usd": 0.0},
        total_net_summary={"n_trades": 6, "wins": 3, "losses": 3, "total_pnl_usd": 0.5},
        interval_net_summary={"n_trades": 1, "wins": 1, "losses": 0, "total_pnl_usd": 0.75},
        open_positions=1,
        last_signal={
            "side": "UP",
            "confidence": 0.81,
            "reason": "entry_signal_passed_all_filters",
            "features": {"delta_usd": 72.5, "delta_soft_min_usd": 55.0, "delta_hard_min_usd": 70.0},
        },
    )

    assert "BTC 5m Polymarket report | PAPER" in text
    assert "Last interval main strategy" in text
    assert "Total main strategy" in text
    assert "trades: 1" in text
    assert "pnl: $0.75" in text
    assert "hedge: 2 trades" in text
    assert "net pnl: $0.50" in text
    assert "side: UP" in text
    assert "soft/hard: $55/$70" in text
