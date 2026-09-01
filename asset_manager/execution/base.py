"""
BaseExecutionEngine — the abstract interface for order routing.

An execution engine's job is narrow and dangerous by design: given an
Order that has ALREADY been approved by RiskManager, either simulate a
fill (PaperExecutionEngine) or send it to a real broker/exchange
(LiveExecutionEngine). Execution engines never re-derive sizing or
re-check risk limits — that separation keeps "what almost happened" and
"what the risk manager approved" from silently diverging.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from ..schemas import Order


class ExecutionError(RuntimeError):
    """Raised for execution-level failures (broker rejection, network)."""


class BaseExecutionEngine(ABC):
    name: str = "base"
    is_dry_run: bool = True

    @abstractmethod
    def execute(self, order: Order) -> Order:
        """
        Execute (or simulate) `order` and return it updated in place with
        status/filled_price/filled_quantity/receipt set. Never raises for
        an ordinary rejection — that's expressed via OrderStatus.REJECTED
        with `reject_reason` set. Only raises ExecutionError for
        infrastructure failures (broker unreachable, malformed response).
        """
        raise NotImplementedError
