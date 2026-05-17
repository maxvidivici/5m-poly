# Профессиональный анализ: Polymarket BTC 5m Up/Down

## 1. Ваш базовый репозиторий (Novals83/5min-btc-polymarket)

### Что хорошо
- Тригерит вход по **CLOB best ask**, а не Gamma outcomePrices — устойчиво к сток-снэпшотам Gamma.
- Профили `conservative` / `aggressive` с конфигом в YAML.
- Базовый exit-flow: SL по проценту + time-exit + двойной fallback (FAK → GTC → cancel+force GTC).
- Allowance pre-check (см. `pm_live_trade_runner.py` в `polymarket-hl-strategy`).
- Резолвинг slug текущего 5m bucket: `btc-updown-5m-<unix5m>`.

### Слабые места (где конкуренты впереди)
1. **Сигнал на вход примитивный**: только `clob_ask ≥ 0.70`. Нет проверки реального движения BTC ($70–$100), нет z-score, нет RSI, нет ATR-фильтра. Полностью игнорируется ваша стратегия по движению цены.
2. **Источник цены BTC** отсутствует. Вы говорили про "движение BTC на $70–$100", но скрипт не читает спот цену вообще — только опросит CLOB.
3. **Sizing**: фикс `--stake-usd 5`, без Kelly, без equity-based, без дневного капа в коде (только в YAML, но не применяется).
4. **Хеджа фактически нет**: YAML описывает hedge config, но в `test_btc_5m_session_exit_sl.py` хедж не реализован.
5. **REST polling каждые 5с** — за 2 секунды до окончания опоздаешь на 1–4с.
6. **Нет heartbeat/watchdog/graceful shutdown**.
7. **Нет paper-mode** — только `--execute` или сухой прогон без журнала.
8. **Нет backtest** на исторических данных.
9. **Нет trade journal / dashboard**.
10. **Жёсткая зависимость** от внешнего `pm-hl-conservative-plus-repo` (subprocess к `src/live/pm_live_trade_runner.py`).

## 2. Базовый production stack (Novals83/polymarket-hl-strategy)

Это уже более серьёзный шаблон (бот для **15-минутного** BTC рынка с сигналами от Hyperliquid). Полезное для нашего проекта:
- `src/live/pm_live_trade_runner.py` — CLOB client init, balance/allowance precheck, поддержка FAK/FOK/GTC, авто-restore allowance.
- `scripts/pm_live_exit_manager.py` (452 строки) — менеджер SL/TP/trailing/time-exit с volatility-adaptive bands.
- `scripts/pm_reconcile.py`, `pm_bulk_close.py`, `pm_heartbeat_loop.py`, `pm_live_watchdog.sh`, `start_with_health.sh`.
- FastAPI health endpoint, Docker, graceful stop.

Всё это можно **переиспользовать** в улучшенном боте, портировав на 5-минутный рынок.

## 3. Боты-конкуренты из IMPROVEMENT_GUIDE.md

### `jmazzini/5m-poly-bot` — самый похожий конкурент
Реализует именно вашу стратегию:
- **Window Delta** — `(current - period_open) / period_open` от Binance (с этого начинается ваш "$70–$100 move").
- **Micro-momentum** — направление последних 2× 1-минутных свечей.
- **ATR-фильтр** — если текущий диапазон > 1.5× ATR(5) → skip (слишком волатильно).
- **Composite Confidence Score** 0–100% (weight: 7 при delta>1%, 5 при >0.2%, 3 при >0.1%, 1 при >0.05%; +2 если momentum подтверждает).
- **Tight entry**: 10–50 секунд до закрытия.
- **BTC strict**: PM price ≥ 0.94 (vs ETH 0.92).
- **Paper / Dry-run / Live** режимы.
- **Параллельный fetch** через ThreadPoolExecutor.

**Слабости**: нет risk manager / daily caps, нет хеджа, нет trade journal, REST polling, нет тестов.

