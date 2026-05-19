# Strategy Changelog and Research Log

Этот документ фиксирует все изменения стратегии, наблюдений и операционных настроек, которые были сделаны в ходе разбора сделок. Он дополняет `TRADE_REVIEW_PLAYBOOK.md`: playbook описывает как разбирать сделки, а этот файл описывает что уже было изменено и почему.

Главное правило: каждое изменение должно быть помечено как `ACTIVE` или `OBSERVATION_ONLY`.

- `ACTIVE` влияет на входы, размер, reentry или settlement.
- `OBSERVATION_ONLY` только пишет данные в journal/features и не меняет торговое решение.
- Перед новым active-фильтром нужен counterfactual: сколько wins он убивает, сколько losses убирает, какой PnL меняет.

## Current Production Bot

Сервис: `edge-bot-official`

Путь: `/opt/edge-bot-official`

Ветка: `official-settlement-fix`

Текущий смысл конфигурации:

- `EDGE_MODE=paper`
- settlement: official Polymarket / Chainlink outcome
- BTC signal input: Coinbase primary, Binance fallback
- `CLOB_ASK_MIN=0.71`
- `SOFT_DELTA_CLOB_ASK_MIN=0.71`
- `ZSCORE_HARD_BLOCK_ENABLED=true`
- `ZSCORE_HARD_BLOCK_ABS=3.0`
- `MAX_ENTRIES_PER_MARKET=2`
- `ADD_ON_ENABLED=true`
- `BORDERLINE_WEAK_MAIN_GUARD_ENABLED=true`
- `BORDERLINE_WEAK_MAIN_MAX_SIDE_ASK=0.73`
- `BORDERLINE_WEAK_MAIN_MIN_DELTA_STRONG_RATIO=0.60`
- `BORDERLINE_WEAK_MAIN_MIN_ABS_ZSCORE=1.00`

## Baseline Bot

Сервис: `edge-bot-baseline-settlement`

Путь: `/opt/edge-bot-baseline-settlement`

Commit: `591957e Use official Polymarket outcomes for paper settlement`

Цель: параллельно сравнить текущего бота с более старой логикой входа, но уже с official settlement.

Отличия baseline:

- использует official Polymarket / Chainlink settlement;
- использует Coinbase/Binance как signal input;
- не имеет strict ADD_ON;
- не имеет CLOB websocket observation;
- не имеет zscore hard block;
- не имеет high-vol hard/edge guard;
- не имеет borderline weak main guard;
- не имеет candle reversal observation fields;
- Telegram выключен: `TELEGRAM_ENABLED=false`;
- пишет отдельный journal: `/opt/edge-bot-baseline-settlement/runtime/journal.sqlite3`.

Baseline стартовал параллельно 2026-05-19 около `13:50:07 CEST` (`11:50:07 UTC`). Для честного сравнения current bot надо фильтровать с этого времени.

## 1. Official Settlement For Paper

Status: `ACTIVE`

Commit: `591957e Use official Polymarket outcomes for paper settlement`

Что сделано:

- paper trades теперь reconciled через official Polymarket outcome;
- используется `required_resolution_source=chainlink`;
- добавлена команда `edge_bot.cli reconcile-official --apply --mode paper`;
- `signal_observations` тоже можно settle-ить official winning_side;
- результат больше не считается только по spot/proxy close.

Зачем:

- старый paper/proxy settlement мог показывать плюс там, где official Polymarket outcome был другим;
- для BTC 5m Up/Down итог должен сравниваться с official outcome, а не только с Coinbase/Binance spot.

Как проверять:

```bash
cd /opt/edge-bot-official && \
sudo -u edgebot bash -lc 'cd /opt/edge-bot-official && PYTHONPATH=src .venv/bin/python -m edge_bot.cli reconcile-official --apply --mode paper'
```

## 2. Paper Taker Fees

Status: `ACTIVE`

Commit: `6cdd3e2 Account for paper taker fees`

Что сделано:

- paper execution учитывает taker fee model;
- это делает paper PnL ближе к live-реальности;
- fee settings живут в `FeeConfig`.

Зачем:

- маленькие wins у дорогих контрактов легко переоцениваются без fee;
- нужно считать PnL консервативнее.

## 3. Signal Observations Journal

Status: `OBSERVATION_ONLY`

Commit lineage: `e14e355 Add signal observation journal`, затем расширения в следующих commits.

Что сделано:

- каждый enter/skip пишется в `signal_observations`;
- сохраняются reason, side, seconds_left, asks/bids, delta, features_json, winning_side после settlement;
- это позволяет анализировать не только сделки, но и пропущенные сигналы.

