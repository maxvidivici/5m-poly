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
