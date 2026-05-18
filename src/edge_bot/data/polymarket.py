"""Polymarket Gamma (event metadata) and CLOB (orderbook) clients.

Network calls are isolated here so tests can inject fakes.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import time
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
class OrderbookLevel:
    price: float
    size: float

    @property
    def notional_usd(self) -> float:
        return self.price * self.size


@dataclass(slots=True)
class OrderbookSnapshot:
    best_bid: float | None
    best_ask: float | None
    best_bid_size: float
    best_ask_size: float
    top_ask_notional_usd: float
    spread: float | None
    bids: tuple[OrderbookLevel, ...] = ()
    asks: tuple[OrderbookLevel, ...] = ()


@dataclass(slots=True)
class ClobWsTop:
    asset_id: str
    best_bid: float | None
    best_ask: float | None
    best_bid_size: float
    best_ask_size: float
    top_ask_notional_usd: float
    spread: float | None
    event_type: str
    exchange_ts: float | None
    received_ts: float

    @property
    def age_sec(self) -> float:
        return max(0.0, time.time() - self.received_ts)


class ClobWsObserver:
    """Observation-only Polymarket CLOB market-channel cache.

    The strategy still uses REST orderbooks for decisions and execution. This
    observer only keeps a websocket top-of-book cache so we can later compare
    REST decisions against fresher market-channel data in journal features.
    """

    def __init__(self, *, ws_url: str, enabled: bool = False) -> None:
        self.ws_url = ws_url
        self.enabled = enabled
        self._lock = threading.Lock()
        self._asset_ids: set[str] = set()
        self._tops: dict[str, ClobWsTop] = {}
        self._version = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def subscribe(self, asset_ids: list[str] | tuple[str, ...]) -> None:
        if not self.enabled:
            return
        clean = {str(x) for x in asset_ids if str(x)}
        if not clean:
            return
        with self._lock:
            before = set(self._asset_ids)
            if clean != before:
                self._asset_ids = clean
                self._tops = {asset_id: top for asset_id, top in self._tops.items() if asset_id in clean}
                self._version += 1
        self._ensure_started()

    def snapshot(self, asset_id: str) -> ClobWsTop | None:
        if not self.enabled:
            return None
        with self._lock:
            return self._tops.get(str(asset_id))

    def close(self) -> None:
        self._stop.set()
        if (
            self._thread is not None
            and self._thread.is_alive()
            and threading.current_thread() is not self._thread
        ):
            self._thread.join(timeout=2.0)

    def _ensure_started(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run_thread, name="clob-ws-observer", daemon=True)
        self._thread.start()

    def _run_thread(self) -> None:
        try:
            asyncio.run(self._run_forever())
        except Exception as e:
            logger.warning("clob_ws_observer_stopped", exc_info=e)

    async def _run_forever(self) -> None:
        while not self._stop.is_set():
            try:
                await self._run_connection()
            except Exception as e:
                logger.warning("clob_ws_observer_error", exc_info=e)
                await asyncio.sleep(2.0)

    async def _run_connection(self) -> None:
        import websockets

        local_version = -1
        subscribed_asset_ids: set[str] = set()
        last_ping = 0.0
        async with websockets.connect(self.ws_url, ping_interval=None) as ws:
            while not self._stop.is_set():
                version, asset_ids = self._subscription_state()
                if asset_ids and version != local_version:
                    wanted_asset_ids = set(asset_ids)
                    if not subscribed_asset_ids:
                        await ws.send(
                            json.dumps(
                                {
                                    "type": "market",
                                    "assets_ids": asset_ids,
                                    "custom_feature_enabled": True,
                                }
                            )
                        )
                    else:
                        removed_asset_ids = sorted(subscribed_asset_ids - wanted_asset_ids)
                        added_asset_ids = sorted(wanted_asset_ids - subscribed_asset_ids)
                        if removed_asset_ids:
                            await ws.send(json.dumps({"operation": "unsubscribe", "assets_ids": removed_asset_ids}))
                        if added_asset_ids:
                            await ws.send(
                                json.dumps(
                                    {
                                        "operation": "subscribe",
                                        "assets_ids": added_asset_ids,
                                        "custom_feature_enabled": True,
                                    }
                                )
                            )
                    subscribed_asset_ids = wanted_asset_ids
                    local_version = version
                now = time.time()
                if now - last_ping >= 10.0:
                    await ws.send("PING")
                    last_ping = now
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                self._handle_message(raw)

    def _subscription_state(self) -> tuple[int, list[str]]:
        with self._lock:
            return self._version, sorted(self._asset_ids)

    def _handle_message(self, raw: str | bytes) -> None:
        if isinstance(raw, bytes):
            try:
                raw = raw.decode("utf-8")
            except UnicodeDecodeError:
                return
        if str(raw).strip().upper() == "PONG":
            return
        try:
            data = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            return
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    self._handle_event(item)
            return
        if isinstance(data, dict):
            self._handle_event(data)

    def _handle_event(self, event: dict[str, Any]) -> None:
        event_type = str(event.get("event_type") or event.get("type") or "")
        if event_type == "book":
            asset_id = str(event.get("asset_id") or "")
            if asset_id:
                self._store_top(asset_id, _top_from_book_event(asset_id, event, event_type))
            return
        if event_type in {"price_change", "tick_size_change", "last_trade_price"}:
            changes = event.get("price_changes") or event.get("changes") or []
            if isinstance(changes, list) and changes:
                for change in changes:
                    if isinstance(change, dict):
                        asset_id = str(change.get("asset_id") or change.get("token_id") or "")
                        if asset_id:
                            self._store_top(asset_id, _top_from_price_change(asset_id, change, event_type))
            elif event.get("asset_id"):
                asset_id = str(event.get("asset_id"))
                self._store_top(asset_id, _top_from_price_change(asset_id, event, event_type))
            return
        if event.get("asset_id") and (event.get("best_bid") is not None or event.get("best_ask") is not None):
            asset_id = str(event.get("asset_id"))
            self._store_top(asset_id, _top_from_price_change(asset_id, event, event_type or "top_of_book"))

    def _store_top(self, asset_id: str, top: ClobWsTop | None) -> None:
        if top is None:
            return
        with self._lock:
            self._tops[str(asset_id)] = top


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


def _parse_book_levels(rows: list[Any], *, reverse: bool) -> tuple[OrderbookLevel, ...]:
    levels: list[OrderbookLevel] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            price = float(row.get("price", 0) or 0)
            size = float(row.get("size", 0) or 0)
        except (TypeError, ValueError):
            continue
        if price <= 0.0 or size <= 0.0:
            continue
        levels.append(OrderbookLevel(price=price, size=size))
    levels.sort(key=lambda level: level.price, reverse=reverse)
    return tuple(levels)



def _top_from_book_event(asset_id: str, event: dict[str, Any], event_type: str) -> ClobWsTop | None:
    bids = _parse_book_levels(event.get("bids") or [], reverse=True)
    asks = _parse_book_levels(event.get("asks") or [], reverse=False)
    best_bid = bids[0].price if bids else None
    best_ask = asks[0].price if asks else None
    best_bid_size = bids[0].size if bids else 0.0
    best_ask_size = asks[0].size if asks else 0.0
    return _make_ws_top(
        asset_id=asset_id,
        best_bid=best_bid,
        best_ask=best_ask,
        best_bid_size=best_bid_size,
        best_ask_size=best_ask_size,
        event_type=event_type,
        exchange_ts=_event_ts_seconds(event.get("timestamp")),
    )


def _top_from_price_change(asset_id: str, change: dict[str, Any], event_type: str) -> ClobWsTop | None:
    best_bid = _positive_float(change.get("best_bid"))
    best_ask = _positive_float(change.get("best_ask"))
    if best_bid is None and best_ask is None:
        return None
    return _make_ws_top(
        asset_id=asset_id,
        best_bid=best_bid,
        best_ask=best_ask,
        best_bid_size=_positive_float(change.get("best_bid_size")) or 0.0,
        best_ask_size=_positive_float(change.get("best_ask_size")) or 0.0,
        event_type=event_type,
        exchange_ts=_event_ts_seconds(change.get("timestamp")),
    )


def _make_ws_top(
    *,
    asset_id: str,
    best_bid: float | None,
    best_ask: float | None,
    best_bid_size: float,
    best_ask_size: float,
    event_type: str,
    exchange_ts: float | None,
) -> ClobWsTop | None:
    if best_bid is None and best_ask is None:
        return None
    spread = None
    if best_bid is not None and best_ask is not None:
        spread = round(max(0.0, best_ask - best_bid), 4)
    return ClobWsTop(
        asset_id=str(asset_id),
        best_bid=best_bid,
        best_ask=best_ask,
        best_bid_size=best_bid_size,
        best_ask_size=best_ask_size,
        top_ask_notional_usd=(best_ask or 0.0) * best_ask_size,
        spread=spread,
        event_type=event_type,
        exchange_ts=exchange_ts,
        received_ts=time.time(),
    )


def _positive_float(value: Any) -> float | None:
    parsed = _to_float(value)
    if parsed is None or parsed <= 0.0:
        return None
    return parsed


def _event_ts_seconds(value: Any) -> float | None:
    parsed = _to_float(value)
    if parsed is None or parsed <= 0.0:
        return None
    return parsed / 1000.0 if parsed > 10_000_000_000 else parsed


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

        bids = _parse_book_levels(data.get("bids") or [], reverse=True)
        asks = _parse_book_levels(data.get("asks") or [], reverse=False)
        best_bid = bids[0].price if bids else None
        best_bid_size = bids[0].size if bids else 0.0
        best_ask = asks[0].price if asks else None
        best_ask_size = asks[0].size if asks else 0.0

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
            bids=bids,
            asks=asks,
        )
