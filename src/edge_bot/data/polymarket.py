"""Polymarket Gamma (event metadata) and CLOB (orderbook) clients.

Network calls are isolated here so tests can inject fakes.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

import httpx

from ..utils.clock import bucket_5m_start

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class MarketSnapshot:
    slug: str
    end_ts: float
    seconds_left: float
    up_token_id: str
    down_token_id: str
    gamma_up_price: float
    gamma_down_price: float
    resolution_source: str


@dataclass(slots=True)
class MarketResolution:
    slug: str
    resolved: bool
    winning_side: str | None
    resolution_source: str
    raw_status: str
    up_price: float | None = None
    down_price: float | None = None


@dataclass(slots=True)
class OrderbookSnapshot:
    best_bid: float | None
    best_ask: float | None
    best_bid_size: float
    best_ask_size: float
    top_ask_notional_usd: float
    spread: float | None


def _parse_jsonish(v: Any) -> list[Any]:
    if isinstance(v, list):
        return v
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:
            return [x.strip() for x in v.split(",") if x.strip()]
    return []


def _source_matches(actual: str, required: str) -> bool:
    actual_norm = re.sub(r"[^a-z0-9]+", "", actual.lower())
    required_norm = re.sub(r"[^a-z0-9]+", "", required.lower())
    return bool(required_norm and required_norm in actual_norm)


def _outcome_side_indices(outcomes: list[Any]) -> tuple[int, int]:
    labels = [str(x).lower() for x in outcomes[:2]] if outcomes else []
    up_i, down_i = 0, 1
    if len(labels) >= 2 and ("up" in labels[1] or "yes" in labels[1]):
        up_i, down_i = 1, 0
    return up_i, down_i


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class GammaClient:
    def __init__(self, base_url: str = "https://gamma-api.polymarket.com", timeout: float = 8.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def fetch_event(self, slug: str) -> dict[str, Any] | None:
        url = f"{self.base_url}/events"
        try:
            r = httpx.get(url, params={"slug": slug}, timeout=self.timeout)
            r.raise_for_status()
            arr = r.json()
        except Exception as e:
            logger.warning("gamma_event_error", exc_info=e)
            return None
        if not arr:
            return None
        return arr[0] if isinstance(arr, list) else None

    def fetch_event_by_slug(self, slug: str) -> dict[str, Any] | None:
        # Prefer the documented slug endpoint, then fall back to the list filter.
        url = f"{self.base_url}/events/slug/{slug}"
        try:
            r = httpx.get(url, timeout=self.timeout, headers={"User-Agent": "edge-bot/0.1"})
            r.raise_for_status()
            data = r.json()
            if isinstance(data, dict):
                return data
        except Exception as e:
            logger.info("gamma_event_slug_error slug=%s", slug, exc_info=e)
        return self.fetch_event(slug)

    def resolve_market_resolution(
        self,
        slug: str,
        *,
        required_resolution_source: str | None = None,
    ) -> MarketResolution | None:
        ev = self.fetch_event_by_slug(slug)
        if not ev:
            return None
        mkts = ev.get("markets") or []
        if not mkts:
            return None
        m = mkts[0]
        resolution_source = str(m.get("resolutionSource") or ev.get("resolutionSource") or "")
        if required_resolution_source and not _source_matches(resolution_source, required_resolution_source):
            logger.warning(
                "market_resolution_source_mismatch slug=%s expected=%s actual=%s",
                slug,
                required_resolution_source,
                resolution_source,
            )
            return None

        outcomes = _parse_jsonish(m.get("outcomes") or [])
        prices = _parse_jsonish(m.get("outcomePrices") or [])
        if len(outcomes) < 2 or len(prices) < 2:
            return MarketResolution(slug, False, None, resolution_source, str(m.get("umaResolutionStatus") or ""))

        up_i, down_i = _outcome_side_indices(outcomes)
        up_price = _to_float(prices[up_i])
        down_price = _to_float(prices[down_i])
        raw_status = str(m.get("umaResolutionStatus") or ev.get("umaResolutionStatus") or "")
        closed = m.get("closed") is True or ev.get("closed") is True

        winning_side: str | None = None
        if up_price is not None and up_price >= 0.999:
            winning_side = "UP"
        elif down_price is not None and down_price >= 0.999:
            winning_side = "DOWN"

        resolved = bool(winning_side and (closed or raw_status.lower() == "resolved"))
        return MarketResolution(
            slug=slug,
            resolved=resolved,
            winning_side=winning_side if resolved else None,
            resolution_source=resolution_source,
            raw_status=raw_status,
            up_price=up_price,
            down_price=down_price,
        )

    def resolve_current_btc_5m_market(
        self,
        *,
        ts: float | None = None,
        required_resolution_source: str | None = None,
    ) -> MarketSnapshot | None:
        bucket = bucket_5m_start(ts)
        slug = f"btc-updown-5m-{bucket}"
        ev = self.fetch_event(slug)
        if not ev:
            return None

        mkts = ev.get("markets") or []
        if not mkts:
            return None
        m = mkts[0]
        if m.get("closed") is True or m.get("active") is False:
            return None

        resolution_source = str(m.get("resolutionSource") or ev.get("resolutionSource") or "")
        if required_resolution_source and not _source_matches(resolution_source, required_resolution_source):
            logger.warning(
                "market_resolution_source_mismatch slug=%s expected=%s actual=%s",
                slug,
                required_resolution_source,
                resolution_source,
            )
            return None

        outcomes = _parse_jsonish(m.get("outcomes") or [])
        prices = _parse_jsonish(m.get("outcomePrices") or [])
        tokens = _parse_jsonish(m.get("clobTokenIds") or [])
        if len(prices) < 2 or len(tokens) < 2:
            return None

        up_i, down_i = _outcome_side_indices(outcomes)

        end_iso = str(m.get("endDate") or m.get("endDateIso") or "")
        try:
            import datetime as dt

            end_ts = dt.datetime.fromisoformat(end_iso.replace("Z", "+00:00")).timestamp()
        except Exception:
            return None

        import time as _time

        seconds_left = max(0.0, end_ts - _time.time())
        if seconds_left <= 1.0:
            return None

        return MarketSnapshot(
            slug=slug,
            end_ts=end_ts,
            seconds_left=seconds_left,
            up_token_id=str(tokens[up_i]),
            down_token_id=str(tokens[down_i]),
            gamma_up_price=float(prices[up_i]),
            gamma_down_price=float(prices[down_i]),
            resolution_source=resolution_source,
        )


class ClobClient:
    """Thin httpx wrapper for public Polymarket CLOB endpoints (orderbook + midpoint).

    For signed order placement we delegate to py-clob-client elsewhere in the
    execution layer; that part requires private key + L2 API creds.
    """

    def __init__(self, base_url: str = "https://clob.polymarket.com", timeout: float = 6.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def orderbook(self, token_id: str) -> OrderbookSnapshot:
        url = f"{self.base_url}/book"
        try:
            r = httpx.get(url, params={"token_id": str(token_id)}, timeout=self.timeout)
            r.raise_for_status()
            data = r.json() or {}
        except Exception as e:
            logger.warning("clob_book_error", exc_info=e)
            return OrderbookSnapshot(None, None, 0.0, 0.0, 0.0, None)

        bids = data.get("bids") or []
        asks = data.get("asks") or []
        best_bid: float | None = None
        best_bid_size = 0.0
        best_ask: float | None = None
        best_ask_size = 0.0
        for b in bids:
            try:
                p = float(b.get("price", 0) or 0)
                s = float(b.get("size", 0) or 0)
            except (TypeError, ValueError):
                continue
            if best_bid is None or p > best_bid:
                best_bid = p
                best_bid_size = s
        for a in asks:
            try:
                p = float(a.get("price", 0) or 0)
                s = float(a.get("size", 0) or 0)
            except (TypeError, ValueError):
                continue
            if best_ask is None or p < best_ask:
                best_ask = p
                best_ask_size = s

        spread = None
        if best_bid is not None and best_ask is not None:
            spread = max(0.0, best_ask - best_bid)

        top_ask_notional = (best_ask or 0.0) * best_ask_size
        return OrderbookSnapshot(
            best_bid=best_bid,
            best_ask=best_ask,
            best_bid_size=best_bid_size,
            best_ask_size=best_ask_size,
            top_ask_notional_usd=top_ask_notional,
            spread=spread,
        )
