from edge_bot.data.polymarket import _parse_book_levels


def test_orderbook_depth_levels_are_sorted_and_filtered() -> None:
    rows = [
        {"price": "0.82", "size": "4"},
        {"price": "0.80", "size": "5"},
        {"price": "0.00", "size": "10"},
    ]

    asks = _parse_book_levels(rows, reverse=False)
    bids = _parse_book_levels(rows, reverse=True)

    assert [level.price for level in asks] == [0.80, 0.82]
    assert [level.size for level in asks] == [5.0, 4.0]
    assert [level.price for level in bids] == [0.82, 0.80]
