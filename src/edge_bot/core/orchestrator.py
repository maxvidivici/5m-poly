"""Main strategy orchestrator: pulls data, evaluates signals, runs lifecycle."""

from __future__ import annotations

import logging
import signal
import time
import uuid
from collections import deque
from dataclasses import dataclass

from ..analysis.regime import regime_tags_from_features
from ..dashboard.text import render_dashboard
from ..data.polymarket import ClobClient, ClobWsObserver, ClobWsTop, GammaClient, MarketSnapshot, OrderbookLevel, OrderbookSnapshot
from ..data.price_feed import PriceFeed
from ..execution.fill_sim import simulate_buy_fill
from ..execution.router import LiveExecutor, PaperExecutor, make_executor
from ..hedge.engine import evaluate_hedge
from ..notifications.telegram import TelegramReporter, build_status_report
from ..risk.manager import RiskManager
from ..signals.composite import FeatureSet, evaluate
from ..storage.journal import (
    CloseTradeRecord,
    FillSimulationRecord,
    OpenTradeRecord,
    OrderbookSnapshotRecord,
    SignalObservationRecord,
    TradeJournal,
)
from ..utils.clock import bucket_5m_start, iso_z, now_ts
from .config import AppConfig

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class OpenPosition:
    trade_id: str
    side: str
    token_id: str
    entry_price: float
    shares: float
    cost_usd: float
    market_end_ts: float
    market_slug: str
    is_hedge: bool = False
    parent_trade_id: str | None = None
    opened_ts: float = 0.0