Зачем:

- без `signal_observations` нельзя понять, что бот не взял и почему;
- все новые фильтры проверяются через counterfactual по observations/trades.

## 4. Live-Readiness Fill Analytics

Status: `OBSERVATION_ONLY`

Commit: `81dd37c Add ATR hard volatility block and live-readiness fill analytics`

Что сделано:

- после входа пишутся snapshots стакана;
- пишутся fill simulations на целевые размеры (`FILL_SIM_TARGETS_USD=10,25,50,100`);
- таблицы: `orderbook_snapshots`, `fill_simulations`.

Что НЕ сделано:

- fill simulation пока не блокирует вход;
- active pre-fill guard еще не включен.

Зачем:

- понять, исполнился бы live-order по похожей цене;
- оценить slippage/weighted average fill price;
- отделить плохой сигнал от плохой исполнимости.

Текущий вывод:

- для paper-ставок около `$3` текущие losses скорее связаны с wrong side/reversal, а не с fill;
- для live и больших размеров понадобится active pre-fill guard.

## 5. ATR Hard Block And High-Vol Edge Guard

Status: `ACTIVE`

Commit: `81dd37c Add ATR hard volatility block and live-readiness fill analytics`

Настройки:

- `ATR_HARD_BLOCK_ENABLED=true`
- `ATR_HARD_BLOCK_PCT=0.30`
- `HIGH_VOL_EDGE_GUARD_ENABLED=true`
- `HIGH_VOL_EDGE_GUARD_ATR_PCT=0.22`
- `HIGH_VOL_MIN_DELTA_STRONG_RATIO=0.80`
- `HIGH_VOL_MIN_ABS_ZSCORE=0.50`

Что делает:

- блокирует extreme 5m volatility по ATR;
- в high-vol режиме требует более сильный delta/zscore edge;
- снижает входы в ситуации, где резкий возврат через open-line вероятнее.

Риск:

- high-vol фильтры могут убивать часть хороших trend continuation moves;
- поэтому их надо контролировать через `signal_observations` и official settled outcomes.

## 6. Strict ADD_ON Entry Guard

Status: `ACTIVE`

Commit: `24fd07c Add strict add-on entry guard`

Что сделано:

- `MAX_ENTRIES_PER_MARKET=2`, но обычный `main` максимум один;
- второй вход разрешен только как `ADD_ON`;
- ADD_ON не является свободным вторым входом.

Условия ADD_ON:

- первый main уже открыт;
- первый вход не hedge;
- same side;
- `seconds_left >= 45`;
- `side_ask >= 0.82`;
- `delta_strong_ratio >= 1.00`;
- `abs_zscore >= 1.00`;
- `spread <= 0.02`;
- `top_ask_notional_usd >= addon_size`;
- market exposure после addon <= cap;
- `addon_size <= base_size`.

Зачем:

- убрать слабые повторные входы;
- разрешить добивку только когда рынок реально дожал в нашу сторону;
- не превращать второй вход в удвоение риска на слабом сигнале.

Подтверждение из сделки id 2:

- после входа были моменты `side_ask=0.84-0.91`, но `delta_ratio=0.65-0.70`;
- ADD_ON был заблокирован `addon_delta_ratio_below_1.00`;
- official outcome был UP, то есть свободный второй вход усилил бы loss;
- strict ADD_ON сработал правильно.

## 7. CLOB Websocket Cache

Status: `OBSERVATION_ONLY`

Commit: `a927f94 Add observation-only CLOB websocket cache`

Настройки:

- `PM_CLOB_WS_ENABLED=true`
- `PM_CLOB_WS_URL=wss://ws-subscriptions-clob.polymarket.com/ws/market`

Что сделано:

- добавлен `ClobWsObserver`;
- websocket top-of-book пишется в `features_json`;
- поля включают `clob_ws_*`, `clob_ws_side_*`, `clob_ws_opposite_*`, diff vs REST и age.

Что НЕ сделано:

- websocket не влияет на вход;
- это не active filter и не источник исполнения.

Зачем:

- понять, давал ли WS более свежий CLOB ask/bid, чем REST;
- позже проверить, помог бы WS снизить latency/slippage или избежать stale REST.

## 8. Zscore Hard Block

Status: `ACTIVE`

Commit: `4c625c7 Add zscore hard block`

Настройки:

- `ZSCORE_HARD_BLOCK_ENABLED=true`
- `ZSCORE_HARD_BLOCK_ABS=3.0`

Что делает:

