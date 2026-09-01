from .base import BaseExecutionEngine, ExecutionError
from .paper_engine import PaperExecutionEngine

__all__ = ["BaseExecutionEngine", "ExecutionError", "PaperExecutionEngine", "get_execution_engine"]


def get_execution_engine(connector, settings=None) -> BaseExecutionEngine:
    """
    Factory: PAPER TRADING IS THE DEFAULT. Only returns a live engine when
    `settings.paper_trading` is explicitly False AND the connector is a
    live-capable one — both conditions, independently, must be true.
    """
    from ..config.settings import get_settings
    settings = settings or get_settings()

    if settings.paper_trading or connector.is_paper_only:
        return PaperExecutionEngine(
            simulated_slippage_pct=settings.simulated_slippage_pct,
            max_slippage_pct=settings.max_slippage_pct,
        )

    from .live_engine import LiveExecutionEngine
    return LiveExecutionEngine(connector=connector)
