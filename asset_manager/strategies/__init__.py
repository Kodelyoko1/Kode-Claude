from .base import BaseStrategy
from .rebalance import RebalanceStrategy
from .sma_crossover import SMACrossoverStrategy
from .momentum import MomentumStrategy

__all__ = [
    "BaseStrategy",
    "RebalanceStrategy",
    "SMACrossoverStrategy",
    "MomentumStrategy",
    "get_strategy",
]


def get_strategy(name: str, settings=None) -> BaseStrategy:
    """Factory: build a strategy by name from Settings + config/*.yaml."""
    from ..config.settings import get_settings, load_target_allocations
    settings = settings or get_settings()

    if name == "rebalance":
        allocations = load_target_allocations().allocations
        return RebalanceStrategy(
            target_weights=allocations,
            drift_threshold=settings.rebalance_drift_threshold,
        )
    if name == "sma_crossover":
        return SMACrossoverStrategy(
            fast_period=settings.sma_fast_period,
            slow_period=settings.sma_slow_period,
        )
    if name == "momentum":
        return MomentumStrategy(
            lookback=settings.momentum_lookback,
            top_n=settings.momentum_top_n,
            alloc_weight=settings.momentum_alloc_weight,
        )
    raise ValueError(f"Unknown strategy: {name!r} (expected rebalance|sma_crossover|momentum)")