- если `abs(zscore) >= 3.0`, вход блокируется;
- zscore ниже hard threshold продолжает влиять на confidence как soft score.

Counterfactual на момент включения:

- всего проверено: 45 trades;
- would_block: 1;
- blocked_wins: 0;
- blocked_losses: 1;
- saved PnL: `$3.00`;
- конкретный кейс: current id 9, `abs_zscore=3.21`, `pnl=-3.00`.

Зачем:

- extreme zscore часто означал вход в растянутый импульс перед возвратом;
- hard block не трогал обычные wins по истории.

## 9. Trade Review Playbook

Status: `PROCESS`

Файл: `TRADE_REVIEW_PLAYBOOK.md`

Что зафиксировано:

- после каждой закрытой сделки перепроверять каждый фильтр;
- задавать вопросы:
  - что произошло, чего не должно было произойти?
  - что не произошло, но должно было произойти?
- перед новым фильтром считать counterfactual по archive + current journal;
- не объяснять loss одним параметром без проверки wins с такими же значениями.

## 10. Borderline Weak Main Guard, Initial Version

Status: `REPLACED_BY_REFINED_VERSION`

Commit: `aa3ccfe Add borderline weak main guard`

Первичная логика:

```text
first main blocked if:
side_ask <= 0.71
and delta_strong_ratio < 0.60
```

Причина:

- loss id 2 был `side_ask=0.71`, `delta_ratio=0.53`, `abs_zscore=1.55`, official `UP`, pnl `-3.00`;
- перед входом была серия слабых наблюдений, потом один тик до `0.71`, затем откат.

Counterfactual показал, что двухусловный фильтр слишком грубый:

- would_block: 2;
- blocked_wins: 1;
- blocked_losses: 1;
- blocked_pnl: `-1.86`.

Конкретные сделки:

- archive id 20: WIN, `side_ask=0.71`, `delta_ratio=0.53`, `abs_zscore=0.15`, pnl `+1.14`;
- current id 2: LOSS, `side_ask=0.71`, `delta_ratio=0.53`, `abs_zscore=1.55`, pnl `-3.00`.

Вывод:

- `side_ask` + `delta_ratio` сами по себе не отличали хороший кейс от плохого;
- нужен был третий признак `abs_zscore`.

## 11. Borderline Weak Main Guard, Refined With Zscore

Status: `ACTIVE`

Commit: `da19980 Refine borderline weak main guard with zscore`

Финальная текущая логика:

```text
first main blocked if:
side_ask <= BORDERLINE_WEAK_MAIN_MAX_SIDE_ASK
and delta_strong_ratio < 0.60
and abs_zscore >= 1.00
```

Текущий production threshold:

```text
BORDERLINE_WEAK_MAIN_MAX_SIDE_ASK=0.73
BORDERLINE_WEAK_MAIN_MIN_DELTA_STRONG_RATIO=0.60
BORDERLINE_WEAK_MAIN_MIN_ABS_ZSCORE=1.00
```

Этап 1, с `max_side_ask=0.71`:

- would_block: 1;
- blocked_wins: 0;
- blocked_losses: 1;
- blocked_pnl: `-3.00`;
- сохранял archive id 20, потому что `abs_zscore=0.15`.

Этап 2, расширение до `max_side_ask=0.73`:

Counterfactual:

- would_block: 2;
- blocked_wins: 0;
- blocked_losses: 2;
- blocked_pnl: `-5.96`.

Пойманные плохие типы:

- id 2: `0.71 / delta 0.53 / zscore 1.55`, LOSS `-3.00`;
- id 30: `0.73 / delta 0.46 / zscore 1.32`, LOSS `-2.96`.

Что изменило бы с начала текущей серии:

- фактический результат после 39 trades: `31W / 8L`, PnL `-2.02`;
- если бы guard `0.73` стоял с начала: убрал бы еще `$5.96` losses;
- расчетный PnL был бы около `+3.94`.

## 12. Broad Single-Tick Confirmation Guard

Status: `REJECTED`

Идея:

- блокировать входы, если перед entry не было подтвержденного предыдущего good tick;
- ждать устойчивость сигнала.

Counterfactual:

- would_block_single_tick_signal: 39 trades;
- blocked wins: 29;
- blocked losses: 10;
- bucket PnL: `-8.48`.

Вывод:

- слишком широкий фильтр;
- убивает много winners;
- не внедрять как общий guard.

Что оставили вместо него:

- узкий `borderline_weak_main_guard` только для пограничных first main входов.

## 13. UP Signal Analysis

Status: `RESEARCH_DONE_NO_ACTIVE_CHANGE`