class Orchestrator:
    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        self.gamma = GammaClient(base_url=cfg.data.gamma_base_url)
        self.clob = ClobClient(base_url=cfg.data.clob_base_url)
        self.clob_ws = ClobWsObserver(ws_url=cfg.data.clob_ws_url, enabled=cfg.data.clob_ws_enabled)
        self.price_feed = PriceFeed(
            primary=cfg.data.primary_price_source,
            fallback=cfg.data.fallback_sources,
            staleness_max_sec=cfg.data.price_staleness_max_sec,
        )
        self.executor: PaperExecutor | LiveExecutor = make_executor(cfg, self.clob)
        self.risk = RiskManager(cfg.risk)
        self.journal = TradeJournal(cfg.storage.journal_db)
        self.session_started_ts = now_ts()
        self.session_id = self.journal.start_session(cfg.mode, _serialize_cfg(cfg))
        self.open_positions: dict[str, OpenPosition] = {}
        self.historical_deltas_pct: deque[float] = deque(maxlen=24)
        self._observed_signal_markets: dict[str, tuple[float, int]] = {}
        self._settled_signal_markets: set[str] = set()
        self._load_unsettled_signal_markets()
        self._stop = False
        self._installed_signals = False
        self._last_signal_payload: dict | None = None
        self.telegram_reporter = TelegramReporter(cfg.telegram)
        self._last_telegram_report_ts = now_ts()

    def _install_signal_handlers(self) -> None:
        if self._installed_signals:
            return
        try:
            signal.signal(signal.SIGINT, self._handle_signal)
            signal.signal(signal.SIGTERM, self._handle_signal)
            self._installed_signals = True
        except (ValueError, OSError):  # not in main thread
            pass

    def _handle_signal(self, signum: int, _frame) -> None:
        logger.warning("received_signal_%d_initiating_graceful_shutdown", signum)
        self._stop = True

    def stop(self) -> None:
        self._stop = True

    # ---------------------- main loop ----------------------

    def run(self) -> None:
        self._install_signal_handlers()
        logger.info("orchestrator_starting mode=%s", self.cfg.mode)
        self._send_startup_telegram_report()
        try:
            while not self._stop:
                try:
                    self._tick()
                except Exception as e:
                    logger.error("tick_error", exc_info=e)
                self._maybe_send_telegram_report()
                time.sleep(self.cfg.ops.loop_poll_sec)
        finally:
            self._graceful_close_all()
            self.clob_ws.close()
            self._refresh_dashboard()
            self.journal.end_session(self.session_id, self.risk.state.equity, notes="graceful_shutdown")
            logger.info("orchestrator_stopped equity=%.2f", self.risk.state.equity)

    def run_ticks(self, max_ticks: int) -> None:
        """Run a bounded number of ticks, useful for smoke tests and paper probes."""
        logger.info("orchestrator_starting_bounded mode=%s max_ticks=%d", self.cfg.mode, max_ticks)
        self._send_startup_telegram_report()
        try:
            for _ in range(max_ticks):
                if self._stop:
                    break
                try:
                    self._tick()
                except Exception as e:
                    logger.error("tick_error", exc_info=e)
                self._maybe_send_telegram_report()
                time.sleep(self.cfg.ops.loop_poll_sec)
        finally:
            self._graceful_close_all()
            self.clob_ws.close()
            self._refresh_dashboard()
            self.journal.end_session(self.session_id, self.risk.state.equity, notes="bounded_run_complete")
            logger.info("orchestrator_bounded_stopped equity=%.2f", self.risk.state.equity)

    def _tick(self) -> None:
        self._settle_expired_signal_markets()
        market = self.gamma.resolve_current_btc_5m_market(
            required_resolution_source=self.cfg.data.required_resolution_source
        )
        self._refresh_dashboard(market=market)
        self._manage_positions(market)
        if market is None:
            return

        gate = self.risk.can_trade()
        if not gate.allowed:
            self._last_signal_payload = {"enter": False, "reason": gate.reason, "features": {}}
            self._record_signal_observation(market, self._last_signal_payload)
            self._refresh_dashboard(market=market)
            return

        decision_payload = self._evaluate_signal(market)
        self._last_signal_payload = decision_payload
        self._refresh_dashboard(market=market)
        if not decision_payload["enter"]:
            self._record_signal_observation(market, decision_payload)
            return

        reentry_gate = self._can_open_for_market(market, decision_payload)
        if not reentry_gate["allowed"]:
            self._last_signal_payload = {**decision_payload, "enter": False, "reason": reentry_gate["reason"]}
            self._record_signal_observation(market, self._last_signal_payload)
            self._refresh_dashboard(market=market)
            return

        if reentry_gate.get("entry_kind"):
            decision_payload = {
                **decision_payload,
                "entry_kind": reentry_gate.get("entry_kind"),
                "size_usd": reentry_gate.get("size_usd"),
                "parent_trade_id": reentry_gate.get("parent_trade_id"),
            }
        self._record_signal_observation(market, decision_payload)
        self._open_position(market, decision_payload)

    def _send_startup_telegram_report(self) -> None:
        if self.cfg.telegram.send_on_start:
            self._send_telegram_report(since_ts=self.session_started_ts)

    def _maybe_send_telegram_report(self) -> None:
        if not self.telegram_reporter.configured:
            return
        now = now_ts()
        if now - self._last_telegram_report_ts < self.cfg.telegram.report_interval_sec:
            return
        since_ts = self._last_telegram_report_ts
        self._send_telegram_report(since_ts=since_ts)
        self._last_telegram_report_ts = now

    def _send_telegram_report(self, *, since_ts: float | None = None) -> None:
        if not self.telegram_reporter.configured:
            return
        risk_snapshot = self.risk.snapshot()
        text = build_status_report(
            mode=self.cfg.mode,
            equity=self.risk.state.equity,
            risk=risk_snapshot,
            total_summary=self.journal.summary(hedge=False),
            interval_summary=self.journal.summary(since_ts=since_ts, hedge=False)
            if since_ts is not None
            else self.journal.summary(hedge=False),
            total_hedge_summary=self.journal.summary(hedge=True),
            interval_hedge_summary=self.journal.summary(since_ts=since_ts, hedge=True)
            if since_ts is not None
            else self.journal.summary(hedge=True),
            total_net_summary=self.journal.summary(),
            interval_net_summary=self.journal.summary(since_ts=since_ts) if since_ts is not None else self.journal.summary(),
            open_positions=len(self.open_positions),
            last_signal=self._last_signal_payload,
            signal_summary=self.journal.signal_summary(since_ts=since_ts),
        )
        result = self.telegram_reporter.send_text(text)
        if result.ok:
            logger.info("telegram_report_sent")
        else:
            logger.warning("telegram_report_not_sent reason=%s", result.reason)
    def _evaluate_signal(self, market: MarketSnapshot) -> dict:
        candles_1m = self.price_feed.fetch_candles_1m(n=6)
        candles_5m = self.price_feed.fetch_candles_5m(n=12)
        current_btc = self.price_feed.current_price()
        window_start = _slug_bucket_start(market.slug) or bucket_5m_start()
        window_open = self.price_feed.window_open_price(window_start)
        self.clob_ws.subscribe((market.up_token_id, market.down_token_id))
        up_book = self.clob.orderbook(market.up_token_id)
        down_book = self.clob.orderbook(market.down_token_id)
        ws_up = self.clob_ws.snapshot(market.up_token_id)
        ws_down = self.clob_ws.snapshot(market.down_token_id)
        ws_features = _clob_ws_feature_dump(
            enabled=self.cfg.data.clob_ws_enabled,
            up_ws=ws_up,
            down_ws=ws_down,
            up_http=up_book,
            down_http=down_book,
        )

        # In a heavily one-sided book one side may have no ask (everyone is bidding).
        # We only need OUR-side ask to evaluate entry; the opposite ask is used for skew
        # and can be defaulted to (1 - opposite_bid) when missing.
        if current_btc is None or window_open is None:
            return {
                "enter": False,
                "reason": "missing_data",
                "features": {
                    "current_btc": current_btc or 0.0,
                    "window_open": window_open or 0.0,
                    "window_open_btc": window_open or 0.0,
                    "up_ask": up_book.best_ask or 0.0,
                    "down_ask": down_book.best_ask or 0.0,
                    "up_bid": up_book.best_bid or 0.0,
                    "down_bid": down_book.best_bid or 0.0,
                    **ws_features,
                },
            }
        if up_book.best_ask is None and down_book.best_ask is None:
            return {
                "enter": False,
                "reason": "no_asks_either_side",
                "features": {
                    "current_btc": current_btc,
                    "window_open": window_open,
                    "window_open_btc": window_open,
                    "up_bid": up_book.best_bid or 0.0,
                    "down_bid": down_book.best_bid or 0.0,
                    **ws_features,
                },
            }

        # Fill in the missing-side ask from the opposite bid (complementary prob).
        eff_up_ask = (
            up_book.best_ask
            if up_book.best_ask is not None
            else (round(1.0 - down_book.best_bid, 4) if down_book.best_bid is not None else 0.0)
        )
        eff_down_ask = (
            down_book.best_ask
            if down_book.best_ask is not None
            else (round(1.0 - up_book.best_bid, 4) if up_book.best_bid is not None else 0.0)
        )

        # Compute prior 5m deltas as historical sample for z-score
        if len(candles_5m) >= 2:
            for i in range(1, len(candles_5m)):
                prev_close = candles_5m[i - 1].close
                cur_close = candles_5m[i].close
                if prev_close > 0:
                    self.historical_deltas_pct.append((cur_close - prev_close) / prev_close * 100.0)

        direction_up = current_btc >= window_open
        side_label = "UP" if direction_up else "DOWN"
        ws_features.update(_clob_ws_side_features(side_label, ws_up, ws_down, up_book, down_book))
        side_top_ask_notional_usd = (
            up_book.top_ask_notional_usd if direction_up else down_book.top_ask_notional_usd
        )

        features = FeatureSet(
            current_btc=current_btc,
            window_open_btc=window_open,
            closes_1m=tuple(c.close for c in candles_1m),
            highs_5m=tuple(c.high for c in candles_5m),
            lows_5m=tuple(c.low for c in candles_5m),
            closes_5m=tuple(c.close for c in candles_5m),
            historical_deltas_pct=tuple(self.historical_deltas_pct),
            seconds_left=market.seconds_left,
            clob_up_ask=eff_up_ask,
            clob_down_ask=eff_down_ask,
            clob_up_bid=up_book.best_bid or 0.0,
            clob_down_bid=down_book.best_bid or 0.0,
            top_ask_notional_usd=side_top_ask_notional_usd,
        )

        decision = evaluate(features, self.cfg.signal)
        decision_features = {
            **decision.features,
            "current_btc": current_btc,
            "window_open_btc": window_open,
            "seconds_left": market.seconds_left,
            "up_ask": up_book.best_ask or 0.0,
            "down_ask": down_book.best_ask or 0.0,
            "up_bid": up_book.best_bid or 0.0,
            "down_bid": down_book.best_bid or 0.0,
            "top_ask_notional_usd": side_top_ask_notional_usd,
            **ws_features,
        }
        return {
            "enter": decision.enter,
            "side": decision.side,
            "confidence": decision.confidence,
            "reason": decision.reason,
            "features": decision_features,
            "up_ask": up_book.best_ask,
            "down_ask": down_book.best_ask,
            "up_bid": up_book.best_bid,
            "down_bid": down_book.best_bid,
            "top_ask_notional_usd": side_top_ask_notional_usd,
            "spread": min(
                up_book.spread if up_book.spread is not None else 0.99,
                down_book.spread if down_book.spread is not None else 0.99,
            ),
        }

    def _record_signal_observation(self, market: MarketSnapshot, decision: dict) -> None:
        window_start = _slug_bucket_start(market.slug) or bucket_5m_start()
        self._observed_signal_markets[market.slug] = (market.end_ts, window_start)
        features = decision.get("features") or {}
        try:
            self.journal.record_signal_observation(
                SignalObservationRecord(
                    ts=now_ts(),
                    market_slug=market.slug,
                    market_end_ts=market.end_ts,
                    seconds_left=max(0.0, market.end_ts - now_ts()),
                    enter=bool(decision.get("enter")),
                    side=decision.get("side"),
                    confidence=_optional_float(decision.get("confidence")),
                    reason=str(decision.get("reason") or "unknown"),
                    current_btc=_optional_float(features.get("current_btc")),
                    window_open_btc=_optional_float(features.get("window_open_btc", features.get("window_open"))),
                    delta_usd=_optional_float(features.get("delta_usd")),
                    delta_pct=_optional_float(features.get("delta_pct")),
                    up_ask=_optional_float(decision.get("up_ask", features.get("up_ask"))),
                    down_ask=_optional_float(decision.get("down_ask", features.get("down_ask"))),
                    up_bid=_optional_float(decision.get("up_bid", features.get("up_bid"))),
                    down_bid=_optional_float(decision.get("down_bid", features.get("down_bid"))),
                    spread=_optional_float(decision.get("spread", features.get("spread"))),
                    top_ask_notional_usd=_optional_float(
                        decision.get("top_ask_notional_usd", features.get("top_ask_notional_usd"))
                    ),
                    features=features,
                )
            )
        except Exception as e:
            logger.warning("signal_observation_record_failed", exc_info=e)

    def _settle_expired_signal_markets(self) -> None:
        if not self._observed_signal_markets:
            return
        now = now_ts()
        for slug, (end_ts, _window_start) in list(self._observed_signal_markets.items()):
            if slug in self._settled_signal_markets or now < end_ts:
                continue
            resolution = self.gamma.resolve_market_resolution(
                slug,
                required_resolution_source=self.cfg.data.required_resolution_source,
            )
            if resolution is None or not resolution.resolved or resolution.winning_side is None:
                continue
            updated = self.journal.settle_signal_observations(
                market_slug=slug,
                winning_side=resolution.winning_side,
                settled_ts=now,
            )
            self._settled_signal_markets.add(slug)
            self._observed_signal_markets.pop(slug, None)
            logger.info(
                "signal_observations_official_settled slug=%s side=%s updated=%d",
                slug,
                resolution.winning_side,
                updated,
            )

    def _load_unsettled_signal_markets(self) -> None:
        try:
            rows = self.journal.unsettled_signal_markets()
        except Exception as e:
            logger.warning("load_unsettled_signal_markets_failed", exc_info=e)
            return
        for row in rows:
            slug = str(row.get("market_slug") or "")
            window_start = _slug_bucket_start(slug)
            if window_start is None:
                continue
            self._observed_signal_markets[slug] = (float(row.get("market_end_ts") or 0.0), window_start)

    def _open_position(self, market: MarketSnapshot, decision: dict) -> None:
        side = decision["side"]
        confidence = float(decision["confidence"])
        token_id = market.up_token_id if side == "UP" else market.down_token_id
        book = self.clob.orderbook(token_id)
        side_ask = book.best_ask
        if side_ask is None:
            logger.warning("open_position_failed_no_live_ask side=%s slug=%s", side, market.slug)
            return
        side_ask = float(side_ask)

        size_usd = self.risk.size_position(confidence=confidence, side_ask=side_ask)
        size_override = _optional_float(decision.get("size_usd"))
        if size_override is not None and size_override > 0:
            size_usd = min(size_usd, size_override) if size_usd > 0 else 0.0
        if size_usd <= 0:
            return
        size_usd = round(size_usd, 2)

        result = self._submit_buy(token_id=token_id, notional_usd=size_usd, snapshot=book)
        if not result.success:
            logger.warning("open_position_failed status=%s raw=%s", result.status, result.raw)
            return

        trade_id = f"trade-{uuid.uuid4().hex[:12]}"
        opened_ts = now_ts()
        entry_kind = str(decision.get("entry_kind") or "main")
        parent_trade_id = decision.get("parent_trade_id") if entry_kind == "addon" else None
        features = dict(decision.get("features") or {})
        features["entry_kind"] = entry_kind
        if parent_trade_id:
            features["addon_parent_trade_id"] = str(parent_trade_id)
        features["regime_tags"] = regime_tags_from_features(features, self.cfg.signal)
        pos = OpenPosition(
            trade_id=trade_id,
            side=side,
            token_id=token_id,
            entry_price=result.filled_price,
            shares=result.filled_shares,
            cost_usd=result.cost_usd,
            market_end_ts=market.end_ts,
            market_slug=market.slug,
            opened_ts=opened_ts,
        )
        pos.parent_trade_id = str(parent_trade_id) if parent_trade_id else None
        self.open_positions[trade_id] = pos
        self.risk.record_trade_open()
        self.journal.record_open(
            OpenTradeRecord(
                trade_id=trade_id,
                mode=self.cfg.mode,
                market_slug=market.slug,
                side=side,
                is_hedge=False,
                parent_trade_id=pos.parent_trade_id,
                entry_ts=opened_ts,
                entry_price=result.filled_price,
                shares=result.filled_shares,
                cost_usd=result.cost_usd,
                confidence=confidence,
                features=features,
                extra={"order_id": result.order_id, "ts": iso_z(), "entry_kind": entry_kind},
            )
        )
        self._record_entry_liquidity_analysis(
            trade_id=trade_id,
            ts=opened_ts,
            market=market,
            side=side,
            token_id=token_id,
            book=book,
        )
        logger.info(
            "opened kind=%s side=%s slug=%s shares=%.4f cost=%.2f confidence=%.3f",
            entry_kind,
            side,
            market.slug,
            result.filled_shares,
            result.cost_usd,
            confidence,
        )

    def _submit_buy(
        self,
        *,
        token_id: str,
        notional_usd: float,
        snapshot: OrderbookSnapshot | None = None,
    ):
        if isinstance(self.executor, LiveExecutor):
            book = snapshot or self.clob.orderbook(token_id)
            return self.executor.buy(token_id=token_id, notional_usd=notional_usd, snapshot=book)
        return self.executor.buy(token_id=token_id, notional_usd=notional_usd, snapshot=snapshot)

    def _record_entry_liquidity_analysis(
        self,
        *,
        trade_id: str,
        ts: float,
        market: MarketSnapshot,
        side: str,
        token_id: str,
        book: OrderbookSnapshot,
    ) -> None:
        try:
            level_limit = max(1, int(self.cfg.live_readiness.orderbook_snapshot_levels))
            asks = tuple(book.asks[:level_limit])
            bids = tuple(book.bids[:level_limit])
            self.journal.record_orderbook_snapshot(
                OrderbookSnapshotRecord(
                    trade_id=trade_id,
                    ts=ts,
                    market_slug=market.slug,
                    side=side,
                    token_id=token_id,
                    best_bid=book.best_bid,
                    best_ask=book.best_ask,
                    best_bid_size=book.best_bid_size,
                    best_ask_size=book.best_ask_size,
                    top_ask_notional_usd=book.top_ask_notional_usd,
                    spread=book.spread,
                    bids=_levels_to_json(bids),
                    asks=_levels_to_json(asks),
                )
            )
            if book.best_ask is None:
                return

            max_level_price = min(
                self.cfg.signal.clob_ask_max,
                float(book.best_ask) + max(0.0, self.cfg.live_readiness.max_fill_slippage),
            )
            max_avg_fill_price = self.cfg.live_readiness.max_avg_fill_price
            fee_rate = self.cfg.fees.paper_taker_fee_rate if self.cfg.fees.paper_taker_fees_enabled else 0.0
            for target in self.cfg.live_readiness.fill_sim_targets_usd:
                sim = simulate_buy_fill(
                    asks,
                    target_notional_usd=target,
                    max_level_price=max_level_price,
                    fee_rate=fee_rate,
                    max_avg_fill_price=max_avg_fill_price,
                )
                self.journal.record_fill_simulation(
                    FillSimulationRecord(
                        trade_id=trade_id,
                        target_notional_usd=sim.target_notional_usd,
                        fillable=sim.fillable,
                        fill_ratio=sim.fill_ratio,
                        actual_notional_usd=sim.actual_notional_usd,
                        gross_notional_usd=sim.gross_notional_usd,
                        best_ask=sim.best_ask,
                        weighted_avg_fill_price=sim.weighted_avg_fill_price,
                        max_level_price_used=sim.max_level_price_used,
                        slippage_from_best_ask=sim.slippage_from_best_ask,
                        estimated_fee_usd=sim.estimated_fee_usd,
                        estimated_shares=sim.estimated_shares,
                        pnl_if_won=sim.pnl_if_won,
                        pnl_if_lost=sim.pnl_if_lost,
                        levels_used=[level.as_dict() for level in sim.levels_used],
                    )
                )
        except Exception as e:
            logger.warning("entry_liquidity_analysis_failed trade_id=%s", trade_id, exc_info=e)

    def _submit_close(self, *, token_id: str, shares: float):
        if isinstance(self.executor, LiveExecutor):
            book = self.clob.orderbook(token_id)
            return self.executor.close(token_id=token_id, shares=shares, snapshot=book)
        return self.executor.close(token_id=token_id, shares=shares)

    def _manage_positions(self, market: MarketSnapshot | None) -> None:
        if not self.open_positions:
            return
        now = now_ts()
        for trade_id, pos in list(self.open_positions.items()):
            seconds_left = max(0.0, pos.market_end_ts - now)

            # Time exit
            if seconds_left <= 0:
                if isinstance(self.executor, PaperExecutor) and self.cfg.exit_.paper_settle_on_expiry:
                    self._settle_paper_position(trade_id)
                else:
                    self._close_position(trade_id, reason="expired_force_close")
                continue

            if seconds_left <= self.cfg.exit_.exit_before_sec:
                if isinstance(self.executor, PaperExecutor) and self.cfg.exit_.paper_settle_on_expiry:
                    continue
                self._close_position(trade_id, reason="time_exit")
                continue

            # Hedge logic (only for main, not hedges)
            if market is None:
                continue

            if not pos.is_hedge and self.cfg.hedge.enabled and pos.market_slug == market.slug:
                opposite_token = market.down_token_id if pos.side == "UP" else market.up_token_id
                opposite_book = self.clob.orderbook(opposite_token)
                opposite_ask = opposite_book.best_ask or 0.0
                # skew_against_us = how much market backs our side already
                if pos.side == "UP":
                    main_ask = self.clob.orderbook(market.up_token_id).best_ask or 0.0
                else:
                    main_ask = self.clob.orderbook(market.down_token_id).best_ask or 0.0
                skew = main_ask  # main_ask near 1 means market is sure
                hedge = evaluate_hedge(
                    self.cfg.hedge,
                    main_side=pos.side,
                    main_notional_usd=pos.cost_usd,
                    seconds_left=seconds_left,
                    opposite_ask=opposite_ask,
                    skew_against_us=skew,
                )
                if (
                    hedge.place_hedge
                    and hedge.side is not None
                    and not _has_hedge_for(self.open_positions, trade_id)
                    and not (
                        self.cfg.hedge.once_per_market
                        and _has_hedge_for_market(self.open_positions, pos.market_slug)
                    )
                ):
                    res = self._submit_buy(token_id=opposite_token, notional_usd=hedge.notional_usd)
                    if res.success:
                        hedge_id = f"hedge-{uuid.uuid4().hex[:12]}"
                        self.open_positions[hedge_id] = OpenPosition(
                            trade_id=hedge_id,
                            side=hedge.side,
                            token_id=opposite_token,
                            entry_price=res.filled_price,
                            shares=res.filled_shares,
                            cost_usd=res.cost_usd,
                            market_end_ts=pos.market_end_ts,
                            market_slug=pos.market_slug,
                            is_hedge=True,
                            parent_trade_id=trade_id,
                            opened_ts=now_ts(),
                        )
                        self.risk.record_trade_open()
                        self.journal.record_open(
                            OpenTradeRecord(
                                trade_id=hedge_id,
                                mode=self.cfg.mode,
                                market_slug=pos.market_slug,
                                side=hedge.side,
                                is_hedge=True,
                                parent_trade_id=trade_id,
                                entry_ts=now_ts(),
                                entry_price=res.filled_price,
                                shares=res.filled_shares,
                                cost_usd=res.cost_usd,
                                confidence=None,
                                features={"skew": skew, "opposite_ask": opposite_ask},
                                extra={"reason": hedge.reason, "order_id": res.order_id},
                            )
                        )
                        logger.info(
                            "hedge_opened parent=%s hedge_side=%s notional=%.2f skew=%.3f",
                            trade_id,
                            hedge.side,
                            hedge.notional_usd,
                            skew,
                        )

    def _close_position(self, trade_id: str, *, reason: str) -> None:
        pos = self.open_positions.get(trade_id)
        if pos is None:
            return
        res = self._submit_close(token_id=pos.token_id, shares=pos.shares)
        proceeds = res.proceeds_usd if res.success else 0.0
        pnl = proceeds - pos.cost_usd
        self.journal.record_close(
            CloseTradeRecord(
                trade_id=trade_id,
                exit_ts=now_ts(),
                exit_price=res.exit_price,
                proceeds_usd=proceeds,
                pnl_usd=pnl,
                close_reason=reason if res.success else f"{reason}_close_failed",
                extra={"raw": res.raw},
            )
        )
        self.risk.record_trade_close(pnl)
        del self.open_positions[trade_id]
        logger.info("closed trade_id=%s pnl=%.2f reason=%s", trade_id, pnl, reason)

    def _can_open_for_market(self, market: MarketSnapshot, decision: dict) -> dict[str, object]:
        main_positions = [
            p for p in self.open_positions.values() if p.market_slug == market.slug and not p.is_hedge
        ]
        if not main_positions:
            return {"allowed": True, "reason": "ok"}

        cfg = self.cfg.risk

        if not self.cfg.risk.allow_multiple_entries_per_market:
            return {"allowed": False, "reason": "position_already_open_for_market"}

        if len(main_positions) >= self.cfg.risk.max_entries_per_market:
            return {"allowed": False, "reason": "max_entries_per_market_hit"}

        side = decision.get("side")
        if any(p.side != side for p in main_positions):
            return {"allowed": False, "reason": "opposite_side_position_open"}

        now = now_ts()
        last_opened_ts = max((p.opened_ts for p in main_positions), default=0.0)
        elapsed = now - last_opened_ts
        if elapsed < self.cfg.risk.min_reentry_delay_sec:
            return {"allowed": False, "reason": f"reentry_delay_{int(self.cfg.risk.min_reentry_delay_sec - elapsed)}s"}

        market_exposure = sum(p.cost_usd for p in main_positions)
        max_exposure = self.risk.state.equity * (self.cfg.risk.max_market_exposure_pct / 100.0)
        if market_exposure >= max_exposure:
            return {"allowed": False, "reason": "max_market_exposure_hit"}

        if not cfg.addon_enabled:
            return {"allowed": False, "reason": "addon_disabled"}

        features = decision.get("features") or {}
        if not isinstance(features, dict):
            return {"allowed": False, "reason": "addon_missing_features"}

        seconds_left = _feature_float(features, "seconds_left", market.seconds_left)
        side_ask = _feature_float(features, "side_ask", 0.0)
        delta_ratio = _feature_float(features, "delta_strong_ratio", 0.0)
        abs_zscore = abs(_feature_float(features, "zscore", 0.0))
        spread = _feature_float(features, "spread", 0.99)
        top_ask_usd = _feature_float(features, "top_ask_notional_usd", 0.0)

        if seconds_left < cfg.addon_min_seconds_left:
            return {
                "allowed": False,
                "reason": f"addon_seconds_left_{seconds_left:.1f}_below_{cfg.addon_min_seconds_left:.1f}",
            }
        if side_ask < cfg.addon_min_side_ask:
            return {
                "allowed": False,
                "reason": f"addon_side_ask_{side_ask:.3f}_below_{cfg.addon_min_side_ask:.2f}",
            }
        if delta_ratio < cfg.addon_min_delta_strong_ratio:
            return {
                "allowed": False,
                "reason": f"addon_delta_ratio_{delta_ratio:.2f}_below_{cfg.addon_min_delta_strong_ratio:.2f}",
            }
        if abs_zscore < cfg.addon_min_abs_zscore:
            return {
                "allowed": False,
                "reason": f"addon_abs_zscore_{abs_zscore:.2f}_below_{cfg.addon_min_abs_zscore:.2f}",
            }
        if spread > cfg.addon_max_spread:
            return {"allowed": False, "reason": f"addon_spread_{spread:.3f}_too_wide"}

        base_size = min(p.cost_usd for p in main_positions)
        addon_size = round(min(cfg.addon_size_usd, base_size), 2)
        if addon_size < cfg.min_position_usd:
            return {"allowed": False, "reason": "addon_size_below_min_position"}
        if top_ask_usd < addon_size:
            return {"allowed": False, "reason": f"addon_top_ask_${top_ask_usd:.1f}_too_thin"}
        if market_exposure + addon_size > max_exposure:
            return {"allowed": False, "reason": "addon_market_exposure_hit"}

        parent = min(main_positions, key=lambda p: p.opened_ts or 0.0)
        return {
            "allowed": True,
            "reason": "ok_addon",
            "entry_kind": "addon",
            "size_usd": addon_size,
            "parent_trade_id": parent.trade_id,
        }

    def _settle_paper_position(self, trade_id: str) -> None:
        pos = self.open_positions.get(trade_id)
        if pos is None:
            return
        resolution = self.gamma.resolve_market_resolution(
            pos.market_slug,
            required_resolution_source=self.cfg.data.required_resolution_source,
        )
        if resolution is None or not resolution.resolved or resolution.winning_side is None:
            logger.info("paper_settle_waiting_official_resolution trade_id=%s slug=%s", trade_id, pos.market_slug)
            return

        winning_side = resolution.winning_side
        won = pos.side == winning_side
        proceeds = round(pos.shares if won else 0.0, 4)
        pnl = proceeds - pos.cost_usd
        exit_price = 1.0 if won else 0.0
        self.journal.record_close(
            CloseTradeRecord(
                trade_id=trade_id,
                exit_ts=now_ts(),
                exit_price=exit_price,
                proceeds_usd=proceeds,
                pnl_usd=pnl,
                close_reason="official_settled_won" if won else "official_settled_lost",
                extra={
                    "winning_side": winning_side,
                    "resolution_source": resolution.resolution_source,
                    "raw_status": resolution.raw_status,
                    "official_up_price": resolution.up_price,
                    "official_down_price": resolution.down_price,
                },
            )
        )
        self.risk.record_trade_close(pnl)
        del self.open_positions[trade_id]
        logger.info("paper_official_settled trade_id=%s pnl=%.2f winning_side=%s", trade_id, pnl, winning_side)

    def _graceful_close_all(self) -> None:
        if not self.open_positions:
            return
        logger.warning("graceful_close_all n=%d", len(self.open_positions))
        for trade_id, pos in list(self.open_positions.items()):
            try:
                if isinstance(self.executor, PaperExecutor) and self.cfg.exit_.paper_settle_on_expiry:
                    if now_ts() >= pos.market_end_ts:
                        self._settle_paper_position(trade_id)
                    else:
                        logger.warning("paper_position_left_open_on_shutdown trade_id=%s", trade_id)
                    continue
                self._close_position(trade_id, reason="graceful_shutdown")
            except Exception as e:
                logger.error("graceful_close_error trade_id=%s", trade_id, exc_info=e)

    def _refresh_dashboard(self, *, market: MarketSnapshot | None = None) -> None:
        market_payload = None
        if market is not None:
            up = self.clob.orderbook(market.up_token_id)
            dn = self.clob.orderbook(market.down_token_id)
            market_payload = {
                "slug": market.slug,
                "seconds_left": max(0.0, market.end_ts - now_ts()),
                "up_ask": up.best_ask,
                "down_ask": dn.best_ask,
                "spread": min(
                    up.spread if up.spread is not None else 0.99,
                    dn.spread if dn.spread is not None else 0.99,
                ),
            }
        render_dashboard(
            out_path=self.cfg.storage.dashboard_path,
            mode=self.cfg.mode,
            market=market_payload,
            last_signal=self._last_signal_payload,
            risk=self.risk.snapshot(),
            journal_summary=self.journal.summary(),
            extras={"open_positions": len(self.open_positions)},
        )


