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

CREATE TABLE IF NOT EXISTS orderbook_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id TEXT NOT NULL UNIQUE,
    ts REAL NOT NULL,
    market_slug TEXT NOT NULL,
    side TEXT NOT NULL,
    token_id TEXT NOT NULL,
    best_bid REAL,
    best_ask REAL,
    best_bid_size REAL,
    best_ask_size REAL,
    top_ask_notional_usd REAL,
    spread REAL,
    bids_json TEXT NOT NULL,
    asks_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_orderbook_snapshots_trade_id ON orderbook_snapshots(trade_id);
CREATE INDEX IF NOT EXISTS idx_orderbook_snapshots_market_slug ON orderbook_snapshots(market_slug);

CREATE TABLE IF NOT EXISTS trade_fill_simulations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id TEXT NOT NULL,
    target_notional_usd REAL NOT NULL,
    fillable INTEGER NOT NULL DEFAULT 0,
    fill_ratio REAL NOT NULL DEFAULT 0.0,
    actual_notional_usd REAL NOT NULL DEFAULT 0.0,
    gross_notional_usd REAL NOT NULL DEFAULT 0.0,
    best_ask REAL,
    weighted_avg_fill_price REAL,
    max_level_price_used REAL,
    slippage_from_best_ask REAL,
    estimated_fee_usd REAL NOT NULL DEFAULT 0.0,
    estimated_shares REAL NOT NULL DEFAULT 0.0,
    pnl_if_won REAL NOT NULL DEFAULT 0.0,
    pnl_if_lost REAL NOT NULL DEFAULT 0.0,
    levels_used_json TEXT NOT NULL,
    created_at REAL NOT NULL DEFAULT (strftime('%s','now')),
    UNIQUE(trade_id, target_notional_usd)
);

CREATE INDEX IF NOT EXISTS idx_trade_fill_sims_trade_id ON trade_fill_simulations(trade_id);
CREATE INDEX IF NOT EXISTS idx_trade_fill_sims_target ON trade_fill_simulations(target_notional_usd);
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


@dataclass(slots=True)
class OrderbookSnapshotRecord:
    trade_id: str
    ts: float
    market_slug: str
    side: str
    token_id: str
    best_bid: float | None
    best_ask: float | None
    best_bid_size: float
    best_ask_size: float
    top_ask_notional_usd: float
    spread: float | None
    bids: list[dict[str, float]]
    asks: list[dict[str, float]]


