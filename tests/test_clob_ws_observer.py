from edge_bot.data.polymarket import ClobWsObserver


def test_clob_ws_observer_records_book_top() -> None:
    observer = ClobWsObserver(ws_url="wss://example.invalid", enabled=True)

    observer._handle_event(
        {
            "event_type": "book",
            "asset_id": "asset-1",
            "timestamp": "1779141600123",
            "bids": [{"price": "0.80", "size": "10"}, {"price": "0.82", "size": "5"}],
            "asks": [{"price": "0.85", "size": "4"}, {"price": "0.86", "size": "3"}],
        }
    )

    top = observer.snapshot("asset-1")

    assert top is not None
    assert top.best_bid == 0.82
    assert top.best_bid_size == 5.0
    assert top.best_ask == 0.85
    assert top.best_ask_size == 4.0
    assert top.top_ask_notional_usd == 3.4
    assert top.exchange_ts == 1_779_141_600.123


def test_clob_ws_observer_records_price_change_best_prices() -> None:
    observer = ClobWsObserver(ws_url="wss://example.invalid", enabled=True)

    observer._handle_event(
        {
            "event_type": "price_change",
            "price_changes": [
                {
                    "asset_id": "asset-2",
                    "best_bid": "0.73",
                    "best_ask": "0.75",
                    "timestamp": "1779141601000",
                }
            ],
        }
    )

    top = observer.snapshot("asset-2")

    assert top is not None
    assert top.best_bid == 0.73
    assert top.best_ask == 0.75
    assert top.spread == 0.02
    assert top.event_type == "price_change"
