"""
PaperExecutionEngine — dry-run order simulation. This is the DEFAULT
execution path (Settings.paper_trading = True). It never talks to a
network; it fills the order at the current price plus a small simulated
slippage, logs the "receipt", and leaves the actual portfolio-ledger
update to `agent.py` (via `storage.apply_fill`), so this class stays a
pure order->fill transformer with no hidden state.
"""
from __future__ import annotations

import random

from ..logging_config import get_logger
from ..schemas import Order, OrderStatus
from .base import BaseExecutionEngine

log = get_logger(__name__)


class PaperExecutionEngine(BaseExecutionEngine):
    name = "paper"
    is_dry_run = True

    def __init__(self, simulated_slippage_pct: float = 0.001, max_slippage_pct: float = 0.01):
        self.simulated_slippage_pct = simulated_slippage_pct
        self.max_slippage_pct = max_slippage_pct
        self._rng = random.Random()

    def execute(self, order: Order, current_price: float | None = None) -> Order:
        """`current_price` defaults to order.limit_price for LIMIT orders,
        otherwise the caller must supply it (agent.py always does, from the
        connector's fetch_price)."""
        reference_price = current_price if current_price is not None else order.limit_price
        if reference_price is None or reference_price <= 0:
            order.status = OrderStatus.REJECTED
            order.reject_reason = "no reference price available to simulate a fill"
            log.warning("paper order %s rejected: %s", order.id, order.reject_reason)
            return order

        # Simulate a small, randomized adverse slippage — BUYs fill slightly
        # higher, SELLs slightly lower, capped by max_slippage_pct so a
        # simulated fill can never itself violate the tolerance the risk
        # manager already checked against.
        drift = min(self.simulated_slippage_pct, self.max_slippage_pct) * self._rng.uniform(0.0, 1.0)
        sign = 1 if order.side == "BUY" else -1
        fill_price = reference_price * (1 + sign * drift)

        order.status = OrderStatus.SIMULATED
        order.dry_run = True
        order.filled_price = round(fill_price, 8)
        order.filled_quantity = order.quantity
        order.slippage_pct = abs(fill_price - reference_price) / reference_price
        order.receipt = {
            "engine": self.name,
            "reference_price": reference_price,
            "fill_price": order.filled_price,
            "notional": round(order.filled_quantity * order.filled_price, 2),
            "note": "SIMULATED FILL — no real order was placed",
        }
        log.info(
            "paper fill: %s %s %.8g @ %.8g (notional $%.2f)",
            order.side, order.symbol, order.filled_quantity, order.filled_price,
            order.receipt["notional"],
        )
        return order
