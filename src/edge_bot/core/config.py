"""Application configuration loaded from environment / .env."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _env(name: str, default: str) -> str:
    val = os.getenv(name)
    return val if val is not None and val != "" else default


def _envf(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except (TypeError, ValueError):
        return default


def _envi(name: str, default: int) -> int:
    try:
        return int(float(_env(name, str(default))))
    except (TypeError, ValueError):
        return default


def _envb(name: str, default: bool) -> bool:
    raw = _env(name, "1" if default else "0").strip().lower()
    return raw in {"1", "true", "yes", "on", "y"}


def _env_float_tuple(name: str, default: tuple[float, ...]) -> tuple[float, ...]:
    raw = _env(name, ",".join(str(x) for x in default))
    vals: list[float] = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            vals.append(float(item))
        except ValueError:
            continue
    return tuple(vals) if vals else default


@dataclass(slots=True)
class PolymarketAuth:
    private_key: str = ""
    funder: str = ""
    signature_type: int = 2
    api_key: str = ""
    api_secret: str = ""
    api_passphrase: str = ""

    @property
    def has_full_creds(self) -> bool:
        return bool(self.private_key and self.api_key and self.api_secret and self.api_passphrase)

    @property
    def can_sign(self) -> bool:
        return bool(self.private_key)


@dataclass(slots=True)
class DataConfig:
    primary_price_source: str = "coinbase"
    fallback_sources: tuple[str, ...] = ("binance",)
    coinbase_ws_url: str = "wss://ws-feed.exchange.coinbase.com"
    binance_ws_url: str = "wss://stream.binance.com:9443/stream"
    price_staleness_max_sec: float = 4.0
    gamma_base_url: str = "https://gamma-api.polymarket.com"
    clob_base_url: str = "https://clob.polymarket.com"
    clob_ws_url: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    clob_ws_enabled: bool = False
    required_resolution_source: str = "chainlink"


@dataclass(slots=True)
class SignalConfig:
    window_delta_usd_min: float = 70.0
    window_delta_usd_soft_min: float = 55.0
    window_delta_usd_strong: float = 120.0
    adaptive_delta_enabled: bool = True
    adaptive_delta_atr_mult: float = 0.35
    adaptive_delta_hard_min: float = 65.0
    adaptive_delta_hard_max: float = 100.0
    adaptive_delta_soft_ratio: float = 0.82
    entry_seconds_left_max: float = 120.0
    entry_seconds_left_min: float = 10.0
    clob_ask_min: float = 0.68
    soft_delta_clob_ask_min: float = 0.70
    clob_ask_max: float = 0.95
    min_confidence: float = 0.62
    zscore_max: float = 2.5
    zscore_hard_block_enabled: bool = True
    zscore_hard_block_abs: float = 3.0
    atr_score_min_pct: float = 0.06
    atr_score_max_pct: float = 0.22
    atr_score_high_pct: float = 0.35
    atr_hard_block_enabled: bool = True
    atr_hard_block_pct: float = 0.30
    high_vol_edge_guard_enabled: bool = True
    high_vol_edge_guard_atr_pct: float = 0.22
    high_vol_min_delta_strong_ratio: float = 0.80
    high_vol_min_abs_zscore: float = 0.50
    borderline_weak_main_guard_enabled: bool = True
    borderline_weak_main_max_side_ask: float = 0.71
    borderline_weak_main_min_delta_strong_ratio: float = 0.60
    rsi_max: float = 78.0
    rsi_min: float = 22.0
    atr_overheat_mult: float = 1.6
    spread_max: float = 0.03
    top_ask_notional_usd_min: float = 20.0
    consensus_guard_min: float = 0.35


@dataclass(slots=True)
class RiskConfig:
    start_equity_usd: float = 100.0
    max_position_pct: float = 3.0
    kelly_fraction: float = 0.35
    min_position_usd: float = 1.0
    max_position_usd: float = 15.0
    min_probability_edge: float = 0.02
    allow_multiple_entries_per_market: bool = True
    max_entries_per_market: int = 3
    max_market_exposure_pct: float = 7.5
    min_reentry_delay_sec: float = 15.0
    addon_enabled: bool = False
    addon_size_usd: float = 3.0
    addon_min_seconds_left: float = 45.0
    addon_min_side_ask: float = 0.82
    addon_min_delta_strong_ratio: float = 1.0
    addon_min_abs_zscore: float = 1.0
    addon_max_spread: float = 0.02
    daily_loss_cap_pct: float = 5.0
    max_consecutive_losses: int = 3
    cooldown_after_losses_sec: float = 900.0
    max_trades_per_hour: int = 8
    max_trades_per_day: int = 60
    min_balance_usd: float = 30.0


@dataclass(slots=True)
class HedgeConfig:
    enabled: bool = True
    skew_trigger: float = 0.95
    seconds_left_max: float = 30.0
    min_main_notional_usd: float = 25.0
    once_per_market: bool = True
    notional_pct_of_main: float = 3.0
    notional_usd_min: float = 1.0
    notional_usd_max: float = 2.0


@dataclass(slots=True)
class FeeConfig:
    paper_taker_fees_enabled: bool = True
    paper_taker_fee_rate: float = 0.07


@dataclass(slots=True)
class ExitConfig:
    stop_loss_pct: float = 0.25
    take_profit_pct: float = 0.20
    trail_arm_pct: float = 0.12
    trail_giveback_pct: float = 0.06
    exit_before_sec: float = 20.0
    paper_settle_on_expiry: bool = True
    close_retry_max: int = 10
    close_retry_delay_sec: float = 2.0


@dataclass(slots=True)
class LiveReadinessConfig:
    fill_sim_targets_usd: tuple[float, ...] = (10.0, 25.0, 50.0, 100.0)
    orderbook_snapshot_levels: int = 10
    max_fill_slippage: float = 0.02
    min_fill_ratio: float = 0.50
    max_avg_fill_price: float = 0.92


@dataclass(slots=True)
class TelegramConfig:
    enabled: bool = False
    bot_token: str = ""
    chat_id: str = ""
    report_interval_sec: float = 900.0
    send_on_start: bool = True
    timeout_sec: float = 10.0


@dataclass(slots=True)
class StorageConfig:
    runtime_dir: Path = field(default_factory=lambda: Path("runtime"))
    journal_db: Path = field(default_factory=lambda: Path("runtime/journal.sqlite3"))
    audit_log: Path = field(default_factory=lambda: Path("runtime/audit.jsonl"))
    dashboard_path: Path = field(default_factory=lambda: Path("runtime/dashboard.txt"))


@dataclass(slots=True)
class OperationsConfig:
    loop_poll_sec: float = 1.0
    heartbeat_interval_sec: float = 5.0
    health_port: int = 8080
    dashboard_refresh_sec: float = 5.0
    log_level: str = "INFO"


@dataclass(slots=True)
class AppConfig:
    mode: str = "paper"
    live_trading_ack: str = ""
    symbol: str = "BTC"
    market_kind: str = "btc-updown-5m"
    auth: PolymarketAuth = field(default_factory=PolymarketAuth)
    data: DataConfig = field(default_factory=DataConfig)
    signal: SignalConfig = field(default_factory=SignalConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    hedge: HedgeConfig = field(default_factory=HedgeConfig)
    fees: FeeConfig = field(default_factory=FeeConfig)
    live_readiness: LiveReadinessConfig = field(default_factory=LiveReadinessConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    exit_: ExitConfig = field(default_factory=ExitConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    ops: OperationsConfig = field(default_factory=OperationsConfig)


def load_config() -> AppConfig:
    fb_raw = _env("FALLBACK_PRICE_SOURCES", "binance")
    fb_sources = tuple(s.strip() for s in fb_raw.split(",") if s.strip())

    runtime_dir = Path(_env("RUNTIME_DIR", "runtime"))
    journal_db = Path(_env("JOURNAL_DB", str(runtime_dir / "journal.sqlite3")))
    audit_log = Path(_env("AUDIT_LOG", str(runtime_dir / "audit.jsonl")))
    dashboard_path = Path(_env("DASHBOARD_PATH", str(runtime_dir / "dashboard.txt")))

    cfg = AppConfig(
        mode=_env("EDGE_MODE", "paper").lower(),
        live_trading_ack=_env("LIVE_TRADING_ACK", ""),
        symbol=_env("SYMBOL", "BTC").upper(),
        market_kind=_env("MARKET_KIND", "btc-updown-5m"),
        auth=PolymarketAuth(
            private_key=_env("PM_PRIVATE_KEY", ""),
            funder=_env("PM_FUNDER", _env("PM_ADDRESS", "")),
            signature_type=_envi("PM_SIGNATURE_TYPE", 2),
            api_key=_env("PM_API_KEY", ""),
            api_secret=_env("PM_API_SECRET", ""),
            api_passphrase=_env("PM_API_PASSPHRASE", ""),
        ),
        data=DataConfig(
            primary_price_source=_env("PRIMARY_PRICE_SOURCE", "coinbase"),
            fallback_sources=fb_sources,
            coinbase_ws_url=_env("COINBASE_WS_URL", "wss://ws-feed.exchange.coinbase.com"),
            binance_ws_url=_env("BINANCE_WS_URL", "wss://stream.binance.com:9443/stream"),
            price_staleness_max_sec=_envf("PRICE_STALENESS_MAX_SEC", 4.0),
            gamma_base_url=_env("GAMMA_BASE_URL", "https://gamma-api.polymarket.com"),
            clob_base_url=_env("PM_CLOB_BASE", "https://clob.polymarket.com"),
            clob_ws_url=_env("PM_CLOB_WS_URL", "wss://ws-subscriptions-clob.polymarket.com/ws/market"),
            clob_ws_enabled=_envb("PM_CLOB_WS_ENABLED", False),
            required_resolution_source=_env("REQUIRED_RESOLUTION_SOURCE", "chainlink").lower(),
        ),
        signal=SignalConfig(
            window_delta_usd_min=_envf("WINDOW_DELTA_USD_MIN", 70.0),
            window_delta_usd_soft_min=_envf("WINDOW_DELTA_USD_SOFT_MIN", 55.0),
            window_delta_usd_strong=_envf("WINDOW_DELTA_USD_STRONG", 120.0),
            adaptive_delta_enabled=_envb("ADAPTIVE_DELTA_ENABLED", True),
            adaptive_delta_atr_mult=_envf("ADAPTIVE_DELTA_ATR_MULT", 0.35),
            adaptive_delta_hard_min=_envf("ADAPTIVE_DELTA_HARD_MIN", 65.0),
            adaptive_delta_hard_max=_envf("ADAPTIVE_DELTA_HARD_MAX", 100.0),
            adaptive_delta_soft_ratio=_envf("ADAPTIVE_DELTA_SOFT_RATIO", 0.82),
            entry_seconds_left_max=_envf("ENTRY_SECONDS_LEFT_MAX", 120.0),
            entry_seconds_left_min=_envf("ENTRY_SECONDS_LEFT_MIN", 10.0),
            clob_ask_min=_envf("CLOB_ASK_MIN", 0.68),
            soft_delta_clob_ask_min=_envf("SOFT_DELTA_CLOB_ASK_MIN", 0.70),
            clob_ask_max=_envf("CLOB_ASK_MAX", 0.95),
            min_confidence=_envf("MIN_CONFIDENCE", 0.62),
            zscore_max=_envf("ZSCORE_MAX", 2.5),
            zscore_hard_block_enabled=_envb("ZSCORE_HARD_BLOCK_ENABLED", True),
            zscore_hard_block_abs=_envf("ZSCORE_HARD_BLOCK_ABS", 3.0),
            atr_score_min_pct=_envf("ATR_SCORE_MIN_PCT", 0.06),
            atr_score_max_pct=_envf("ATR_SCORE_MAX_PCT", 0.22),
            atr_score_high_pct=_envf("ATR_SCORE_HIGH_PCT", 0.35),
            atr_hard_block_enabled=_envb("ATR_HARD_BLOCK_ENABLED", True),
            atr_hard_block_pct=_envf("ATR_HARD_BLOCK_PCT", 0.30),
            high_vol_edge_guard_enabled=_envb("HIGH_VOL_EDGE_GUARD_ENABLED", True),
            high_vol_edge_guard_atr_pct=_envf("HIGH_VOL_EDGE_GUARD_ATR_PCT", 0.22),
            high_vol_min_delta_strong_ratio=_envf("HIGH_VOL_MIN_DELTA_STRONG_RATIO", 0.80),
            high_vol_min_abs_zscore=_envf("HIGH_VOL_MIN_ABS_ZSCORE", 0.50),
            borderline_weak_main_guard_enabled=_envb("BORDERLINE_WEAK_MAIN_GUARD_ENABLED", True),
            borderline_weak_main_max_side_ask=_envf("BORDERLINE_WEAK_MAIN_MAX_SIDE_ASK", 0.71),
            borderline_weak_main_min_delta_strong_ratio=_envf(
                "BORDERLINE_WEAK_MAIN_MIN_DELTA_STRONG_RATIO",
                0.60,
            ),
            rsi_max=_envf("RSI_MAX", 78.0),
            rsi_min=_envf("RSI_MIN", 22.0),
            atr_overheat_mult=_envf("ATR_OVERHEAT_MULT", 1.6),
            spread_max=_envf("SPREAD_MAX", 0.03),
            top_ask_notional_usd_min=_envf("TOP_ASK_NOTIONAL_USD_MIN", 20.0),
            consensus_guard_min=_envf("CONSENSUS_GUARD_MIN", 0.35),
        ),
        risk=RiskConfig(
            start_equity_usd=_envf("START_EQUITY_USD", 100.0),
            max_position_pct=_envf("MAX_POSITION_PCT", 3.0),
            kelly_fraction=_envf("KELLY_FRACTION", 0.35),
            min_position_usd=_envf("MIN_POSITION_USD", 1.0),
            max_position_usd=_envf("MAX_POSITION_USD", 15.0),
            min_probability_edge=_envf("MIN_PROBABILITY_EDGE", 0.02),
            allow_multiple_entries_per_market=_envb("ALLOW_MULTIPLE_ENTRIES_PER_MARKET", True),
            max_entries_per_market=_envi("MAX_ENTRIES_PER_MARKET", 3),
            max_market_exposure_pct=_envf("MAX_MARKET_EXPOSURE_PCT", 7.5),
            min_reentry_delay_sec=_envf("MIN_REENTRY_DELAY_SEC", 15.0),
            addon_enabled=_envb("ADD_ON_ENABLED", False),
            addon_size_usd=_envf("ADD_ON_SIZE_USD", 3.0),
            addon_min_seconds_left=_envf("ADD_ON_MIN_SECONDS_LEFT", 45.0),
            addon_min_side_ask=_envf("ADD_ON_MIN_SIDE_ASK", 0.82),
            addon_min_delta_strong_ratio=_envf("ADD_ON_MIN_DELTA_STRONG_RATIO", 1.0),
            addon_min_abs_zscore=_envf("ADD_ON_MIN_ABS_ZSCORE", 1.0),
            addon_max_spread=_envf("ADD_ON_MAX_SPREAD", 0.02),
            daily_loss_cap_pct=_envf("DAILY_LOSS_CAP_PCT", 5.0),
            max_consecutive_losses=_envi("MAX_CONSECUTIVE_LOSSES", 3),
            cooldown_after_losses_sec=_envf("COOLDOWN_AFTER_LOSSES_SEC", 900.0),
            max_trades_per_hour=_envi("MAX_TRADES_PER_HOUR", 8),
            max_trades_per_day=_envi("MAX_TRADES_PER_DAY", 60),
            min_balance_usd=_envf("MIN_BALANCE_USD", 30.0),
        ),
        telegram=TelegramConfig(
            enabled=_envb("TELEGRAM_ENABLED", False),
            bot_token=_env("TELEGRAM_BOT_TOKEN", ""),
            chat_id=_env("TELEGRAM_CHAT_ID", ""),
            report_interval_sec=_envf("TELEGRAM_REPORT_INTERVAL_SEC", 900.0),
            send_on_start=_envb("TELEGRAM_SEND_ON_START", True),
            timeout_sec=_envf("TELEGRAM_TIMEOUT_SEC", 10.0),
        ),
        hedge=HedgeConfig(
            enabled=_envb("HEDGE_ENABLED", True),
            skew_trigger=_envf("HEDGE_SKEW_TRIGGER", 0.95),
            seconds_left_max=_envf("HEDGE_SECONDS_LEFT_MAX", 30.0),
            min_main_notional_usd=_envf("HEDGE_MIN_MAIN_NOTIONAL_USD", 25.0),
            once_per_market=_envb("HEDGE_ONCE_PER_MARKET", True),
            notional_pct_of_main=_envf("HEDGE_NOTIONAL_PCT_OF_MAIN", 3.0),
            notional_usd_min=_envf("HEDGE_NOTIONAL_USD_MIN", 1.0),
            notional_usd_max=_envf("HEDGE_NOTIONAL_USD_MAX", 2.0),
        ),
        fees=FeeConfig(
            paper_taker_fees_enabled=_envb("PAPER_TAKER_FEES_ENABLED", True),
            paper_taker_fee_rate=_envf("PAPER_TAKER_FEE_RATE", 0.07),
        ),
        live_readiness=LiveReadinessConfig(
            fill_sim_targets_usd=_env_float_tuple("FILL_SIM_TARGETS_USD", (10.0, 25.0, 50.0, 100.0)),
            orderbook_snapshot_levels=max(1, _envi("ORDERBOOK_SNAPSHOT_LEVELS", 10)),
            max_fill_slippage=_envf("LIVE_MAX_FILL_SLIPPAGE", 0.02),
            min_fill_ratio=_envf("LIVE_MIN_FILL_RATIO", 0.50),
            max_avg_fill_price=_envf("LIVE_MAX_AVG_FILL_PRICE", 0.92),
        ),
        exit_=ExitConfig(
            stop_loss_pct=_envf("STOP_LOSS_PCT", 0.25),
            take_profit_pct=_envf("TAKE_PROFIT_PCT", 0.20),
            trail_arm_pct=_envf("TRAIL_ARM_PCT", 0.12),
            trail_giveback_pct=_envf("TRAIL_GIVEBACK_PCT", 0.06),
            exit_before_sec=_envf("EXIT_BEFORE_SEC", 20.0),
            paper_settle_on_expiry=_envb("PAPER_SETTLE_ON_EXPIRY", True),
            close_retry_max=_envi("CLOSE_RETRY_MAX", 10),
            close_retry_delay_sec=_envf("CLOSE_RETRY_DELAY_SEC", 2.0),
        ),
        storage=StorageConfig(
            runtime_dir=runtime_dir,
            journal_db=journal_db,
            audit_log=audit_log,
            dashboard_path=dashboard_path,
        ),
        ops=OperationsConfig(
            loop_poll_sec=_envf("LOOP_POLL_SEC", 1.0),
            heartbeat_interval_sec=_envf("HEARTBEAT_INTERVAL_SEC", 5.0),
            health_port=_envi("HEALTH_PORT", 8080),
            dashboard_refresh_sec=_envf("DASHBOARD_REFRESH_SEC", 5.0),
            log_level=_env("LOG_LEVEL", "INFO").upper(),
        ),
    )
    cfg.storage.runtime_dir.mkdir(parents=True, exist_ok=True)
    return cfg
