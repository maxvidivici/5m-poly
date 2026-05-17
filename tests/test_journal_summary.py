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


def _open_hedge(trade_id: str, entry_ts: float) -> OpenTradeRecord:
    rec = _open_trade(trade_id, entry_ts)
    return OpenTradeRecord(
        trade_id=rec.trade_id,
        mode=rec.mode,
        market_slug=rec.market_slug,
        side="DOWN",
        is_hedge=True,
        parent_trade_id="old",
        entry_ts=rec.entry_ts,
        entry_price=rec.entry_price,
        shares=rec.shares,
        cost_usd=rec.cost_usd,
        confidence=rec.confidence,
        features=rec.features,
        extra=rec.extra,
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


def test_summary_can_split_main_and_hedge_trades(tmp_path) -> None:
    journal = TradeJournal(tmp_path / "journal.sqlite3")
    journal.record_open(_open_trade("main", 100.0))
    journal.record_open(_open_hedge("hedge", 101.0))
    journal.record_close(CloseTradeRecord("main", 150.0, 1.0, 1.0, 0.5, "won"))
    journal.record_close(CloseTradeRecord("hedge", 150.0, 0.0, 0.0, -0.7, "lost"))

    main = journal.summary(hedge=False)
    hedge = journal.summary(hedge=True)
    net = journal.summary()

    assert main["n_trades"] == 1
    assert main["total_pnl_usd"] == 0.5
    assert hedge["n_trades"] == 1
    assert hedge["total_pnl_usd"] == -0.7
    assert net["n_trades"] == 2
    assert net["total_pnl_usd"] == -0.2
