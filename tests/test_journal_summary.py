from edge_bot.storage.journal import CloseTradeRecord, OpenTradeRecord, TradeJournal


def _open_trade(trade_id: str, entry_ts: float) -> OpenTradeRecord:
    return OpenTradeRecord(
        trade_id=trade_id,
        mode="paper",
        market_slug="btc-updown-5m-1",
        side="UP",
        is_hedge=False,
        parent_trade_id=None,
        entry_ts=entry_ts,
        entry_price=0.7,
        shares=1.0,
        cost_usd=0.7,
        confidence=0.8,
        features={},
        extra={},
    )


def test_summary_since_ts_filters_closed_trades(tmp_path) -> None:
    journal = TradeJournal(tmp_path / "journal.sqlite3")
    journal.record_open(_open_trade("old", 100.0))
    journal.record_open(_open_trade("new", 200.0))
    journal.record_close(CloseTradeRecord("old", 150.0, 1.0, 1.0, 0.3, "won"))
    journal.record_close(CloseTradeRecord("new", 250.0, 1.0, 1.0, 0.3, "won"))

    summary = journal.summary(since_ts=200.0)

    assert summary["n_trades"] == 1
    assert summary["wins"] == 1
    assert summary["total_pnl_usd"] == 0.3
