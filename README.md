# 5m-poly edge-bot

Automated BTC 5-minute Up/Down strategy for Polymarket.

Default mode is **paper**: live market data, simulated orders, SQLite journal. Live trading is deliberately blocked until you set an explicit acknowledgement flag.

## What It Does

- Resolves the current `btc-updown-5m-<timestamp>` market from Polymarket Gamma.
- Verifies current markets use Chainlink BTC/USD as the resolution source.
- Watches BTC spot movement from the 5-minute window open with an ATR-based floating threshold and CLOB-confirmed soft zone.
- Enters only when core tradeability gates pass; ATR, RSI, z-score and micro-momentum adjust the composite confidence instead of acting as standalone hard blocks.
- Uses fractional Kelly sizing with hard risk caps instead of 50% bankroll bets.
- Can place a small opposite-side paper hedge when skew becomes extreme.
- Records every simulated trade in `runtime/journal.sqlite3` and dashboard state in `runtime/dashboard.txt`.

## Quick Start

```powershell
cd G:\5m-poly\edge-bot
$env:PYTHONPATH='src'
python -m edge_bot.cli dump-config
python -m edge_bot.cli run --mode paper
```

Bounded paper smoke test:

```powershell
$env:PYTHONPATH='src'
python -m edge_bot.cli run --mode paper --max-ticks 3 --log-level WARNING
```

Report:

```powershell
$env:PYTHONPATH='src'
python -m edge_bot.cli report --limit 20
python -m edge_bot.cli fill-report --limit 20
Get-Content runtime\dashboard.txt
```

## Backtest And Sweep

Backtest uses historical BTC 1-minute candles and a proxy model for Polymarket ask prices. It is useful for logic validation, but it is not a replacement for paper trading on live CLOB data.

```powershell
$env:PYTHONPATH='src'
python -m edge_bot.cli backtest --days 30
python -m edge_bot.cli optimize --days 30 --top 10
```

## Ubuntu Server + Telegram

See [UBUNTU_DEPLOY.md](UBUNTU_DEPLOY.md) for systemd deployment and Telegram reports every 15 minutes.

## Tests

If your global Python has unrelated pytest plugins installed, disable autoload:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
$env:PYTHONPATH='src'
python -m pytest -q
```

## Trade Review

See [TRADE_REVIEW_PLAYBOOK.md](TRADE_REVIEW_PLAYBOOK.md) for the required per-trade filter review checklist before changing strategy rules.

## Live Preparation

See [LIVE_READINESS.md](LIVE_READINESS.md) for the live-fill, liquidity-aware sizing, slippage and regime-analysis plan.

## Live Trading Gate

Live mode places real Polymarket orders. It will not start unless all credentials are configured and this exact flag is set:

```powershell
$env:LIVE_TRADING_ACK='I_UNDERSTAND_REAL_MONEY'
python -m edge_bot.cli run --mode live
```

Do not enable live until paper mode has produced enough completed trades to validate fill quality, settlement accuracy, drawdown and skipped-signal behavior.
