"""
Shared Pydantic data contracts for the AI Asset Manager Agent.

Every module in this package (connectors, strategies, risk, execution,
storage, agent) speaks these types instead of passing raw dicts around, so a
malformed signal or order is rejected at construction time rather than
surfacing as a KeyError three modules downstream.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# =============================================================================
# ENUMS
# =============================================================================

class AssetClass(str, Enum):
    CRYPTO = "crypto"
    EQUITY = "equity"
    CUSTOM = "custom"


class ActionType(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"


class OrderStatus(str, Enum):
    PENDING = "pending"          # created, not yet sent to an execution engine
    SIMULATED = "simulated"      # filled by the paper engine
    SUBMITTED = "submitted"      # sent to a live broker/exchange, awaiting fill
    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"
    REJECTED = "rejected"        # rejected by risk manager or broker
    CANCELLED = "cancelled"
    ERROR = "error"


# =============================================================================
# MARKET DATA
# =============================================================================

class Candle(BaseModel):
    """One OHLCV bar."""
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    @model_validator(mode="after")
    def _check_ohlc_sane(self) -> "Candle":
        if self.high < self.low:
            raise ValueError(f"candle high ({self.high}) < low ({self.low})")
        return self


# =============================================================================
# PORTFOLIO
# =============================================================================

class Position(BaseModel):
    symbol: str
    quantity: float = 0.0
    avg_entry_price: float = 0.0
    current_price: float = 0.0

    @property
    def market_value(self) -> float:
        return self.quantity * self.current_price

    @property
    def cost_basis(self) -> float:
        return self.quantity * self.avg_entry_price

    @property
    def unrealized_pnl(self) -> float:
        return self.market_value - self.cost_basis

    @property
    def unrealized_pnl_pct(self) -> float:
        if self.cost_basis == 0:
            return 0.0
        return self.unrealized_pnl / abs(self.cost_basis)


class PortfolioState(BaseModel):
    cash: float
    positions: dict[str, Position] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=_utcnow)
    # Equity at the start of the current trading day, used by the risk
    # manager's daily-loss guardrail. Set by whoever persists/loads state.
    day_start_equity: Optional[float] = None

    @property
    def total_equity(self) -> float:
        return self.cash + sum(p.market_value for p in self.positions.values())

    @property
    def total_market_value(self) -> float:
        return sum(p.market_value for p in self.positions.values())

    def weight_of(self, symbol: str) -> float:
        """Fraction of total equity currently allocated to `symbol` (0..1+)."""
        equity = self.total_equity
        if equity <= 0:
            return 0.0
        pos = self.positions.get(symbol)
        if pos is None:
            return 0.0
        return pos.market_value / equity

    def daily_pnl(self) -> Optional[float]:
        if self.day_start_equity is None:
            return None
        return self.total_equity - self.day_start_equity

    def daily_pnl_pct(self) -> Optional[float]:
        if not self.day_start_equity:
            return None
        return (self.total_equity - self.day_start_equity) / self.day_start_equity


# =============================================================================
# STRATEGY SIGNALS
# =============================================================================

class Signal(BaseModel):
    """
    Standardized output every strategy must produce. `target_weight` is used
    by weight-based strategies (e.g. rebalancing); `size` is used by
    fixed-size tactical strategies (e.g. SMA crossover, momentum). A signal
    may set either, both, or neither (HOLD signals typically set neither).
    """
    symbol: str
    action: ActionType
    target_weight: Optional[float] = None   # 0.0 - 1.0 fraction of total equity
    size: Optional[float] = None            # absolute quantity of the asset
    confidence: float = 1.0                 # 0.0 - 1.0
    rationale: str = ""
    strategy: str = ""
    generated_at: datetime = Field(default_factory=_utcnow)

    @field_validator("target_weight")
    @classmethod
    def _weight_in_range(cls, v: Optional[float]) -> Optional[float]:
        if v is not None and not (0.0 <= v <= 1.0):
            raise ValueError(f"target_weight must be within [0, 1], got {v}")
        return v

    @field_validator("confidence")
    @classmethod
    def _confidence_in_range(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"confidence must be within [0, 1], got {v}")
        return v

    @field_validator("size")
    @classmethod
    def _size_non_negative(cls, v: Optional[float]) -> Optional[float]:
        if v is not None and v < 0:
            raise ValueError(f"size must be >= 0, got {v}")
        return v


# =============================================================================
# RISK
# =============================================================================

class RiskCheckResult(BaseModel):
    approved: bool
    reasons: list[str] = Field(default_factory=list)
    # If the risk manager trims (rather than outright rejects) an order, the
    # execution engine should use this size instead of the signal's raw size.
    adjusted_quantity: Optional[float] = None


# =============================================================================
# ORDERS
# =============================================================================

class Order(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    symbol: str
    side: ActionType
    order_type: OrderType = OrderType.MARKET
    quantity: float
    limit_price: Optional[float] = None
    status: OrderStatus = OrderStatus.PENDING
    created_at: datetime = Field(default_factory=_utcnow)

    # Filled once an execution engine processes the order
    filled_price: Optional[float] = None
    filled_quantity: float = 0.0
    slippage_pct: Optional[float] = None
    dry_run: bool = True
    reject_reason: Optional[str] = None
    strategy: str = ""
    receipt: dict = Field(default_factory=dict)

    @field_validator("quantity")
    @classmethod
    def _quantity_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError(f"order quantity must be > 0, got {v}")
        return v

    @field_validator("side")
    @classmethod
    def _side_not_hold(cls, v: ActionType) -> ActionType:
        if v == ActionType.HOLD:
            raise ValueError("an Order cannot have side=HOLD (HOLD signals never become orders)")
        return v