def _levels_to_json(levels: tuple[OrderbookLevel, ...]) -> list[dict[str, float]]:
    return [
        {
            "price": round(level.price, 4),
            "size": round(level.size, 4),
            "notional_usd": round(level.notional_usd, 4),
        }
        for level in levels
    ]


def _has_hedge_for(positions: dict[str, OpenPosition], parent_trade_id: str) -> bool:
    return any(p.is_hedge and p.parent_trade_id == parent_trade_id for p in positions.values())


def _has_hedge_for_market(positions: dict[str, OpenPosition], market_slug: str) -> bool:
    return any(p.is_hedge and p.market_slug == market_slug for p in positions.values())


def _slug_bucket_start(slug: str) -> int | None:
    try:
        return int(slug.rsplit("-", 1)[-1])
    except (TypeError, ValueError):
        return None


def _feature_float(features: dict, key: str, default: float) -> float:
    try:
        value = features.get(key, default)
        return float(default if value is None else value)
    except (TypeError, ValueError):
        return float(default)


def _clob_ws_feature_dump(
    *,
    enabled: bool,
    up_ws: ClobWsTop | None,
    down_ws: ClobWsTop | None,
    up_http: OrderbookSnapshot,
    down_http: OrderbookSnapshot,
) -> dict[str, object]:
    data: dict[str, object] = {"clob_ws_enabled": 1.0 if enabled else 0.0}
    data.update(_clob_ws_top_features("clob_ws_up", up_ws, up_http))
    data.update(_clob_ws_top_features("clob_ws_down", down_ws, down_http))
    return data


