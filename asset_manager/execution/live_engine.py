"""
LiveExecutionEngine — routes an already-risk-approved Order to a real
connector's place_order(). This class is only ever constructed by
`execution.get_execution_engine()`, and that factory only returns it when
BOTH `Settings.paper_trading` is explicitly False AND the connector
reports `is_paper_only = False`. There is no code path that reaches a real
exchange/broker by accident.

Every fill (or rejection) is logged with the same structure as the paper
engine so a human can diff simulated vs. live behavior from the logs
alone.
"""
from __future__ import annotations

from ..connectors.base import BaseConnector, ConnectorError
from ..logging_config import get_logger
from ..schemas import Order, OrderStatus
from .base import BaseExecutionEngine, ExecutionError

log = get_logger(__name__)


class LiveExecutionEngine(BaseExecutionEngine):
    name = "live"
    is_dry_run = False

    def __init__(self, connector: BaseConnector):
        if connector.is_paper_only:
            raise ExecutionError(
                f"LiveExecutionEngine cannot be built on a paper-only connector "
                f"({connector.name!r}). This should be unreachable — "
                f"execution.get_execution_engine() guards against it."
            )
        self.connector = connector

    def execute(self, order: Order) -> Order:
        order.dry_run = False
        log.warning(
            "LIVE ORDER: submitting %s %s %.8g via %s",
            order.side, order.symbol, order.quantity, self.connector.name,
        )
        try:
            receipt = self.connector.place_order(
                symbol=order.symbol,
                side=order.side.value if hasattr(order.side, "value") else str(order.side),
                quantity=order.quantity,
                order_type=order.order_type.value if hasattr(order.order_type, "value") else str(order.order_type),
                limit_price=order.limit_price,
            )
        except ConnectorError as e:
            order.status = OrderStatus.ERROR
            order.reject_reason = str(e)
            log.error("live order %s errored: %s", order.id, e)
            return order

        order.status = OrderStatus.SUBMITTED
        order.receipt = receipt if isinstance(receipt, dict) else {"raw": str(receipt)}
        # Best-effort extraction of an immediate fill price/qty; many
        # exchanges/brokers return these only once the order is later
        # polled/filled — that reconciliation is left to a future
        # `sync_order_status()` pass, not modeled here to keep this engine's
        # contract simple ("submitted" is a valid, honest terminal state for
        # a market order that hasn't confirmed yet).
        filled_price = order.receipt.get("average") or order.receipt.get("price") or order.receipt.get("filled_avg_price")
        filled_qty = order.receipt.get("filled") or order.receipt.get("filled_qty")
        if filled_price:
            order.filled_price = float(filled_price)
        if filled_qty:
            order.filled_quantity = float(filled_qty)
            if order.filled_quantity >= order.quantity:
                order.status = OrderStatus.FILLED
            elif order.filled_quantity > 0:
                order.status = OrderStatus.PARTIALLY_FILLED

        log.info("live order %s status=%s receipt_keys=%s", order.id, order.status, list(order.receipt.keys()))
        return order
