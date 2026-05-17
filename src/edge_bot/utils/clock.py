"""Clock helpers (wall + monotonic + 5m bucketing)."""

from __future__ import annotations

import datetime as dt
import time

UTC = dt.UTC

BUCKET_SECONDS_5M = 300


def now_utc() -> dt.datetime:
    return dt.datetime.now(UTC)


def now_ts() -> float:
    return time.time()


def now_mono() -> float:
    return time.monotonic()


def iso_z(ts: dt.datetime | None = None) -> str:
    return (ts or now_utc()).isoformat().replace("+00:00", "Z")


def bucket_5m_start(ts: float | None = None) -> int:
    base = int(ts if ts is not None else now_ts())
    return base - (base % BUCKET_SECONDS_5M)


def bucket_5m_end(ts: float | None = None) -> int:
    return bucket_5m_start(ts) + BUCKET_SECONDS_5M


def seconds_left_in_bucket(ts: float | None = None) -> float:
    base = ts if ts is not None else now_ts()
    return max(0.0, float(bucket_5m_end(base)) - base)
