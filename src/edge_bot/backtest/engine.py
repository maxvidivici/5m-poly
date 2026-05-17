"""Simple backtest engine.

Replays historical BTC 5m windows from Binance candles (open/high/low/close)
through the same signal stack and risk manager used in live trading. CLOB
prices are modeled as a function of BTC delta (proxy) since Polymarket's
historical orderbook is not publicly available without their archive endpoint.

This is therefore an **approximation**: it tells you how the signal logic
would have behaved given a plausible market-skew model, not the exact past P&L.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from ..core.config import AppConfig
from ..execution.router import taker_fee_per_share
from ..risk.manager import RiskManager
from ..signals.composite import FeatureSet, _delta_thresholds, evaluate
from ..signals import indicators
from ..utils.clock import BUCKET_SECONDS_5M

BINANCE_REST = "https://api.binance.com"


@dataclass(slots=True)
class BacktestResult:
    n_trades: int
    wins: int
    losses: int
    total_pnl: float
    final_equity: float
    max_drawdown_pct: float
    win_rate_pct: float
    trades: list[dict]


def _fetch_kline(symbol: str, interval: str, start_ms: int, end_ms: int) -> list[list]:
    out: list[list] = []
    cursor = start_ms
    with httpx.Client(timeout=10.0) as c:
        while cursor < end_ms:
            r = c.get(
                f"{BINANCE_REST}/api/v3/klines",
                params={
                    "symbol": symbol,
                    "interval": interval,
                    "startTime": cursor,
                    "endTime": end_ms,
                    "limit": 1000,
                },
            )
            r.raise_for_status()
            rows = r.json()
            if not rows:
                break
            out.extend(rows)
            cursor = int(rows[-1][6]) + 1
            if len(rows) < 1000:
                break
    return out


def fetch_btc_1m_klines(*, start_ms: int, end_ms: int) -> list[list]:
    """Fetch BTCUSDT 1m candles once for backtests and parameter sweeps."""
    return _fetch_kline("BTCUSDT", "1m", start_ms, end_ms)


def _proxy_ask(
    delta_usd: float,
    soft_threshold_usd: float = 55.0,
    threshold_usd: float = 70.0,
    sweet_usd: float = 120.0,
) -> float:
    """Project a CLOB ask price from the BTC delta.

    This is a proxy for backtests only. It mirrors live observations where a
    near-$70 BTC move can already price around 0.70 on the winning side.
    """
    a = abs(delta_usd)
    if a < soft_threshold_usd:
        return 0.55
    if a < threshold_usd:
        span = max(1e-6, threshold_usd - soft_threshold_usd)
        return 0.62 + (a - soft_threshold_usd) * 0.10 / span
    if a <= sweet_usd:
        span = max(1e-6, sweet_usd - threshold_usd)
        return 0.72 + (a - threshold_usd) * 0.13 / span
    extra = a - sweet_usd
    return min(0.98, 0.85 + 0.1 * (1 - 1 / (1 + extra / 100.0)))


def run_backtest(cfg: AppConfig, *, start_ms: int, end_ms: int) -> BacktestResult:
    """Run a backtest over [start_ms, end_ms) using BTCUSDT 1m candles."""
    return run_backtest_from_klines(cfg, fetch_btc_1m_klines(start_ms=start_ms, end_ms=end_ms))


def run_backtest_from_klines(cfg: AppConfig, klines: list[list]) -> BacktestResult:
    """Run a backtest from already-loaded BTCUSDT 1m candles."""
    risk = RiskManager(cfg.risk)
    if not klines:
        return BacktestResult(0, 0, 0, 0.0, risk.state.equity, 0.0, 0.0, [])

    # Index 1m closes by open_time
    closes_by_t: dict[int, float] = {}
    highs_by_t: dict[int, float] = {}
    lows_by_t: dict[int, float] = {}
    for row in klines:
        t = int(row[0]) // 1000
        closes_by_t[t] = float(row[4])
        highs_by_t[t] = float(row[2])
        lows_by_t[t] = float(row[3])

    sorted_ts = sorted(closes_by_t.keys())
    if not sorted_ts:
        return BacktestResult(0, 0, 0, 0.0, risk.state.equity, 0.0, 0.0, [])

    historical_deltas_pct: list[float] = []
    trades: list[dict] = []
    peak_equity = risk.state.equity
    max_drawdown_pct = 0.0
    equity_curve: list[float] = []

    # Walk through every 5m bucket present in the data
    bucket_start = sorted_ts[0] - (sorted_ts[0] % BUCKET_SECONDS_5M)
    last_ts = sorted_ts[-1]
    while bucket_start + BUCKET_SECONDS_5M <= last_ts:
        open_ts = bucket_start
        close_ts = bucket_start + BUCKET_SECONDS_5M
        if open_ts not in closes_by_t or (close_ts - 60) not in closes_by_t:
            bucket_start += BUCKET_SECONDS_5M
            continue
        window_open = closes_by_t[open_ts]
        final_close = closes_by_t[close_ts - 60]

        # 5m features: synthesize from 1m bars in last few buckets
        h5: list[float] = []
        lo5: list[float] = []
        cl5: list[float] = []
        for b in range(
            bucket_start - 5 * BUCKET_SECONDS_5M, bucket_start + BUCKET_SECONDS_5M, BUCKET_SECONDS_5M
        ):
            bucket_1m_ts = [t for t in range(b, b + BUCKET_SECONDS_5M, 60) if t in closes_by_t]
            if not bucket_1m_ts:
                continue
            h5.append(max(highs_by_t[t] for t in bucket_1m_ts))
            lo5.append(min(lows_by_t[t] for t in bucket_1m_ts))
            cl5.append(closes_by_t[bucket_1m_ts[-1]])

        entries_this_market: list[dict] = []
        check_points = (close_ts - 120, close_ts - 60)
        for check_ts in check_points:
            if check_ts not in closes_by_t:
                continue
            current_btc = closes_by_t[check_ts]
            delta_usd = current_btc - window_open
            delta_pct = (delta_usd / window_open) * 100.0 if window_open else 0.0

            closes_1m = [closes_by_t[t] for t in range(open_ts, check_ts + 1, 60) if t in closes_by_t]

            proxy_atr = indicators.atr(tuple(h5), tuple(lo5), tuple(cl5), period=5)
            proxy_args = _delta_thresholds(proxy_atr, cfg.signal)
            if delta_usd > 0:
                clob_ask_up = _proxy_ask(delta_usd, *proxy_args)
            else:
                clob_ask_up = 1.0 - _proxy_ask(-delta_usd, *proxy_args)
            clob_ask_down = 1.0 - clob_ask_up
            clob_bid_up = max(0.01, clob_ask_up - 0.01)
            clob_bid_down = max(0.01, clob_ask_down - 0.01)

            features = FeatureSet(
                current_btc=current_btc,
                window_open_btc=window_open,
                closes_1m=tuple(closes_1m),
                highs_5m=tuple(h5),
                lows_5m=tuple(lo5),
                closes_5m=tuple(cl5),
                historical_deltas_pct=tuple(historical_deltas_pct[-24:]),
                seconds_left=float(close_ts - check_ts),
                clob_up_ask=clob_ask_up,
                clob_down_ask=clob_ask_down,
                clob_up_bid=clob_bid_up,
                clob_down_bid=clob_bid_down,
                top_ask_notional_usd=200.0,  # assume liquidity
            )
            decision = evaluate(features, cfg.signal)
            if not decision.enter or decision.side is None:
                if abs(delta_pct) > 0:
                    historical_deltas_pct.append(delta_pct)
                continue

            if entries_this_market:
                if not cfg.risk.allow_multiple_entries_per_market:
                    continue
                if len(entries_this_market) >= cfg.risk.max_entries_per_market:
                    continue
                if any(e["side"] != decision.side for e in entries_this_market):
                    continue
                if check_ts - entries_this_market[-1]["check_ts"] < cfg.risk.min_reentry_delay_sec:
                    continue
                exposure = sum(float(e["size"]) for e in entries_this_market)
                max_exposure = risk.state.equity * (cfg.risk.max_market_exposure_pct / 100.0)
                if exposure >= max_exposure:
                    continue

            gate = risk.can_trade(now=float(check_ts))
            if gate.allowed:
                ask = clob_ask_up if decision.side == "UP" else clob_ask_down
                size = risk.size_position(confidence=decision.confidence, side_ask=ask)
                if size > 0:
                    entries_this_market.append(
                        {
                            "check_ts": check_ts,
                            "side": decision.side,
                            "ask": ask,
                            "delta_usd": delta_usd,
                            "confidence": decision.confidence,
                            "size": size,
                        }
                    )
                    risk.record_trade_open(now=float(check_ts))

            if abs(delta_pct) > 0:
                historical_deltas_pct.append(delta_pct)

        for entry in entries_this_market:
            ask = float(entry["ask"])
            size = float(entry["size"])
            fee_rate = cfg.fees.paper_taker_fee_rate if cfg.fees.paper_taker_fees_enabled else 0.0
            shares = size / (ask + taker_fee_per_share(ask, fee_rate))
            side = str(entry["side"])
            won = (side == "UP" and final_close >= window_open) or (side == "DOWN" and final_close < window_open)
            pnl = (shares * 1.0 - size) if won else -size
            risk.record_trade_close(pnl, now=float(close_ts))
            trades.append(
                {
                    "open_ts": open_ts,
                    "check_ts": entry["check_ts"],
                    "side": side,
                    "delta_usd": entry["delta_usd"],
                    "confidence": entry["confidence"],
                    "ask": round(ask, 4),
                    "size": size,
                    "won": won,
                    "pnl": round(pnl, 4),
                    "equity_after": round(risk.state.equity, 2),
                }
            )

        equity_curve.append(risk.state.equity)
        peak_equity = max(peak_equity, risk.state.equity)
        drawdown = (peak_equity - risk.state.equity) / peak_equity * 100.0 if peak_equity > 0 else 0.0
        max_drawdown_pct = max(max_drawdown_pct, drawdown)

        bucket_start += BUCKET_SECONDS_5M

    wins = sum(1 for t in trades if t["won"])
    losses = len(trades) - wins
    total_pnl = sum(t["pnl"] for t in trades)
    win_rate = (wins / len(trades) * 100.0) if trades else 0.0
    return BacktestResult(
        n_trades=len(trades),
        wins=wins,
        losses=losses,
        total_pnl=round(total_pnl, 2),
        final_equity=round(risk.state.equity, 2),
        max_drawdown_pct=round(max_drawdown_pct, 2),
        win_rate_pct=round(win_rate, 2),
        trades=trades,
    )