@dataclass(slots=True)
class FillSimulationRecord:
    trade_id: str
    target_notional_usd: float
    fillable: bool
    fill_ratio: float
    actual_notional_usd: float
    gross_notional_usd: float
    best_ask: float | None
    weighted_avg_fill_price: float | None
    max_level_price_used: float | None
    slippage_from_best_ask: float | None
    estimated_fee_usd: float
    estimated_shares: float
    pnl_if_won: float
    pnl_if_lost: float
    levels_used: list[dict[str, float]]


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

    def record_orderbook_snapshot(self, rec: OrderbookSnapshotRecord) -> None:
        with self._conn() as cx:
            cx.execute(
                """
                INSERT OR REPLACE INTO orderbook_snapshots (
                    trade_id, ts, market_slug, side, token_id,
                    best_bid, best_ask, best_bid_size, best_ask_size,
                    top_ask_notional_usd, spread, bids_json, asks_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rec.trade_id,
                    rec.ts,
                    rec.market_slug,
                    rec.side,
                    rec.token_id,
                    rec.best_bid,
                    rec.best_ask,
                    rec.best_bid_size,
                    rec.best_ask_size,
                    rec.top_ask_notional_usd,
                    rec.spread,
                    json.dumps(rec.bids, default=str),
                    json.dumps(rec.asks, default=str),
                ),
            )

    def record_fill_simulation(self, rec: FillSimulationRecord) -> None:
        with self._conn() as cx:
            cx.execute(
                """
                INSERT OR REPLACE INTO trade_fill_simulations (
                    trade_id, target_notional_usd, fillable, fill_ratio,
                    actual_notional_usd, gross_notional_usd, best_ask,
                    weighted_avg_fill_price, max_level_price_used,
                    slippage_from_best_ask, estimated_fee_usd, estimated_shares,
                    pnl_if_won, pnl_if_lost, levels_used_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rec.trade_id,
                    rec.target_notional_usd,
                    1 if rec.fillable else 0,
                    rec.fill_ratio,
                    rec.actual_notional_usd,
                    rec.gross_notional_usd,
                    rec.best_ask,
                    rec.weighted_avg_fill_price,
                    rec.max_level_price_used,
                    rec.slippage_from_best_ask,
                    rec.estimated_fee_usd,
                    rec.estimated_shares,
                    rec.pnl_if_won,
                    rec.pnl_if_lost,
                    json.dumps(rec.levels_used, default=str),
                ),
            )

    def settle_signal_observations(
        self,
        *,
        market_slug: str,
        winning_side: str,
        settled_ts: float,
        final_btc: float | None = None,
        window_open_btc: float | None = None,
        only_unsettled: bool = True,
    ) -> int:
        where = "market_slug = ?"
        params: list[Any] = [
            settled_ts,
            final_btc,
            window_open_btc,
            winning_side,
            winning_side,
            winning_side,
            market_slug,
        ]
        if only_unsettled:
            where += " AND settled_at IS NULL"
        with self._conn() as cx:
            cur = cx.execute(
                f"""
                UPDATE signal_observations
                SET settled_at = ?,
                    final_btc = ?,
                    window_open_btc = COALESCE(?, window_open_btc),
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
                WHERE {where}
                """,
                tuple(params),
            )
            return int(cur.rowcount or 0)

    def reconcile_trade_official(
        self,
        *,
        trade_id: str,
        winning_side: str,
        reconciled_ts: float,
        resolution_source: str = "polymarket_gamma",
        raw_status: str = "",
    ) -> dict[str, Any] | None:
        with self._conn() as cx:
            row = cx.execute("SELECT * FROM trades WHERE trade_id = ?", (trade_id,)).fetchone()
            if row is None:
                return None
            rec = dict(row)
            won = rec["side"] == winning_side
            proceeds = round(float(rec["shares"]) if won else 0.0, 4)
            pnl = proceeds - float(rec["cost_usd"])
            exit_price = 1.0 if won else 0.0
            close_reason = "official_settled_won" if won else "official_settled_lost"
            try:
                extra = json.loads(rec.get("extra_json") or "{}")
                if not isinstance(extra, dict):
                    extra = {"previous_extra": extra}
            except Exception:
                extra = {"previous_extra_json": rec.get("extra_json")}
            extra["official_resolution"] = {
                "winning_side": winning_side,
                "resolution_source": resolution_source,
                "raw_status": raw_status,
                "reconciled_at": reconciled_ts,
                "previous_close_reason": rec.get("close_reason"),
                "previous_pnl_usd": rec.get("pnl_usd"),
            }
            exit_ts = rec.get("exit_ts") or reconciled_ts
            cx.execute(
                """
                UPDATE trades
                SET exit_ts = ?, exit_price = ?, proceeds_usd = ?,
                    pnl_usd = ?, close_reason = ?, extra_json = ?
                WHERE trade_id = ?
                """,
                (
                    exit_ts,
                    exit_price,
                    proceeds,
                    pnl,
                    close_reason,
                    json.dumps(extra, default=str),
                    trade_id,
                ),
            )
            return {**rec, "new_pnl_usd": pnl, "new_close_reason": close_reason, "winning_side": winning_side}

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

    def signal_markets(self) -> list[dict[str, Any]]:
        with self._conn() as cx:
            rows = cx.execute(
                """
                SELECT market_slug, MAX(market_end_ts) AS market_end_ts
                FROM signal_observations
                GROUP BY market_slug
                ORDER BY market_end_ts ASC
                """
            ).fetchall()
        return [dict(r) for r in rows]

    def list_trades_for_reconcile(self, *, mode: str = "paper") -> list[dict[str, Any]]:
        with self._conn() as cx:
            rows = cx.execute(
                "SELECT * FROM trades WHERE mode = ? ORDER BY entry_ts ASC",
                (mode,),
            ).fetchall()
        return [dict(r) for r in rows]

    def fill_simulation_summary(
        self,
        *,
        hedge: bool = False,
        min_fill_ratio: float | None = None,
    ) -> list[dict[str, Any]]:
        min_ratio = 1.0 if min_fill_ratio is None else max(0.0, float(min_fill_ratio))
        with self._conn() as cx:
            rows = cx.execute(
                """
                SELECT
                    f.target_notional_usd,
                    COUNT(*) AS trades,
                    COALESCE(SUM(f.fillable), 0) AS fully_fillable,
                    ROUND(100.0 * COALESCE(SUM(f.fillable), 0) / COUNT(*), 2) AS fully_fillable_pct,
                    COALESCE(SUM(CASE WHEN f.fill_ratio >= ? THEN 1 ELSE 0 END), 0) AS min_ratio_fillable,
                    ROUND(100.0 * COALESCE(SUM(CASE WHEN f.fill_ratio >= ? THEN 1 ELSE 0 END), 0) / COUNT(*), 2) AS min_ratio_fillable_pct,
                    ROUND(AVG(f.fill_ratio), 4) AS avg_fill_ratio,
                    ROUND(AVG(f.actual_notional_usd), 4) AS avg_actual_notional_usd,
                    ROUND(AVG(f.gross_notional_usd), 4) AS avg_gross_notional_usd,
                    ROUND(AVG(f.weighted_avg_fill_price), 6) AS avg_fill_price,
                    ROUND(AVG(f.slippage_from_best_ask), 6) AS avg_slippage_from_best_ask,
                    COALESCE(SUM(CASE WHEN t.close_reason = 'official_settled_won' THEN 1 ELSE 0 END), 0) AS official_wins,
                    COALESCE(SUM(CASE WHEN t.close_reason = 'official_settled_lost' THEN 1 ELSE 0 END), 0) AS official_losses,
                    COALESCE(SUM(CASE WHEN t.close_reason LIKE 'official_settled_%' THEN 1 ELSE 0 END), 0) AS official_settled,
                    ROUND(COALESCE(SUM(CASE
                        WHEN t.close_reason = 'official_settled_won' THEN f.pnl_if_won
                        WHEN t.close_reason = 'official_settled_lost' THEN f.pnl_if_lost
                        ELSE 0.0
                    END), 0.0), 4) AS official_simulated_pnl_usd
                FROM trade_fill_simulations f
                JOIN trades t ON t.trade_id = f.trade_id
                WHERE t.is_hedge = ?
                GROUP BY f.target_notional_usd
                ORDER BY f.target_notional_usd ASC
                """,
                (min_ratio, min_ratio, 1 if hedge else 0),
            ).fetchall()
        return [dict(r) for r in rows]

    def list_recent_fill_simulations(self, limit: int = 20, *, hedge: bool = False) -> list[dict[str, Any]]:
        with self._conn() as cx:
            rows = cx.execute(
                """
                SELECT
                    t.entry_ts, t.market_slug, t.side, t.entry_price, t.cost_usd,
                    t.close_reason, t.pnl_usd,
                    f.target_notional_usd, f.fillable, f.fill_ratio,
                    f.actual_notional_usd, f.gross_notional_usd, f.best_ask,
                    f.weighted_avg_fill_price, f.max_level_price_used,
                    f.slippage_from_best_ask, f.estimated_fee_usd,
                    f.estimated_shares, f.pnl_if_won, f.pnl_if_lost
                FROM trade_fill_simulations f
                JOIN trades t ON t.trade_id = f.trade_id
                WHERE t.is_hedge = ?
                ORDER BY t.entry_ts DESC, f.target_notional_usd ASC
                LIMIT ?
                """,
                (1 if hedge else 0, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def list_trade_analysis(self, *, hedge: bool = False, limit: int = 0) -> list[dict[str, Any]]:
        sql = "SELECT * FROM trades WHERE is_hedge = ? ORDER BY entry_ts DESC"
        params: tuple[Any, ...] = (1 if hedge else 0,)
        if limit > 0:
            sql += " LIMIT ?"
            params = (1 if hedge else 0, limit)
        with self._conn() as cx:
            rows = cx.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def summary(self, *, since_ts: float | None = None, hedge: bool | None = None) -> dict[str, Any]:
        where = "exit_ts IS NOT NULL"
        params: list[float | int] = []
        if since_ts is not None:
            where += " AND exit_ts >= ?"
            params.append(since_ts)
        if hedge is not None:
            where += " AND is_hedge = ?"
            params.append(1 if hedge else 0)

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
                tuple(params),
            ).fetchone()
        return dict(rows) if rows is not None else {}

    def list_recent(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._conn() as cx:
            rows = cx.execute(
                "SELECT * FROM trades ORDER BY entry_ts DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
