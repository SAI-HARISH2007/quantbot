"""Configuration system: YAML files + environment overrides, fully typed.

Nothing in the platform hardcodes tunables — everything routes through here.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class PolymarketConfig(BaseModel):
    gamma_url: str = "https://gamma-api.polymarket.com"
    clob_url: str = "https://clob.polymarket.com"
    ws_url: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    request_timeout: float = 15.0
    rate_limit_rps: float = 5.0


class CryptoConfig(BaseModel):
    binance_url: str = "https://api.binance.com"
    coinbase_url: str = "https://api.exchange.coinbase.com"
    binance_ws_url: str = "wss://stream.binance.com:9443/ws"
    symbols: list[str] = Field(default_factory=lambda: ["BTCUSDT", "ETHUSDT"])
    request_timeout: float = 15.0


class StorageConfig(BaseModel):
    root: Path = Path("data")
    db_file: str = "quantbot.db"

    @property
    def db_path(self) -> Path:
        return self.root / self.db_file


class CostConfig(BaseModel):
    """Transaction cost model. Polymarket currently charges no maker/taker
    fee on most markets, but this must stay configurable — verify per market."""

    taker_fee_bps: float = 0.0
    maker_fee_bps: float = 0.0
    # Extra slippage beyond walking the book, as a fraction of price (paranoia buffer).
    extra_slippage: float = 0.001
    # Fixed gas/relayer cost per order in USDC (0 when using Polymarket's relayer).
    per_order_cost: float = 0.0


class RiskConfig(BaseModel):
    initial_capital: float = 10_000.0
    max_position_per_market: float = 500.0  # USDC notional
    max_total_exposure: float = 5_000.0  # USDC notional
    max_drawdown_pct: float = 0.15  # kill switch
    kelly_fraction: float = 0.25  # fractional Kelly
    max_kelly_stake_pct: float = 0.05  # hard cap per trade as % of equity
    min_edge: float = 0.02  # ignore signals with < 2c expected edge
    min_order_notional: float = 5.0


class BacktestConfig(BaseModel):
    fill_at: str = "cross"  # 'cross' = pay the spread; 'mid' = optimistic
    latency_ms: float = 500.0


class StrategyConfig(BaseModel):
    name: str
    enabled: bool = True
    params: dict = Field(default_factory=dict)


class AppConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="QUANTBOT_", env_nested_delimiter="__")

    polymarket: PolymarketConfig = PolymarketConfig()
    crypto: CryptoConfig = CryptoConfig()
    storage: StorageConfig = StorageConfig()
    costs: CostConfig = CostConfig()
    risk: RiskConfig = RiskConfig()
    backtest: BacktestConfig = BacktestConfig()
    strategies: list[StrategyConfig] = Field(default_factory=list)
    log_level: str = "INFO"


def load_config(path: Optional[Path] = None) -> AppConfig:
    """Load YAML config (if given/found) merged with env-var overrides."""
    data: dict = {}
    candidates = [path] if path else [Path("configs/default.yaml")]
    for p in candidates:
        if p and p.exists():
            with open(p) as f:
                data = yaml.safe_load(f) or {}
            break
    return AppConfig(**data)