Наблюдение:

- в текущей серии все реальные trades были `side=DOWN`;
- все losses имели `winning_side=UP`, потому что DOWN-позиция проиграла official UP outcome.

Проверка `signal_observations` показала:

- `DOWN enter=1 entry_signal_passed_all_filters`: 88 observations;
- `UP enter=1 entry_signal_passed_all_filters`: не найдено в выводе;
- UP observations чаще блокировались по `btc_move_below_soft`, `side_ask_0.000_below_0.71`, `side_ask_0.99_above_0.95_too_close_to_resolved`.

Вывод:

- бот не имеет ручного bias на DOWN;
- он выбирает сторону по `current_btc - window_open_btc`;
- в этот период качественные проходящие сигналы были DOWN;
- UP часто был либо слишком слабый по BTC move, либо уже слишком дорогой `0.98-1.00`, либо стакан UP был пустой/непокупаемый.

Решение:

- не включать отдельный soft-UP режим;
- не покупать UP по `0.98-1.00`, потому что payout почти нулевой;
- фокус перенести на DOWN reversal guard.

## 14. DOWN Reversal Diagnosis

Status: `RESEARCH_DIRECTION`

Наблюдение:

- losses происходят, когда бот покупает DOWN после падения BTC, но official outcome становится UP;
- это не проблема симметричного UP/DOWN выбора, а проблема `DOWN momentum -> rebound above open`.

Гипотеза:

- часть DOWN losses происходит на проколе вниз, который быстро откупается;
- для этого нужны candle/reversal признаки, а не только delta/ask/zscore.

Что искать:

- большой lower wick у DOWN;
- close последней 1m свечи против стороны;
- восстановление от low;
- серия закрытий обратно к open;
- маленькое расстояние от open line при дорогом DOWN;
- дорогие входы `0.84+`, где один loss съедает много small wins.

## 15. Candle Reversal Features

Status: `OBSERVATION_ONLY`

Commit: `b07cae3 Add observation-only candle reversal features`

Что добавлено в `features_json`:

- `distance_from_open_usd`
- `abs_distance_from_open_usd`
- `distance_from_open_to_atr`
- `last_1m_open`
- `last_1m_high`
- `last_1m_low`
- `last_1m_close`
- `last_1m_body_usd`
- `last_1m_range_usd`
- `last_1m_upper_wick_usd`
- `last_1m_lower_wick_usd`
- `last_1m_upper_wick_ratio`
- `last_1m_lower_wick_ratio`
- `last_1m_body_ratio`
- `last_1m_direction`
- `wick_against_side_ratio`
- `recovery_from_side_extreme_ratio`
- `last_1m_close_against_side`
- `last_1m_close_change_usd`
- `last_1m_close_change_against_side`
- `three_close_recovery_against_side`

Для DOWN:

- `wick_against_side_ratio = last_1m_lower_wick_ratio`;
- высокий lower wick означает риск откупа снизу;
- `last_1m_close_against_side=1` означает, что последняя 1m candle закрылась против DOWN.

Для UP:

- `wick_against_side_ratio = last_1m_upper_wick_ratio`;
- высокий upper wick означает риск продажи сверху.

Что НЕ сделано:

- эти поля не блокируют вход;
- active DOWN-reversal guard еще не включен.

Зачем:

- собрать данные на новых wins/losses;
- потом проверить, например:

```text
if side = DOWN
and wick_against_side_ratio >= X
and last_1m_close_against_side = 1
then сколько wins/losses было бы заблокировано?
```

## 16. Current New Bot Result Snapshot

Status: `OBSERVED`

По official reconciliation на момент анализа:

```text
trades_checked: 39
official_wins: 31
official_losses: 8
official_pnl_usd: -2.0176
avg_win: +0.70
avg_loss: -2.98
winrate: 79.5%
```

Математика:

```text
breakeven winrate ~= 2.98 / (2.98 + 0.70) = ~81%
```

Вывод:

- winrate высокий, но payout profile тяжелый;
- один loss съедает примерно 4 small wins;
- задача не добавить больше входов, а убрать small set losses без убийства winners;
- после `BORDERLINE_WEAK_MAIN_MAX_SIDE_ASK=0.73` последние наблюдавшиеся сделки `id 32-39` были all WIN, но выборка маленькая.

## 17. Expensive Entry Issue

Status: `RESEARCH_PENDING`

Наблюдение:

- много entries в зоне `0.84-0.90`;
- wins там маленькие;
- loss там почти полный.

Примеры losses:

