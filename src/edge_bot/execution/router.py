"""Execution router with two backends: paper and live.

Paper backend simulates fills at the current best ask (with slippage = spread).
Live backend wraps py-clob-client with allowance pre-check + FAK-> GTC-> force ladder.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from ..core.config import AppConfig
from ..data.polymarket import ClobClient, OrderbookSnapshot
from ..utils.clock import iso_z

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class OrderResult:
    success: bool
    status: str
    order_id: str | None
    filled_price: float
    filled_shares: float
    cost_usd: float
    raw: dict


@dataclass(slots=True)
class CloseResult:
    success: bool
    status: str
    proceeds_usd: float
    exit_price: float
    raw: dict


class PaperExecutor:
    """Simulated executor with realistic spread+latency."""

    def __init__(self, clob: ClobClient, *, taker_fee_rate: float = 0.0) -> None:
        self.clob = clob
        self.taker_fee_rate = max(0.0, float(taker_fee_rate))

    def buy(self, *, token_id: str, notional_usd: float) -> OrderResult:
        book = self.clob.orderbook(token_id)
        ask = book.best_ask or 0.0
        if ask <= 0.0:
            return OrderResult(False, "no_ask", None, 0.0, 0.0, 0.0, {"book": book.__dict__})

        # Pessimistic fill: take the ask and include taker fee in the notional budget.
        fill_px = min(0.99, ask)
        fee_per_share = taker_fee_per_share(fill_px, self.taker_fee_rate)
        shares = round(notional_usd / (fill_px + fee_per_share), 4)
        gross_cost = shares * fill_px
        fee = shares * fee_per_share
        cost = round(gross_cost + fee, 4)
        return OrderResult(
            success=True,
            status="paper_matched",
            order_id=f"paper-{uuid.uuid4().hex[:12]}",
            filled_price=fill_px,
            filled_shares=shares,
            cost_usd=cost,
            raw={"book_ask": ask, "gross_cost": round(gross_cost, 4), "fee_usd": round(fee, 4), "ts": iso_z()},
        )

    def close(self, *, token_id: str, shares: float) -> CloseResult:
        book = self.clob.orderbook(token_id)
        bid = book.best_bid or 0.0
        if bid <= 0.0 or shares <= 0.0:
            return CloseResult(False, "no_bid_or_shares", 0.0, 0.0, {"book": book.__dict__})
        exit_px = max(0.01, bid)
        gross_proceeds = shares * exit_px
        fee = shares * taker_fee_per_share(exit_px, self.taker_fee_rate)
        proceeds = round(max(0.0, gross_proceeds - fee), 4)
        return CloseResult(
            True,
            "paper_matched",
            proceeds,
            exit_px,
            {"book_bid": bid, "gross_proceeds": round(gross_proceeds, 4), "fee_usd": round(fee, 4), "ts": iso_z()},
        )


class LiveExecutor:
    """Live executor using py-clob-client. Only constructed when EDGE_MODE=live.

    All actual signing/order placement happens here. Allowance pre-check is
    performed before SELL closes (Polymarket conditional tokens need a balance
    allowance restore after each settlement cycle).
    """

    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        self._client: object | None = None  # lazy
        self._order_builder: object | None = None

    def _ensure_client(self) -> tuple[Any, Any]:
        if self._client is not None and self._order_builder is not None:
            return self._client, self._order_builder
        try:
            from py_clob_client.client import ClobClient as SignedClob  # type: ignore
            from py_clob_client.clob_types import ApiCreds, OrderArgs, OrderType  # type: ignore
            from py_clob_client.constants import POLYGON  # type: ignore
            from py_clob_client.order_builder.constants import BUY as _BUY  # type: ignore
            from py_clob_client.order_builder.constants import SELL as _SELL  # type: ignore
        except ImportError as e:
            raise RuntimeError("py-clob-client is required for live mode") from e

        auth = self.cfg.auth
        if not auth.can_sign:
            raise RuntimeError("Live mode requires PM_PRIVATE_KEY")

        client = SignedClob(
            host=self.cfg.data.clob_base_url,
            chain_id=POLYGON,
            key=auth.private_key,
            signature_type=auth.signature_type,
            funder=auth.funder or None,
        )
        if auth.has_full_creds:
            creds = ApiCreds(
                api_key=auth.api_key,
                api_secret=auth.api_secret,
                api_passphrase=auth.api_passphrase,
            )
        else:
            creds = client.create_or_derive_api_creds()
        client.set_api_creds(creds)
        self._client = client

        builder = SimpleNamespace(
            BUY=_BUY,
            SELL=_SELL,
            OrderArgs=OrderArgs,
            OrderType=OrderType,
        )
        self._order_builder = builder
        return self._client, self._order_builder

    def buy(self, *, token_id: str, notional_usd: float, snapshot: OrderbookSnapshot) -> OrderResult:
        client, builder = self._ensure_client()
        if snapshot.best_ask is None:
            return OrderResult(False, "no_ask", None, 0.0, 0.0, 0.0, {})
        taker_price = min(0.99, round(float(snapshot.best_ask) + 0.01, 2))
        shares = round(notional_usd / taker_price, 4)
        try:
            order_args = builder.OrderArgs(  # type: ignore[attr-defined]
                token_id=token_id,
                price=taker_price,
                size=shares,
                side=builder.BUY,  # type: ignore[attr-defined]
            )
            resp = client.create_and_post_order(order_args, builder.OrderType.FAK)  # type: ignore[attr-defined]
        except Exception as e:
            logger.warning("live_buy_error", exc_info=e)
            return OrderResult(False, "exception", None, 0.0, 0.0, 0.0, {"error": str(e)})

        success = bool(resp and resp.get("success") is True)
        status = str(resp.get("status") if resp else "")
        order_id = resp.get("orderID") if resp else None
        taking = float(resp.get("takingAmount") or 0) if resp else 0.0
        making = float(resp.get("makingAmount") or 0) if resp else 0.0
        return OrderResult(
            success=success and status.lower() == "matched",
            status=status,
            order_id=order_id,
            filled_price=taker_price,
            filled_shares=taking,
            cost_usd=making,
            raw=resp or {},
        )

    def close(self, *, token_id: str, shares: float, snapshot: OrderbookSnapshot) -> CloseResult:
        client, builder = self._ensure_client()
        if snapshot.best_bid is None or shares <= 0.0:
            return CloseResult(False, "no_bid_or_shares", 0.0, 0.0, {})
        for attempt in range(self.cfg.exit_.close_retry_max):
            taker_price = max(0.01, round(float(snapshot.best_bid) - 0.01 * attempt, 2))
            try:
                order_args = builder.OrderArgs(  # type: ignore[attr-defined]
                    token_id=token_id,
                    price=taker_price,
                    size=shares,
                    side=builder.SELL,  # type: ignore[attr-defined]
                )
                resp = client.create_and_post_order(order_args, builder.OrderType.GTC)  # type: ignore[attr-defined]
            except Exception as e:
                logger.warning("live_close_error_attempt_%d", attempt, exc_info=e)
                time.sleep(self.cfg.exit_.close_retry_delay_sec)
                continue
            success = bool(
                resp and resp.get("success") is True and str(resp.get("status")).lower() == "matched"
            )
            if success:
                proceeds = float(resp.get("takingAmount") or 0)
                return CloseResult(True, "matched", proceeds, taker_price, resp or {})
            time.sleep(self.cfg.exit_.close_retry_delay_sec)
        return CloseResult(False, "exhausted_retries", 0.0, 0.0, {})


def make_executor(cfg: AppConfig, clob: ClobClient) -> PaperExecutor | LiveExecutor:
    if cfg.mode == "live":
        if cfg.live_trading_ack != "I_UNDERSTAND_REAL_MONEY":
            raise RuntimeError(
                "Live mode is blocked until LIVE_TRADING_ACK=I_UNDERSTAND_REAL_MONEY is set. "
                "Run paper/backtest first; live mode places real Polymarket orders."
            )
        return LiveExecutor(cfg)
    taker_fee_rate = cfg.fees.paper_taker_fee_rate if cfg.fees.paper_taker_fees_enabled else 0.0
    return PaperExecutor(clob, taker_fee_rate=taker_fee_rate)


def taker_fee_per_share(price: float, fee_rate: float) -> float:
    price = max(0.0, min(1.0, float(price)))
    return max(0.0, float(fee_rate)) * price * (1.0 - price)
