# Live Readiness Plan

This bot must not move to real-money trading until paper results are validated with official Polymarket outcomes and live-style fill simulation.

## Current Live Gate

Live mode is blocked unless `LIVE_TRADING_ACK=I_UNDERSTAND_REAL_MONEY` is set. Keep the bot in `paper` while validating:

- official Polymarket/Chainlink settlement accuracy;
- CLOB fill quality;
- slippage by order size;
- drawdown and losing streaks;
- behavior across market regimes.

## Settlement Rule

Paper PnL must use official Polymarket outcome, not Coinbase/Binance spot close.

Allowed for signal input:

- Coinbase/Binance BTC price as a fast momentum proxy;
- Polymarket CLOB best bid/ask and depth.

Allowed for final PnL:

- Polymarket Gamma resolved `outcomePrices` from the market slug;
- `resolutionSource` must match Chainlink BTC/USD.

## Liquidity-Aware Live Sizing

Live orders must not use a fixed notional blindly. The bot should calculate a target size, then only buy as much as the orderbook allows at acceptable prices.

Definitions:

```text
target_size_usd = min(risk_size_usd, max_position_usd)
max_allowed_price = min(clob_ask_max, best_ask + max_fill_slippage)
fillable_size_usd = notional available across ask levels where price <= max_allowed_price
actual_size_usd = min(target_size_usd, fillable_size_usd)
```

Entry is allowed only if:

```text
actual_size_usd >= min_position_usd
actual_size_usd >= target_size_usd * min_fill_ratio
weighted_avg_fill_price <= max_allowed_avg_price
```

If those checks fail, skip the trade or enter with the smaller `actual_size_usd` only when it still meets the minimums.

## Example

Target order size is `$50` and the ask book is:

```text
0.80 - $18
0.81 - $20
0.82 - $40
```

If `max_allowed_price = 0.81`, the bot buys:

```text
0.80 - $18
0.81 - $20
actual_size_usd = $38
```

It does not buy the `0.82` level, because that level is outside the allowed price. Buying `$38` with good edge is preferred over forcing `$50` and damaging expected value.

If `0.82` is still allowed by the strategy, the bot can take another `$12` at `0.82` to complete the `$50` target.

## Fill Simulation In Paper

For every new paper trade, the bot stores an orderbook snapshot and simulated fills for several target sizes:

```text
$10
$25
$50
$100
```

For each target, record:

```text
fillable: true/false
requested_notional_usd
actual_notional_usd
best_ask
weighted_avg_fill_price
max_level_price_used
slippage_from_best_ask
estimated_fee_usd
estimated_shares
pnl_if_won
pnl_if_lost
```

This lets reports answer:

- whether `$50` or `$100` was realistically fillable;
- whether fill quality was still profitable after fee and slippage;
- how often the strategy only supports smaller sizes.

Run the report with:

```bash
PYTHONPATH=src .venv/bin/python -m edge_bot.cli fill-report --limit 20
```

The fill tables only populate for trades opened after this code is deployed. Older trades still have their official settlement PnL, but not a historical depth snapshot.

## Proposed Config Keys

```env
LIVE_MAX_POSITION_USD=50
LIVE_MIN_POSITION_USD=5
LIVE_MAX_FILL_SLIPPAGE=0.02
LIVE_MIN_FILL_RATIO=0.50
LIVE_MAX_AVG_FILL_PRICE=0.92
FILL_SIM_TARGETS_USD=10,25,50,100
ORDERBOOK_SNAPSHOT_LEVELS=10
```

These are starting points for paper analysis, not final live settings.

## Market Regime Tags

Each trade should be tagged so results are not judged from one market regime only:

```text
trend_down
trend_up
sideways
high_volatility
low_volatility
late_reversal_risk
```

Reports should break down performance by:

```text
side: UP / DOWN
delta bucket: 55-65 / 65-75 / 75-100 / 100+
ask bucket: 0.68-0.75 / 0.75-0.85 / 0.85-0.95
seconds_left bucket: 10-30 / 30-60 / 60-90 / 90-120
liquidity bucket: <25 / 25-50 / 50-100 / 100+
regime tag
```

## Extreme Volatility Block

The strategy blocks new entries when current 5m ATR volatility is extreme:

```env
ATR_HARD_BLOCK_ENABLED=true
ATR_HARD_BLOCK_PCT=0.30
```

This means `atr_pct >= 0.30` skips the entry and records a signal reason like:

```text
atr_pct_0.310_above_hard_0.300
```

The initial threshold comes from observed results: visible overnight winners were below about `0.224%`, while the three official losses that triggered this rule were around `0.307-0.310%`.

## Live Readiness Checklist

Do not enable real-money mode until all items are true:

- paper has at least 100 official-settled trades for first review;
- official-settled PnL remains positive after fees;
- losses and drawdown are understood, not hidden by settlement bugs;
- `$25` and `$50` fill simulation is positive after slippage;
- strategy is tested across DOWN, UP and sideways regimes;
- no stale price or unresolved market errors affect settlement;
- live order code supports liquidity-aware sizing and FAK/IOC-style safety;
- Telegram reports separate main strategy, hedge and net PnL;
- live credentials are configured only after explicit manual approval.
