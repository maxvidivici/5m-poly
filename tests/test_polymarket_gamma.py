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
    def __init__(self, payload: list[dict]) -> None:
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

