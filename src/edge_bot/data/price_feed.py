"""BTC spot price feed (Coinbase primary, Binance fallback).

Provides:
  - current_price() — latest mid (cached, monotonic-fresh).
  - window_open_price(window_start_ts) — open of 5m bucket.
  - candles_1m(n) — recent 1m closes for momentum.
  - candles_5m(n) — recent 5m H/L/C for ATR.

REST-based for simplicity. Current Polymarket BTC 5m markets resolve from
Chainlink BTC/USD, so this feed is a tradable proxy signal/paper approximation,
not the authoritative settlement source.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

COINBASE_REST = "https://api.exchange.coinbase.com"
BINANCE_REST = "https://api.binance.com"


@dataclass(slots=True)
class Candle:
    open_ts: int
    open: float
    high: float
    low: float
    close: float
    volume: float


class PriceFeed:
    def __init__(
        self,
        *,
        primary: str = "coinbase",
        fallback: tuple[str, ...] = ("binance",),
        staleness_max_sec: float = 4.0,
    ) -> None:
        self.primary = primary
        self.fallback = fallback
        self.staleness_max_sec = staleness_max_sec
        self._last_price: float = 0.0
        self._last_price_ts: float = 0.0
        self._candles_5m: deque[Candle] = deque(maxlen=24)
        self._candles_1m: deque[Candle] = deque(maxlen=10)
        self._http = httpx.Client(timeout=4.0)

    # ---------------------- current price ----------------------

    def current_price(self) -> float | None:
        for src in (self.primary, *self.fallback):
            try:
                if src == "coinbase":
                    px = self._coinbase_spot()
                elif src == "binance":
                    px = self._binance_spot()
                else:
                    continue
                if px is not None and px > 0:
                    self._last_price = float(px)
                    self._last_price_ts = time.time()
                    return self._last_price
            except Exception as e:
                logger.warning("price_feed_error_%s", src, exc_info=e)
        return self._cached_or_none()

    def _cached_or_none(self) -> float | None:
        if self._last_price > 0 and (time.time() - self._last_price_ts) < self.staleness_max_sec:
            return self._last_price
        return None

    def _coinbase_spot(self) -> float | None:
        r = self._http.get(f"{COINBASE_REST}/products/BTC-USD/ticker", timeout=3.0)
        r.raise_for_status()
        data = r.json() or {}
        raw = data.get("price")
        return float(raw) if raw is not None else None

    def _binance_spot(self) -> float | None:
        r = self._http.get(f"{BINANCE_REST}/api/v3/ticker/price", params={"symbol": "BTCUSDT"}, timeout=3.0)
        r.raise_for_status()
        data = r.json() or {}
        raw = data.get("price")
        return float(raw) if raw is not None else None

    # ---------------------- candles ----------------------
    #
    # Strategy: try Binance first (faster updates, wider history) but
    # fall back to Coinbase REST when Binance is unavailable (geo-blocking,
    # 451 etc.). Coinbase /candles returns rows shaped [time, low, high, open, close, volume]
    # newest-first; we normalize to oldest-first to match Binance.

    def fetch_candles_1m(self, n: int = 6) -> list[Candle]:
        candles = self._fetch_binance_klines("1m", n)
        if not candles:
            candles = self._fetch_coinbase_candles(60, n)
        if candles:
            self._candles_1m = deque(candles, maxlen=max(10, n))
            return candles
        return list(self._candles_1m)

    def fetch_candles_5m(self, n: int = 12) -> list[Candle]:
        candles = self._fetch_binance_klines("5m", n)
        if not candles:
            candles = self._fetch_coinbase_candles(300, n)
        if candles:
            self._candles_5m = deque(candles, maxlen=max(24, n))
            return candles
        return list(self._candles_5m)

    def window_open_price(self, window_start_ts: int) -> float | None:
        # 1) Binance 5m kline that starts exactly at window_start_ts (cheap, when reachable)
        # 2) Coinbase /candles for that 5m bucket (only published after bucket closes)
        # 3) 1m kline whose open_ts == window_start_ts (works mid-bucket if 1m feed is live)
        # 4) Last cached 5m candle close ~= next bucket open (warm-start fallback)
        for fn in (
            self._window_open_binance,
            self._window_open_coinbase,
            self._window_open_from_1m_coinbase,
            self._window_open_from_cache,
            self._window_open_prev_5m_close,
        ):
            try:
                px = fn(window_start_ts)
            except Exception as e:
                logger.warning("window_open_error_%s", fn.__name__, exc_info=e)
                continue
            if px is not None and px > 0:
                return float(px)
        return None

    # ---------------------- helpers ----------------------

    def _fetch_binance_klines(self, interval: str, n: int) -> list[Candle]:
        try:
            r = self._http.get(
                f"{BINANCE_REST}/api/v3/klines",
                params={"symbol": "BTCUSDT", "interval": interval, "limit": n},
                timeout=3.0,
            )
            r.raise_for_status()
            rows = r.json() or []
            return [_binance_kline_to_candle(row) for row in rows]
        except Exception as e:
            logger.warning("binance_klines_%s_error", interval, exc_info=e)
            return []

    def _fetch_coinbase_candles(self, granularity_sec: int, n: int) -> list[Candle]:
        try:
            r = self._http.get(
                f"{COINBASE_REST}/products/BTC-USD/candles",
                params={"granularity": granularity_sec},
                timeout=3.0,
            )
            r.raise_for_status()
            rows = r.json() or []
            # Newest-first -> oldest-first, take last n.
            candles = [_coinbase_row_to_candle(row) for row in reversed(rows[:n])]
            return [c for c in candles if c is not None]
        except Exception as e:
            logger.warning("coinbase_klines_%ds_error", granularity_sec, exc_info=e)
            return []

    def _window_open_binance(self, window_start_ts: int) -> float | None:
        r = self._http.get(
            f"{BINANCE_REST}/api/v3/klines",
            params={
                "symbol": "BTCUSDT",
                "interval": "5m",
                "startTime": int(window_start_ts) * 1000,
                "limit": 1,
            },
            timeout=3.0,
        )
        r.raise_for_status()
        rows = r.json() or []
        if rows:
            return float(rows[0][1])
        return None

    def _window_open_coinbase(self, window_start_ts: int) -> float | None:
        import datetime as dt

        start_iso = dt.datetime.fromtimestamp(window_start_ts, dt.UTC).isoformat()
        end_iso = dt.datetime.fromtimestamp(window_start_ts + 300, dt.UTC).isoformat()
        r = self._http.get(
            f"{COINBASE_REST}/products/BTC-USD/candles",
            params={"granularity": 300, "start": start_iso, "end": end_iso},
            timeout=3.0,
        )
        r.raise_for_status()
        rows = r.json() or []
        if rows:
            # Coinbase candle = [time, low, high, open, close, volume]
            return float(rows[0][3])
        return None

    def _window_open_from_cache(self, window_start_ts: int) -> float | None:
        for c in self._candles_5m:
            if c.open_ts == window_start_ts:
                return c.open
        return None

    def _window_open_from_1m_coinbase(self, window_start_ts: int) -> float | None:
        # Pull recent 1m candles; the first candle whose open_ts == bucket start
        # has the same open as the 5m bucket.
        candles = self._candles_1m or self._fetch_coinbase_candles(60, 6)
        for c in candles:
            if c.open_ts == window_start_ts:
                return c.open
        return None

    def _window_open_prev_5m_close(self, window_start_ts: int) -> float | None:
        # If we have a cached 5m candle that closed at window_start_ts, its close
        # is a tight approximation of the new bucket's open price.
        prev_open = window_start_ts - 300
        for c in self._candles_5m:
            if c.open_ts == prev_open:
                return c.close
        return None


def _binance_kline_to_candle(row: list) -> Candle:
    return Candle(
        open_ts=int(row[0]) // 1000,
        open=float(row[1]),
        high=float(row[2]),
        low=float(row[3]),
        close=float(row[4]),
        volume=float(row[5]),
    )


def _coinbase_row_to_candle(row: list) -> Candle | None:
    # Coinbase: [time, low, high, open, close, volume].
    try:
        return Candle(
            open_ts=int(row[0]),
            open=float(row[3]),
            high=float(row[2]),
            low=float(row[1]),
            close=float(row[4]),
            volume=float(row[5]),
        )
    except (IndexError, TypeError, ValueError):
        return None