### `ThinkEnigmatic/polymarket-bot-arena`
Arena из 4 ботов с эволюцией, реальные результаты на 276 трейдах: **-$52 P&L**. Ценные эмпирические инсайты:
- **Market price — самый сильный сигнал**: когда YES priced > 65с — YES выигрывает ~100%.
- **Contrarian/mean-reversion стратегии теряют деньги** на 5m рынках.
- **Confidence 0.30–0.50 — sweet spot** (67.9% WR, +$48).
- **Confidence >0.50 теряет деньги** (большие ставки → большие убытки).
- **NO bets**: 44.9% WR против YES 49.2% (слабое смещение в сторону YES).
- **Никогда не ставь против цены >65с или <35с**.
- Binance WS для свечей, SQLite trade journal, FastAPI dashboard, learning system.

**Используем**: Binance WS pattern, market-consensus guard (никогда не против >65c/<35c), confidence cap, SQLite журнал.

### `roswelly/polymarket-arbitrage-bot` — risk + arb стратегии
- **`risk_manager.py`**: fractional Kelly (×0.25), `daily_pnl` cap, `max_open_positions`, `min_balance`, `consecutive_losses` cooldown, `max_portfolio_exposure_pct`, duplicate market check.
- 5 стратегий: intra-market arb (YES+NO<$1), combinatorial, cross-platform, endgame (>93%), momentum/mean-rev.
- Многотаймфреймовая параметризация (5m / 15m / 1h).

**Используем**: целиком архитектуру risk-менеджера. Дополнительно — Strategy 1 (intra-market YES+NO<$1) можно подсадить как **бесплатное альфа-ускорение** когда основной сигнал не сработал.

## 4. Архитектура улучшенного бота (наше преимущество)

**Имя проекта**: `polymarket-btc-5m-edge` (или ветка `feat/edge-v1` в `5min-btc-polymarket`).

### Слой 1 — Data
- **Coinbase Pro WebSocket** (BTC-USD `matches` + `ticker`) — **primary**, потому что Polymarket резолвит на Coinbase BTC/USD spot.
- **Binance WS** `btcusdt@kline_1m` + `@trade` — **fallback** + cross-check.
- **Polymarket Gamma REST** для slug + outcome metadata.
- **Polymarket CLOB WS** для orderbook diff (best bid/ask + size + spread).
- Все источники с staleness detector (>3с → skip cycle).

### Слой 2 — Signals (Composite Edge Score)
```
score = w_window_delta * window_delta_score
      + w_zscore       * zscore_safety        (отбраковка перегретых движений)
      + w_rsi          * rsi_safety           (overbought/oversold)
      + w_micro_mom    * micro_momentum_score (2× 1m candles direction)
      + w_skew         * book_skew_score      (depth-weighted bid/ask)
      + w_mkt_price    * market_price_edge    (PM ask уже подтверждает)
      - w_atr_penalty  * atr_overheat
      - w_spread_pen   * spread_penalty
```
- **Entry gate**: confidence ≥ 0.45 (sweet spot из arena-инсайтов) И BTC move ≥ $70 И seconds_left ∈ [10..120].
- **Market-consensus guard**: запрет ставить против ask < 0.35 (контрариан = -$).
- **Asymmetry**: лёгкий YES-bias (+1% confidence) — эмпирически выигрывает.

### Слой 3 — Risk Manager
- **MAX_POSITION_PCT**: 2–5% от equity (НЕ 50%) — критичная защита.
- **DAILY_LOSS_CAP**: 8% от стартового equity.
- **MAX_CONSECUTIVE_LOSSES**: 3 → cooldown 15min.
- **MAX_TRADES_PER_HOUR**: 4 (anti-overtrading).
- **Fractional Kelly** для размера: `size = equity × clamp(kelly × 0.25, 0.02, 0.05)`.
- **Hard min balance** — не торговать ниже $50.

### Слой 4 — Execution
- **Adaptive order ladder**: FAK сначала, если no-fill → GTC@mid+0.01, если ещё не fill → cancel + aggressive GTC@best_bid-0.02.
- **Allowance auto-restore** перед каждой сделкой.
- **Tight entry**: окно 10–50с до закрытия (не 60+с — поздно меньше = выше уверенность).
- **Sub-second timing** через async sleep.

### Слой 5 — Hedge Engine
- Триггер: `seconds_left ≤ 30s` И `skew ≥ 95/5` И `main_position_open`.
- Размер: 2–4% основной позиции, абсолютный кап $2.
- Тикер: противоположный токен по `best_ask + 0.01`.
- **НЕ хедж в первый удар** — только когда позиция уже в плюсе и риск переворота высокий.

