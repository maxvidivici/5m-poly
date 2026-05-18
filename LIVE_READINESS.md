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

## Separate Scenario: Tail Reversal Probe

This is not a hedge for an open main position. It is a separate shadow/paper scenario for rare, high-upside reversals when the main strategy wanted to enter one side but the entry was blocked by high-volatility or reversal-risk rules.

Goal:

```text
Risk a small fixed amount on the cheap opposite side only when the payout is large enough to justify a low winrate.
```

Example:

```text
Main signal: DOWN
Main entry: blocked by high volatility / reversal risk
Opposite side: UP
UP ask: 0.05-0.10
Probe size: $1
```

The expected upside is approximately:

```text
ask 0.10 -> about 10x gross payout
ask 0.05 -> about 20x gross payout
ask 0.01 -> about 100x gross payout
```

Initial paper-only entry rules:

```text
main entry would otherwise pass core direction logic
main entry is blocked by high_volatility / late_reversal_risk logic
opposite_ask <= 0.10, or <= 0.12 during analysis only
opposite_ask >= 0.01
opposite spread <= 0.03
opposite top ask liquidity >= $1
only one probe per market
fixed probe size = $1
```

Do not create a probe when the main entry was blocked by operational or bad-market-data reasons:

```text
missing_data
no_asks_either_side
spread_too_wide
top_ask_too_thin
too_late_to_enter
API / resolution / stale-price errors
```

The probe also needs a physical reversal sanity check:

```text
distance_to_open_usd = abs(delta_usd)
required_move_per_sec = distance_to_open_usd / seconds_left
```

Skip the probe if the required move back through the open line is unrealistic for the remaining time.

Track probes separately from main strategy and hedge results:

```text
probe_side
probe_ask
probe_size_usd
estimated_shares
pnl_if_won
pnl_if_lost
atr_pct
delta_usd
seconds_left
block_reason
official_winning_side
official_probe_pnl
```

Reports must show probe PnL separately by:

```text
opposite ask bucket: <0.03 / 0.03-0.05 / 0.05-0.10 / 0.10-0.12
atr bucket
seconds_left bucket
distance_to_open bucket
main block reason
```

This scenario should remain shadow/paper until it has enough official-settled samples to prove positive expected value after fees.

## Separate Scenario: Strong Add-On Entry

This is not a return to unrestricted repeated entries. The base strategy should still allow only one normal main entry per market. A second entry is allowed only as a separate `ADD_ON` scenario when the first position is already open and the signal has become materially stronger.

Initial test posture:

```text
MAX_ENTRIES_PER_MARKET=2
normal main entries per market = 1
second entry type = ADD_ON only
ADD_ON remains shadow/paper until proven by official-settled samples
```

Candidate ADD_ON conditions:

```text
first main entry is already open
first entry is not hedge
ADD_ON side == first entry side
seconds_left >= 45
side_ask >= 0.82
delta_strong_ratio >= 1.00
abs(zscore) >= 1.00
top_ask_notional_usd >= addon_size
spread <= 0.02
market exposure after ADD_ON <= configured cap
addon_size <= base_size
```

Example sizing for paper analysis:

```text
base_size = $3
addon_size = $2-$3 max
```

Purpose:

```text
Do not bring back weak repeated entries.
Allow extra exposure only when the market moves further in the original direction and the signal quality is much stronger.
Keep the add-on smaller than or equal to the base position.
```

Required historical/counterfactual report before enabling:

```text
how many ADD_ON candidates appeared
ADD_ON wins/losses/PnL separately from base trades
combined base + ADD_ON market PnL
whether ADD_ON would have increased losses in losing markets
side_ask bucket
delta_strong_ratio bucket
zscore bucket
seconds_left bucket
liquidity bucket
```

Do not enable ADD_ON from intuition or after one good trade. Review after at least `30-50` new official-settled base trades under the current filters.

Initial SQL-style report should compare:

```text
base_only_pnl
base_plus_addon_pnl
addon_only_pnl
markets_where_addon_won
markets_where_addon_lost
max_market_loss_with_addon
```

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
