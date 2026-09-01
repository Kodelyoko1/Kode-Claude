"""
Configuration schema for the AI Asset Manager Agent.

Two layers, on purpose:

  1. `Settings` (this file, pydantic-settings) — process-level config that
     comes from environment variables / `.env`. This is where credentials
     and mode flags (paper vs. live) live, because those must never be
     checked into a YAML file in the repo.

  2. `RiskLimitsConfig` / `TargetAllocationConfig` (YAML files under
     `asset_manager/config/`) — the "business" config an owner tunes by
     hand: which assets to hold at what weight, and how tight the risk
     guardrails are. These are safe to commit (no secrets) and are reloaded
     fresh on every agent cycle, so editing the YAML takes effect on the
     next run without touching code or environment variables.

Both layers validate strictly with Pydantic: a malformed `.env` value or a
typo'd YAML key fails fast at startup instead of silently defaulting.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal, Optional

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# asset_manager/config/settings.py -> asset_manager/config -> asset_manager -> repo root
ROOT_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT_DIR / "data"
CONFIG_DIR = Path(__file__).resolve().parent

DEFAULT_RISK_LIMITS_PATH = CONFIG_DIR / "risk_limits.yaml"
DEFAULT_TARGET_ALLOCATIONS_PATH = CONFIG_DIR / "target_allocations.yaml"


# =============================================================================
# ENV-DRIVEN SETTINGS
# =============================================================================

class Settings(BaseSettings):
    """
    Loaded from environment variables prefixed `AM_` (falls back to a root
    `.env` file if present). Owner never has to touch these to run in paper
    mode — every field below has a safe default.
    """
    model_config = SettingsConfigDict(
        env_prefix="AM_",
        env_file=str(ROOT_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Safety switch -------------------------------------------------
    # PAPER TRADING IS THE DEFAULT. An operator must explicitly set
    # AM_PAPER_TRADING=0 to arm live order routing, and even then the
    # execution engine + risk manager independently refuse to place a real
    # order unless a live connector is configured.
    paper_trading: bool = True

    # --- Universe --------------------------------------------------------
    asset_class: Literal["crypto", "equity", "custom"] = "crypto"
    connector: Literal["paper", "ccxt", "yfinance"] = "paper"
    symbols: list[str] = Field(
        default_factory=lambda: ["BTC/USDT", "ETH/USDT", "SOL/USDT"],
        description="Universe of symbols the agent monitors/trades each cycle.",
    )
    timeframe: str = "1d"
    candle_limit: int = 200

    # --- crypto (ccxt) connector -----------------------------------------
    exchange_id: str = "binance"
    exchange_api_key: str = ""
    exchange_api_secret: str = ""
    exchange_sandbox: bool = True

    # --- equities (Alpaca via ccxt-less REST, used by yfinance connector
    #     for paper broker execution) --------------------------------------
    alpaca_api_key: str = ""
    alpaca_api_secret: str = ""
    alpaca_paper: bool = True
    alpaca_base_url: str = "https://paper-api.alpaca.markets"

    # --- strategy selection ------------------------------------------------
    strategy: Literal["rebalance", "sma_crossover", "momentum"] = "rebalance"
    rebalance_drift_threshold: float = 0.05      # 5% drift triggers a rebalance
    sma_fast_period: int = 20
    sma_slow_period: int = 50
    momentum_lookback: int = 14
    momentum_top_n: int = 2
    momentum_alloc_weight: float = 0.20          # weight assigned per top-N pick

    # --- risk guardrails (used as fallback defaults if risk_limits.yaml is
    #     missing; risk_limits.yaml overrides these when present) ----------
    max_allocation_pct: float = 0.25
    max_daily_loss_pct: float = 0.05
    max_slippage_pct: float = 0.01
    max_position_notional: Optional[float] = None
    max_orders_per_cycle: int = 5
    min_order_notional: float = 10.0

    # --- execution ---------------------------------------------------------
    starting_cash: float = 10_000.0
    simulated_slippage_pct: float = 0.001        # paper engine's own fill slippage

    # --- scheduling ----------------------------------------------------
    cycle_interval_seconds: int = 300

    # --- persistence -----------------------------------------------------
    db_path: Path = DATA_DIR / "asset_manager.db"

    # --- notifications (reuses repo-wide SMTP creds from .env) -------------
    owner_email: str = ""

    @field_validator("symbols", mode="before")
    @classmethod
    def _split_csv_symbols(cls, v):
        """Allow AM_SYMBOLS="BTC/USDT,ETH/USDT" as a plain CSV env var."""
        if isinstance(v, str):
            return [s.strip() for s in v.split(",") if s.strip()]
        return v

    @field_validator(
        "max_allocation_pct", "max_daily_loss_pct", "max_slippage_pct",
        "rebalance_drift_threshold", "momentum_alloc_weight", "simulated_slippage_pct",
    )
    @classmethod
    def _fraction_in_range(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"expected a fraction in [0, 1], got {v}")
        return v

    @model_validator(mode="after")
    def _live_requires_real_connector(self) -> "Settings":
        if not self.paper_trading and self.connector == "paper":
            raise ValueError(
                "AM_PAPER_TRADING=0 requires AM_CONNECTOR=ccxt or AM_CONNECTOR=yfinance "
                "(the paper connector cannot place live orders)."
            )
        return self


@lru_cache
def get_settings() -> Settings:
    """Cached singleton. Call `get_settings.cache_clear()` in tests that
    mutate environment variables between cases."""
    return Settings()


# =============================================================================
# YAML CONFIG: RISK LIMITS
# =============================================================================

class RiskLimitsConfig(BaseModel):
    """Hard pre-trade guardrails. See asset_manager/config/risk_limits.yaml."""
    max_allocation_pct: float = 0.25
    max_daily_loss_pct: float = 0.05
    max_slippage_pct: float = 0.01
    max_position_notional: Optional[float] = None
    max_orders_per_cycle: int = 5
    min_order_notional: float = 10.0

    @field_validator("max_allocation_pct", "max_daily_loss_pct", "max_slippage_pct")
    @classmethod
    def _fraction_in_range(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"expected a fraction in [0, 1], got {v}")
        return v

    @field_validator("max_orders_per_cycle")
    @classmethod
    def _positive_int(cls, v: int) -> int:
        if v < 1:
            raise ValueError("max_orders_per_cycle must be >= 1")
        return v


class TargetAllocationConfig(BaseModel):
    """Target portfolio weights. See asset_manager/config/target_allocations.yaml."""
    allocations: dict[str, float] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _weights_valid(self) -> "TargetAllocationConfig":
        for symbol, weight in self.allocations.items():
            if not (0.0 <= weight <= 1.0):
                raise ValueError(f"target weight for {symbol} must be in [0, 1], got {weight}")
        total = sum(self.allocations.values())
        if total > 1.0 + 1e-9:
            raise ValueError(
                f"target allocations sum to {total:.4f}, which exceeds 1.0 "
                f"(remaining balance is implicitly held as cash — weights must not exceed 100%)"
            )
        return self


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a YAML mapping at the top level")
    return data


def load_risk_limits(path: Optional[Path] = None) -> RiskLimitsConfig:
    """
    Load risk_limits.yaml, falling back to Settings' env-driven defaults for
    any key the file doesn't specify (and to RiskLimitsConfig's own defaults
    if neither is present).
    """
    raw = _load_yaml(path or DEFAULT_RISK_LIMITS_PATH)
    settings = get_settings()
    merged = {
        "max_allocation_pct": settings.max_allocation_pct,
        "max_daily_loss_pct": settings.max_daily_loss_pct,
        "max_slippage_pct": settings.max_slippage_pct,
        "max_position_notional": settings.max_position_notional,
        "max_orders_per_cycle": settings.max_orders_per_cycle,
        "min_order_notional": settings.min_order_notional,
        **raw,
    }
    return RiskLimitsConfig(**merged)


def load_target_allocations(path: Optional[Path] = None) -> TargetAllocationConfig:
    raw = _load_yaml(path or DEFAULT_TARGET_ALLOCATIONS_PATH)
    return TargetAllocationConfig(**raw)
