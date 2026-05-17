"""SQLite trade journal."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id TEXT UNIQUE NOT NULL,
    mode TEXT NOT NULL,
    market_slug TEXT NOT NULL,
    side TEXT NOT NULL,
    is_hedge INTEGER NOT NULL DEFAULT 0,
    parent_trade_id TEXT,
    entry_ts REAL NOT NULL,
    entry_price REAL NOT NULL,
    shares REAL NOT NULL,
    cost_usd REAL NOT NULL,
    confidence REAL,
    features_json TEXT,
    exit_ts REAL,
    exit_price REAL,
    proceeds_usd REAL,
    pnl_usd REAL,
    close_reason TEXT,
    extra_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_trades_mode ON trades(mode);
CREATE INDEX IF NOT EXISTS idx_trades_market_slug ON trades(market_slug);
CREATE INDEX IF NOT EXISTS idx_trades_entry_ts ON trades(entry_ts);

CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at REAL NOT NULL,
    mode TEXT NOT NULL,
    config_json TEXT,
    ended_at REAL,
    final_equity_usd REAL,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS signal_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    market_slug TEXT NOT NULL,
    market_end_ts REAL NOT NULL,
    seconds_left REAL NOT NULL,
    enter INTEGER NOT NULL DEFAULT 0,
    side TEXT,
    confidence REAL,
    reason TEXT NOT NULL,
    current_btc REAL,
    window_open_btc REAL,
    delta_usd REAL,
    delta_pct REAL,
    up_ask REAL,
    down_ask REAL,
    up_bid REAL,
    down_bid REAL,
    spread REAL,
    top_ask_notional_usd REAL,
    features_json TEXT,
    settled_at REAL,
    final_btc REAL,
    winning_side TEXT,
    hypothetical_won INTEGER,
    hypothetical_pnl_per_1usd REAL
);

CREATE INDEX IF NOT EXISTS idx_signal_obs_ts ON signal_observations(ts);
CREATE INDEX IF NOT EXISTS idx_signal_obs_market_slug ON signal_observations(market_slug);
CREATE INDEX IF NOT EXISTS idx_signal_obs_reason ON signal_observations(reason);
CREATE INDEX IF NOT EXISTS idx_signal_obs_enter ON signal_observations(enter);
"""


@dataclass(slots=True)
class OpenTradeRecord:
    trade_id: str
    mode: str
    market_slug: str
    side: str
    is_hedge: bool
    parent_trade_id: str | None
    entry_ts: float
    entry_price: float
    shares: float
    cost_usd: float
    confidence: float | None
    features: dict[str, Any] | None
    extra: dict[str, Any] | None = None


@dataclass(slots=True)
class CloseTradeRecord:
    trade_id: str
    exit_ts: float
    exit_price: float
    proceeds_usd: float
    pnl_usd: float
    close_reason: str
    extra: dict[str, Any] | None = None


@dataclass(slots=True)
class SignalObservationRecord:
    ts: float
    market_slug: str
    market_end_ts: float
    seconds_left: float
    enter: bool
    side: str | None
    confidence: float | None
    reason: str
    current_btc: float | None
    window_open_btc: float | None
    delta_usd: float | None
    delta_pct: float | None
    up_ask: float | None
    down_ask: float | None
    up_bid: float | None
    down_bid: float | None
    spread: float | None
    top_ask_notional_usd: float | None
    features: dict[str, Any] | None = None