### Слой 6 — Trade Journal & Dashboard
- **SQLite** `trades.db`: `trade_id, slug, side, entry_time, entry_px, shares, cost, exit_time, exit_px, pnl, signal_features (JSON), close_reason`.
- **Text dashboard** `runtime/dashboard.txt` (как в hl-strategy).
- **Опц. FastAPI** на `:8050` (как в bot-arena).
- **JSON-line audit log** `runtime/audit.log` (структурированный).

### Слой 7 — Operational Safety
- **Heartbeat** каждые 5с → если stale → kill switch.
- **Graceful shutdown** (SIGTERM → close all open positions через force-close pipeline).
- **Watchdog** скрипт (systemd-friendly + Docker).
- **Health endpoint** `:8080/health` (как в hl-strategy).

### Слой 8 — Backtest & Paper
- **Backtest**: исторические свечи Binance + архив Polymarket markets (по slug-pattern) → симуляция входов с теми же фильтрами; output: equity curve, WR, Sharpe, max DD.
- **Paper-mode**: live data, simulated orders, реальный журнал — для валидации в реальном времени без капитала.

### Слой 9 — Testing
- `pytest` unit-tests для: z-score, RSI, ATR, kelly, hedge trigger, risk caps.
- Mock CLOB/Gamma/Binance — детерминированные тесты.
- CI: GitHub Actions с ruff + pyright + pytest.

## 5. Почему это лучше каждого конкурента

| Фича | base | jmazzini | arena | roswelly | **edge (наш)** |
|---|---|---|---|---|---|
| Window Delta от спот-биржи | ❌ | ✅ | ✅ | ❌ | ✅ (Coinbase + Binance fallback) |
| Z-score / RSI / ATR safety | ❌ | частично | ❌ | ✅ | ✅ полный |
| Composite Confidence | ❌ | ✅ | ✅ | ❌ | ✅ + market-consensus guard |
| Risk manager c Kelly | ❌ | ❌ | базовый | ✅ | ✅ + daily/hourly caps |
| Hedge engine | YAML only | ❌ | ❌ | ❌ | ✅ реализован |
| WebSocket data | ❌ | ❌ | ✅ | ❌ | ✅ multi-source + staleness |
| Tight entry 10–50с | ❌ (≥60с) | ✅ | ❌ | ❌ | ✅ |
| Trade journal SQLite | ❌ | ❌ | ✅ | базовый | ✅ + audit log |
| Backtest framework | ❌ | ❌ | ❌ | ❌ | ✅ |
| Paper mode | ❌ | ✅ | ✅ | ❌ | ✅ |
| Graceful shutdown | ❌ | ❌ | ✅ | базовый | ✅ |
| Adaptive close ladder | базовый | ❌ | ❌ | ❌ | ✅ FAK→GTC→force |
| Tests + CI | ❌ | ❌ | ❌ | ❌ | ✅ ruff+pyright+pytest |

## 6. Что нужно от вас
1. **PAT с правами `repo`** — куда коммитим? Я предлагаю **создать новую ветку `feat/edge-v1` в форке `Novals83/5min-btc-polymarket` под вашим аккаунтом** (чистая история, видно diff). Альтернатива — отдельный новый репозиторий `polymarket-btc-5m-edge`.
2. **PM credentials** (для будущего live-режима; в paper-mode не нужно):
   - `PM_PRIVATE_KEY` — EOA private key
   - `PM_FUNDER` (или `PM_ADDRESS`) — proxy wallet
   - `PM_API_KEY` / `PM_API_SECRET` / `PM_API_PASSPHRASE` — CLOB L2 (или сгенерируем из ключа автоматом).
3. **Подтверждение порядка работ**: начну с paper-mode (live данные, виртуальные ордера) → backtest → unit tests → CI green → передам вам бот для тестового запуска → дальше live.

После получения PAT я создам ветку и начну выкладывать код модулями. Ориентир: ~2000–3000 строк Python, ~15 файлов, готовый Dockerfile, готовый paper-mode за 1 запуск.
