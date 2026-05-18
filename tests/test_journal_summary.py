from edge_bot.storage.journal import CloseTradeRecord, OpenTradeRecord, TradeJournal

from edge_bot.storage.journal import FillSimulationRecord, OrderbookSnapshotRecord


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



def test_reconcile_trade_official_overwrites_wrong_paper_settlement(tmp_path) -> None:
    journal = TradeJournal(tmp_path / "journal.sqlite3")
    journal.record_open(_open_trade("wrong", 100.0))
    journal.record_close(CloseTradeRecord("wrong", 150.0, 1.0, 1.0, 0.3, "paper_settled_won"))

    rec = journal.reconcile_trade_official(
        trade_id="wrong",
        winning_side="DOWN",
        reconciled_ts=200.0,
        resolution_source="polymarket_gamma",
        raw_status="resolved",
    )

    assert rec is not None
    summary = journal.summary()
    assert summary["n_trades"] == 1
    assert summary["losses"] == 1
    assert summary["total_pnl_usd"] == -0.7


def test_fill_simulation_summary_uses_official_outcome(tmp_path) -> None:
    journal = TradeJournal(tmp_path / "journal.sqlite3")
    journal.record_open(_open_trade("filled", 100.0))
    journal.record_close(CloseTradeRecord("filled", 150.0, 1.0, 1.0, 0.3, "official_settled_won"))
    journal.record_orderbook_snapshot(
        OrderbookSnapshotRecord(
            trade_id="filled",
            ts=100.0,
            market_slug="btc-updown-5m-1",
            side="UP",
            token_id="up-token",
            best_bid=0.79,
            best_ask=0.80,
            best_bid_size=10.0,
            best_ask_size=10.0,
            top_ask_notional_usd=8.0,
            spread=0.01,
            bids=[{"price": 0.79, "size": 10.0, "notional_usd": 7.9}],
            asks=[{"price": 0.80, "size": 10.0, "notional_usd": 8.0}],
        )
    )
    journal.record_fill_simulation(
        FillSimulationRecord(
            trade_id="filled",
            target_notional_usd=10.0,
            fillable=True,
            fill_ratio=1.0,
            actual_notional_usd=10.0,
            gross_notional_usd=9.9,
            best_ask=0.80,
            weighted_avg_fill_price=0.81,
            max_level_price_used=0.81,
            slippage_from_best_ask=0.01,
            estimated_fee_usd=0.1,
            estimated_shares=12.22,
            pnl_if_won=2.22,
            pnl_if_lost=-10.0,
            levels_used=[{"price": 0.80, "shares": 12.22}],
        )
    )

    summary = journal.fill_simulation_summary()

    assert summary[0]["target_notional_usd"] == 10.0
    assert summary[0]["fully_fillable"] == 1
    assert summary[0]["official_wins"] == 1
    assert summary[0]["official_simulated_pnl_usd"] == 2.22
