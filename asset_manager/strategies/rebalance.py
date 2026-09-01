"""
RebalanceStrategy — the default strategy (step 4 of the implementation plan).

Compares each symbol's current portfolio weight against a configured target
weight (asset_manager/config/target_allocations.yaml). When the drift
exceeds `drift_threshold`, emits a BUY or SELL signal carrying the target
weight itself — sizing to reach it is the execution layer's job, not the
strategy's, so this stays a pure function of (market data, portfolio).
"""
from __future__ import annotations

from ..schemas import Candle, PortfolioState, Signal
from .base import BaseStrategy


class RebalanceStrategy(BaseStrategy):
    name = "rebalance"

    def __init__(self, target_weights: dict[str, float], drift_threshold: float = 0.05):
        if not (0.0 <= drift_threshold <= 1.0):
            raise ValueError(f"drift_threshold must be in [0, 1], got {drift_threshold}")
        for symbol, w in target_weights.items():
            if not (0.0 <= w <= 1.0):
                raise ValueError(f"target weight for {symbol} must be in [0, 1], got {w}")
        self.target_weights = dict(target_weights)
        self.drift_threshold = drift_threshold

    def generate_signals(
        self,
        market_data: dict[str, list[Candle]],
        portfolio: PortfolioState,
    ) -> list[Signal]:
        signals: list[Signal] = []
        for symbol in market_data:
            target = self.target_weights.get(symbol, 0.0)
            current = portfolio.weight_of(symbol)
            drift = target - current

            if abs(drift) < self.drift_threshold:
                signals.append(Signal(
                    symbol=symbol, action="HOLD", target_weight=target,
                    confidence=1.0, strategy=self.name,
                    rationale=(
                        f"current weight {current:.1%} within {self.drift_threshold:.1%} "
                        f"of target {target:.1%} (drift {drift:+.1%})"
                    ),
                ))
                continue

            action = "BUY" if drift > 0 else "SELL"
            signals.append(Signal(
                symbol=symbol, action=action, target_weight=target,
                confidence=min(1.0, abs(drift) / max(self.drift_threshold, 1e-9)),
                strategy=self.name,
                rationale=(
                    f"current weight {current:.1%} vs. target {target:.1%} "
                    f"(drift {drift:+.1%}) exceeds {self.drift_threshold:.1%} threshold"
                ),
            ))
        return signals