class TradeJournal:
    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as cx:
            cx.executescript(SCHEMA)

    @contextmanager
    def _conn(self):
        cx = sqlite3.connect(str(self.db_path))
        cx.row_factory = sqlite3.Row
        try:
            yield cx
            cx.commit()
        finally:
            cx.close()

    def start_session(self, mode: str, config: dict[str, Any]) -> int:
        with self._conn() as cx:
            cur = cx.execute(
                "INSERT INTO sessions (started_at, mode, config_json) VALUES (strftime('%s','now'), ?, ?)",
                (mode, json.dumps(config, default=str)),
            )
            return int(cur.lastrowid or 0)

    def end_session(self, session_id: int, final_equity_usd: float, notes: str = "") -> None:
        with self._conn() as cx:
            cx.execute(
                "UPDATE sessions SET ended_at = strftime('%s','now'), final_equity_usd = ?, notes = ? WHERE id = ?",
                (final_equity_usd, notes, session_id),
            )

    def record_open(self, rec: OpenTradeRecord) -> None:
        with self._conn() as cx:
            cx.execute(
                """
                INSERT INTO trades (
                    trade_id, mode, market_slug, side, is_hedge, parent_trade_id,
                    entry_ts, entry_price, shares, cost_usd, confidence,
                    features_json, extra_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rec.trade_id,
                    rec.mode,
                    rec.market_slug,
                    rec.side,
                    1 if rec.is_hedge else 0,
                    rec.parent_trade_id,
                    rec.entry_ts,
                    rec.entry_price,
                    rec.shares,
                    rec.cost_usd,
                    rec.confidence,
                    json.dumps(rec.features or {}, default=str),
                    json.dumps(rec.extra or {}, default=str),
                ),
            )

    def record_close(self, rec: CloseTradeRecord) -> None:
        with self._conn() as cx:
            cx.execute(
                """
                UPDATE trades
                SET exit_ts = ?, exit_price = ?, proceeds_usd = ?,
                    pnl_usd = ?, close_reason = ?,
                    extra_json = ?
                WHERE trade_id = ?
                """,
                (
                    rec.exit_ts,
                    rec.exit_price,
                    rec.proceeds_usd,
                    rec.pnl_usd,
                    rec.close_reason,
                    json.dumps(rec.extra or {}, default=str),
                    rec.trade_id,
                ),
            )

    def record_signal_observation(self, rec: SignalObservationRecord) -> None:
        with self._conn() as cx:
            cx.execute(
                """
                INSERT INTO signal_observations (
                    ts, market_slug, market_end_ts, seconds_left, enter, side,
                    confidence, reason, current_btc, window_open_btc, delta_usd,
                    delta_pct, up_ask, down_ask, up_bid, down_bid, spread,
                    top_ask_notional_usd, features_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rec.ts,
                    rec.market_slug,
                    rec.market_end_ts,
                    rec.seconds_left,
                    1 if rec.enter else 0,
                    rec.side,
                    rec.confidence,
                    rec.reason,
                    rec.current_btc,
                    rec.window_open_btc,
                    rec.delta_usd,
                    rec.delta_pct,
                    rec.up_ask,
                    rec.down_ask,
                    rec.up_bid,
                    rec.down_bid,
                    rec.spread,
                    rec.top_ask_notional_usd,
                    json.dumps(rec.features or {}, default=str),
                ),
            )

    def settle_signal_observations(
        self,
        *,
        market_slug: str,
        final_btc: float,
        window_open_btc: float,
        settled_ts: float,
    ) -> int:
        winning_side = "UP" if final_btc >= window_open_btc else "DOWN"
        with self._conn() as cx:
            cur = cx.execute(
                """
                UPDATE signal_observations
                SET settled_at = ?,
                    final_btc = ?,
                    winning_side = ?,
                    hypothetical_won = CASE
                        WHEN side IS NULL THEN NULL
                        WHEN side = ? THEN 1
                        ELSE 0
                    END,
                    hypothetical_pnl_per_1usd = CASE
                        WHEN side IS NULL THEN NULL
                        WHEN side = 'UP' AND (up_ask IS NULL OR up_ask <= 0) THEN NULL
                        WHEN side = 'DOWN' AND (down_ask IS NULL OR down_ask <= 0) THEN NULL
                        WHEN side = ? THEN ROUND(
                            (1.0 / CASE WHEN side = 'UP' THEN up_ask ELSE down_ask END) - 1.0,
                            6
                        )
                        ELSE -1.0
                    END
                WHERE market_slug = ? AND settled_at IS NULL
                """,
                (settled_ts, final_btc, winning_side, winning_side, winning_side, market_slug),
            )
            return int(cur.rowcount or 0)

    def signal_summary(self, *, since_ts: float | None = None) -> dict[str, Any]:
        where = "1=1"
        params: tuple[float, ...] = ()
        if since_ts is not None:
            where += " AND ts >= ?"
            params = (since_ts,)

        with self._conn() as cx:
            rows = cx.execute(
                f"""
                SELECT
                    COUNT(*) AS observations,
                    COUNT(DISTINCT market_slug) AS markets_seen,
                    COALESCE(SUM(CASE WHEN enter = 1 THEN 1 ELSE 0 END), 0) AS entry_signals,
                    COALESCE(SUM(CASE WHEN settled_at IS NOT NULL THEN 1 ELSE 0 END), 0) AS settled_observations,
                    COALESCE(SUM(CASE WHEN enter = 1 AND hypothetical_won = 1 THEN 1 ELSE 0 END), 0) AS entry_signal_wins,
                    COALESCE(SUM(CASE WHEN enter = 1 AND hypothetical_won = 0 THEN 1 ELSE 0 END), 0) AS entry_signal_losses,
                    ROUND(COALESCE(SUM(CASE WHEN enter = 1 THEN hypothetical_pnl_per_1usd ELSE 0 END), 0.0), 4) AS entry_signal_pnl_per_1usd,
                    COALESCE(SUM(CASE WHEN enter = 0 AND side IS NOT NULL AND seconds_left BETWEEN 10 AND 120 THEN 1 ELSE 0 END), 0) AS skipped_directional_observations,
                    COALESCE(SUM(CASE WHEN enter = 0 AND side IS NOT NULL AND hypothetical_pnl_per_1usd > 0 THEN 1 ELSE 0 END), 0) AS profitable_skipped_observations
                FROM signal_observations
                WHERE {where}
                """,
                params,
            ).fetchone()
        return dict(rows) if rows is not None else {}

    def signal_reason_counts(self, *, limit: int = 20, since_ts: float | None = None) -> list[dict[str, Any]]:
        where = "1=1"
        params: tuple[float, int] | tuple[int]
        if since_ts is not None:
            where += " AND ts >= ?"
            params = (since_ts, limit)
        else:
            params = (limit,)
        with self._conn() as cx:
            rows = cx.execute(
                f"""
                SELECT reason, COUNT(*) AS n
                FROM signal_observations
                WHERE {where}
                GROUP BY reason
                ORDER BY n DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [dict(r) for r in rows]

    def list_profitable_skipped_observations(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._conn() as cx:
            rows = cx.execute(
                """
                SELECT
                    ts, market_slug, seconds_left, side, confidence, reason,
                    delta_usd, delta_pct, up_ask, down_ask, spread,
                    top_ask_notional_usd, final_btc, window_open_btc,
                    winning_side, hypothetical_pnl_per_1usd
                FROM signal_observations
                WHERE enter = 0
                  AND side IS NOT NULL
                  AND hypothetical_pnl_per_1usd > 0
                ORDER BY hypothetical_pnl_per_1usd DESC, ts DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def unsettled_signal_markets(self) -> list[dict[str, Any]]:
        with self._conn() as cx:
            rows = cx.execute(
                """
                SELECT market_slug, MAX(market_end_ts) AS market_end_ts
                FROM signal_observations
                WHERE settled_at IS NULL
                GROUP BY market_slug
                ORDER BY market_end_ts ASC
                """
            ).fetchall()
        return [dict(r) for r in rows]

    def summary(self, *, since_ts: float | None = None) -> dict[str, Any]:
        where = "exit_ts IS NOT NULL"
        params: tuple[float, ...] = ()
        if since_ts is not None:
            where += " AND exit_ts >= ?"
            params = (since_ts,)

        with self._conn() as cx:
            rows = cx.execute(
                f"""
                SELECT
                    COUNT(*) AS n_trades,
                    COALESCE(SUM(CASE WHEN pnl_usd > 0 THEN 1 ELSE 0 END), 0) AS wins,
                    COALESCE(SUM(CASE WHEN pnl_usd <= 0 AND pnl_usd IS NOT NULL THEN 1 ELSE 0 END), 0) AS losses,
                    ROUND(COALESCE(SUM(pnl_usd), 0.0), 2) AS total_pnl_usd,
                    ROUND(COALESCE(AVG(pnl_usd), 0.0), 4) AS avg_pnl_usd
                FROM trades
                WHERE {where}
                """,
                params,
            ).fetchone()
        return dict(rows) if rows is not None else {}

    def list_recent(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._conn() as cx:
            rows = cx.execute(
                "SELECT * FROM trades ORDER BY entry_ts DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
