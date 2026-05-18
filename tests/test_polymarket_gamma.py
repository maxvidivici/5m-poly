from edge_bot.data.polymarket import GammaClient


def event_payload(resolution_source: str = "https://data.chain.link/streams/btc-usd") -> list[dict]:
    return [
        {
            "resolutionSource": resolution_source,
            "markets": [
                {
                    "active": True,
                    "closed": False,
                    "endDate": "2999-01-01T00:05:00Z",
                    "outcomes": '["Up", "Down"]',
                    "outcomePrices": '["0.70", "0.30"]',
                    "clobTokenIds": '["up-token", "down-token"]',
                    "resolutionSource": resolution_source,
                }
            ],
        }
    ]


class DummyResponse:
    def __init__(self, payload) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> list[dict]:
        return self.payload


def test_gamma_accepts_required_chainlink_source(monkeypatch) -> None:
    def fake_get(*_args, **_kwargs):
        return DummyResponse(event_payload())

    monkeypatch.setattr("edge_bot.data.polymarket.httpx.get", fake_get)
    market = GammaClient().resolve_current_btc_5m_market(
        ts=1_779_036_600,
        required_resolution_source="chainlink",
    )

    assert market is not None
    assert market.up_token_id == "up-token"
    assert "chain.link" in market.resolution_source


def test_gamma_rejects_wrong_resolution_source(monkeypatch) -> None:
    def fake_get(*_args, **_kwargs):
        return DummyResponse(event_payload("https://example.com/coinbase"))

    monkeypatch.setattr("edge_bot.data.polymarket.httpx.get", fake_get)

    assert (
        GammaClient().resolve_current_btc_5m_market(
            ts=1_779_036_600,
            required_resolution_source="chainlink",
        )
        is None
    )




def resolved_event_payload(winner: str = "Down") -> dict:
    prices = '["0", "1"]' if winner == "Down" else '["1", "0"]'
    return {
        "slug": "btc-updown-5m-1",
        "closed": True,
        "markets": [
            {
                "closed": True,
                "umaResolutionStatus": "resolved",
                "outcomes": '["Up", "Down"]',
                "outcomePrices": prices,
                "clobTokenIds": '["up-token", "down-token"]',
                "resolutionSource": "https://data.chain.link/streams/btc-usd",
            }
        ],
    }


def test_gamma_resolves_official_winning_side_from_slug_endpoint(monkeypatch) -> None:
    def fake_get(*_args, **_kwargs):
        return DummyResponse(resolved_event_payload("Down"))

    monkeypatch.setattr("edge_bot.data.polymarket.httpx.get", fake_get)

    resolution = GammaClient().resolve_market_resolution(
        "btc-updown-5m-1",
        required_resolution_source="chainlink",
    )

    assert resolution is not None
    assert resolution.resolved is True
    assert resolution.winning_side == "DOWN"
    assert resolution.down_price == 1.0


def test_gamma_does_not_resolve_before_market_is_closed(monkeypatch) -> None:
    payload = resolved_event_payload("Up")
    payload["closed"] = False
    payload["markets"][0]["closed"] = False
    payload["markets"][0]["umaResolutionStatus"] = ""

    def fake_get(*_args, **_kwargs):
        return DummyResponse(payload)

    monkeypatch.setattr("edge_bot.data.polymarket.httpx.get", fake_get)

    resolution = GammaClient().resolve_market_resolution("btc-updown-5m-1")

    assert resolution is not None
    assert resolution.resolved is False
    assert resolution.winning_side is None