def _clob_ws_side_features(
    side_label: str | None,
    up_ws: ClobWsTop | None,
    down_ws: ClobWsTop | None,
    up_http: OrderbookSnapshot,
    down_http: OrderbookSnapshot,
) -> dict[str, object]:
    if side_label == "UP":
        side_ws, side_http = up_ws, up_http
        opposite_ws, opposite_http = down_ws, down_http
    else:
        side_ws, side_http = down_ws, down_http
        opposite_ws, opposite_http = up_ws, up_http
    data = {"clob_ws_side_label": side_label or ""}
    data.update(_clob_ws_top_features("clob_ws_side", side_ws, side_http))
    data.update(_clob_ws_top_features("clob_ws_opposite", opposite_ws, opposite_http))
    return data


def _clob_ws_top_features(prefix: str, ws_top: ClobWsTop | None, http_book: OrderbookSnapshot) -> dict[str, object]:
    if ws_top is None:
        return {f"{prefix}_seen": 0.0}
    return {
        f"{prefix}_seen": 1.0,
        f"{prefix}_bid": ws_top.best_bid,
        f"{prefix}_ask": ws_top.best_ask,
        f"{prefix}_bid_size": ws_top.best_bid_size,
        f"{prefix}_ask_size": ws_top.best_ask_size,
        f"{prefix}_spread": ws_top.spread,
        f"{prefix}_top_ask_notional_usd": ws_top.top_ask_notional_usd,
        f"{prefix}_age_sec": round(ws_top.age_sec, 3),
        f"{prefix}_event_type": ws_top.event_type,
        f"{prefix}_exchange_ts": ws_top.exchange_ts,
        f"{prefix}_received_ts": ws_top.received_ts,
        f"{prefix}_ask_diff_vs_rest": _clob_ws_diff(ws_top.best_ask, http_book.best_ask),
        f"{prefix}_bid_diff_vs_rest": _clob_ws_diff(ws_top.best_bid, http_book.best_bid),
    }


def _clob_ws_diff(ws_value: float | None, rest_value: float | None) -> float | None:
    if ws_value is None or rest_value is None:
        return None
    return round(float(ws_value) - float(rest_value), 4)


def _optional_float(value) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _serialize_cfg(cfg: AppConfig) -> dict:
    """Produce a config snapshot for the session row (no secrets)."""
    import dataclasses

    return {
        "mode": cfg.mode,
        "symbol": cfg.symbol,
        "market_kind": cfg.market_kind,
        "signal": dataclasses.asdict(cfg.signal),
        "risk": dataclasses.asdict(cfg.risk),
        "hedge": dataclasses.asdict(cfg.hedge),
        "fees": dataclasses.asdict(cfg.fees),
        "live_readiness": dataclasses.asdict(cfg.live_readiness),
        "telegram": {
            "enabled": cfg.telegram.enabled,
            "chat_id_configured": bool(cfg.telegram.chat_id),
            "report_interval_sec": cfg.telegram.report_interval_sec,
            "send_on_start": cfg.telegram.send_on_start,
        },
        "exit": dataclasses.asdict(cfg.exit_),
    }