- id 14: `side_ask=0.84`, delta `0.79`, zscore `0.94`, LOSS;
- id 19: `side_ask=0.86`, delta `0.94`, zscore `2.92`, LOSS.

Примеры wins:

- id 37: `0.90`, WIN;
- id 34: `0.85`, WIN;
- id 33: `0.84`, WIN;
- id 31: `0.88`, WIN;
- id 18: `0.87`, WIN;
- id 16: `0.89`, WIN;
- id 15: `0.89`, WIN.

Вывод:

- нельзя просто блокировать `0.84+`;
- нужен bucket-анализ expectancy по `side_ask` диапазонам;
- для дорогих входов нужен либо очень высокий winrate, либо дополнительный confirmation/reversal guard.

## 18. Rejected Or Not Yet Implemented Ideas

### Free Second Entry

Status: `REJECTED`

Причина:

- свободный второй вход может усилить losing side;
- id 2 показал, что рынок мог стать дорогим, но delta не подтвердилась;
- strict ADD_ON безопаснее.

### General Confirmation For Every Entry

Status: `REJECTED`

Причина:

- broad single-tick confirmation убивал слишком много wins.

### Lowering UP Thresholds

Status: `REJECTED_FOR_NOW`

Причина:

- пропущенные UP wins были в основном слишком дорогие `0.98-1.00`, слишком слабые по BTC move, либо без нормального ask;
- нет подтверждения, что мягкий UP режим дал бы хороший expected value.

### Active Wick/Reversal Guard

Status: `NOT_YET_IMPLEMENTED`

Причина:

- сначала нужны observation-only данные на новых сделках;
- активировать только после counterfactual.

## 19. Commands To Compare Current vs Baseline

Current bot since baseline start (`2026-05-19 11:50:07 UTC`):

```bash
cd /opt/edge-bot-official && \
sudo -u edgebot sqlite3 -readonly -cmd ".timeout 5000" -header -column runtime/journal.sqlite3 "
SELECT
  COUNT(*) AS trades,
  SUM(CASE WHEN exit_ts IS NULL THEN 1 ELSE 0 END) AS open,
  SUM(CASE WHEN exit_ts IS NOT NULL AND pnl_usd > 0 THEN 1 ELSE 0 END) AS wins,
  SUM(CASE WHEN exit_ts IS NOT NULL AND pnl_usd <= 0 THEN 1 ELSE 0 END) AS losses,
  ROUND(SUM(COALESCE(pnl_usd, 0)), 2) AS pnl
FROM trades
WHERE is_hedge = 0
  AND entry_ts >= strftime('%s','2026-05-19 11:50:07');
"
```

Baseline bot:

```bash
cd /opt/edge-bot-baseline-settlement && \
sudo -u edgebot sqlite3 -readonly -cmd ".timeout 5000" -header -column runtime/journal.sqlite3 "
SELECT
  COUNT(*) AS trades,
  SUM(CASE WHEN exit_ts IS NULL THEN 1 ELSE 0 END) AS open,
  SUM(CASE WHEN exit_ts IS NOT NULL AND pnl_usd > 0 THEN 1 ELSE 0 END) AS wins,
  SUM(CASE WHEN exit_ts IS NOT NULL AND pnl_usd <= 0 THEN 1 ELSE 0 END) AS losses,
  ROUND(SUM(COALESCE(pnl_usd, 0)), 2) AS pnl
FROM trades
WHERE is_hedge = 0;
"
```

## 20. Next Required Analysis After New Losses

Когда появятся новые сделки после candle reversal observation, надо запросить поля:

```sql
ROUND(CAST(json_extract(features_json, '$.wick_against_side_ratio') AS REAL), 2) AS wick_against_side,
ROUND(CAST(json_extract(features_json, '$.recovery_from_side_extreme_ratio') AS REAL), 2) AS recovery_from_extreme,
ROUND(CAST(json_extract(features_json, '$.last_1m_close_against_side') AS REAL), 0) AS close_against_side,
ROUND(CAST(json_extract(features_json, '$.last_1m_close_change_against_side') AS REAL), 0) AS close_change_against_side,
ROUND(CAST(json_extract(features_json, '$.three_close_recovery_against_side') AS REAL), 0) AS three_close_recovery,
ROUND(CAST(json_extract(features_json, '$.distance_from_open_to_atr') AS REAL), 2) AS dist_to_atr
```

Потом обязательно считать counterfactual для возможного DOWN-reversal guard, например:

```text
side = DOWN
wick_against_side_ratio >= 0.45
last_1m_close_against_side = 1
delta_ratio < 0.90
```

До counterfactual этот guard не включать.
