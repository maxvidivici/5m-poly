"""Orderbook-depth fill simulation for paper/live-readiness analysis."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ..data.polymarket import OrderbookLevel


@dataclass(slots=True)
class FillLevel:
    price: float
    shares: float
    gross_notional_usd: float
    fee_usd: float
    cost_usd: float

    def as_dict(self) -> dict[str, float]:
        return {
            "price": round(self.price, 4),
            "shares": round(self.shares, 4),
            "gross_notional_usd": round(self.gross_notional_usd, 4),
            "fee_usd": round(self.fee_usd, 4),
            "cost_usd": round(self.cost_usd, 4),
        }


@dataclass(slots=True)
class FillSimulation:
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
    levels_used: tuple[FillLevel, ...]


def simulate_buy_fill(
    asks: Sequence[OrderbookLevel],
    *,
    target_notional_usd: float,
    max_level_price: float | None,
    fee_rate: float = 0.0,
    max_avg_fill_price: float | None = None,
) -> FillSimulation:
    """Simulate a taker buy using visible ask depth.

    ``target_notional_usd`` is treated as the all-in budget: gross token cost
    plus estimated taker fee. The simulation can return a partial fill when the
    visible book is too thin or when later levels would violate price limits.
    """
    target = max(0.0, float(target_notional_usd))
    clean_asks = tuple(sorted((a for a in asks if a.price > 0 and a.size > 0), key=lambda a: a.price))
    best_ask = clean_asks[0].price if clean_asks else None
    if target <= 0.0 or not clean_asks:
        return _empty_result(target, best_ask)

    gross = 0.0
    fee = 0.0
    shares = 0.0
    cost = 0.0
    levels: list[FillLevel] = []
    avg_cap = float(max_avg_fill_price) if max_avg_fill_price is not None and max_avg_fill_price > 0 else None
    price_cap = float(max_level_price) if max_level_price is not None and max_level_price > 0 else None

    for ask in clean_asks:
        if price_cap is not None and ask.price > price_cap + 1e-9:
            break

        remaining = target - cost
        if remaining <= 1e-9:
            break

        fee_per_share = taker_fee_per_share(ask.price, fee_rate)
        unit_cost = ask.price + fee_per_share
        if unit_cost <= 0.0:
            continue

        take_shares = min(ask.size, remaining / unit_cost)
        if avg_cap is not None and ask.price > avg_cap:
            if shares <= 1e-9:
                break
            room = avg_cap * shares - gross
            if room <= 1e-9:
                break
            take_shares = min(take_shares, room / (ask.price - avg_cap))

        if take_shares <= 1e-9:
            break

        gross_take = take_shares * ask.price
        fee_take = take_shares * fee_per_share
        cost_take = gross_take + fee_take
        levels.append(
            FillLevel(
                price=ask.price,
                shares=take_shares,
                gross_notional_usd=gross_take,
                fee_usd=fee_take,
                cost_usd=cost_take,
            )
        )
        gross += gross_take
        fee += fee_take
        shares += take_shares
        cost += cost_take

    avg_price = gross / shares if shares > 0 else None
    slippage = (avg_price - best_ask) if avg_price is not None and best_ask is not None else None
    fill_ratio = min(1.0, cost / target) if target > 0 else 0.0
    fillable = target > 0 and target - cost <= 1e-6
    return FillSimulation(
        target_notional_usd=round(target, 4),
        fillable=fillable,
        fill_ratio=round(fill_ratio, 6),
        actual_notional_usd=round(cost, 4),
        gross_notional_usd=round(gross, 4),
        best_ask=round(best_ask, 4) if best_ask is not None else None,
        weighted_avg_fill_price=round(avg_price, 6) if avg_price is not None else None,
        max_level_price_used=round(levels[-1].price, 4) if levels else None,
        slippage_from_best_ask=round(slippage, 6) if slippage is not None else None,
        estimated_fee_usd=round(fee, 4),
        estimated_shares=round(shares, 4),
        pnl_if_won=round(shares - cost, 4),
        pnl_if_lost=round(-cost, 4),
        levels_used=tuple(levels),
    )


def taker_fee_per_share(price: float, fee_rate: float) -> float:
    price = max(0.0, min(1.0, float(price)))
    return max(0.0, float(fee_rate)) * price * (1.0 - price)


def _empty_result(target: float, best_ask: float | None) -> FillSimulation:
    return FillSimulation(
        target_notional_usd=round(target, 4),
        fillable=False,
        fill_ratio=0.0,
        actual_notional_usd=0.0,
        gross_notional_usd=0.0,
        best_ask=round(best_ask, 4) if best_ask is not None else None,
        weighted_avg_fill_price=None,
        max_level_price_used=None,
        slippage_from_best_ask=None,
        estimated_fee_usd=0.0,
        estimated_shares=0.0,
        pnl_if_won=0.0,
        pnl_if_lost=0.0,
        levels_used=(),
    )
