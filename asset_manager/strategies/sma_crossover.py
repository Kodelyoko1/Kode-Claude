"""
SMACrossoverStrategy — classic trend-following tactic.

Golden cross (fast SMA crosses above slow SMA) -> BUY.
Death cross (fast SMA crosses below slow SMA)  -> SELL (only if a position
is actually held — no shorting).
Otherwise -> HOLD.

Needs at least `slow_period + 1` candles per symbol to detect a *crossover*
(as opposed to just a snapshot relationship), since a crossover requires
comparing the current bar against the previous one.
"""
from __future__ import annotations

from ..schemas import Candle, PortfolioState, Signal
from .base import BaseStrategy


def _sma(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


class SMACrossoverStrategy(BaseStrategy):
    name = "sma_crossover"

    def __init__(self, fast_period: int = 20, slow_period: int = 50):
        if fast_period < 1 or slow_period < 1:
            raise ValueError("SMA periods must be >= 1")
        if fast_period >= slow_period:
            raise ValueError(
                f"fast_period ({fast_period}) must be < slow_period ({slow_period})"
            )
        self.fast_period = fast_period
        self.slow_period = slow_period

    def generate_signals(
        self,
        market_data: dict[str, list[Candle]],
        portfolio: PortfolioState,
    ) -> list[Signal]:
        signals: list[Signal] = []
        for symbol, candles in market_data.items():
            closes = [c.close for c in candles]

            if len(closes) < self.slow_period + 1:
                signals.append(self._hold(
                    symbol,
                    f"insufficient history: {len(closes)} candles, need >= {self.slow_period + 1}",
                ))
                continue

            fast_now = _sma(closes, self.fast_period)
            slow_now = _sma(closes, self.slow_period)
            fast_prev = _sma(closes[:-1], self.fast_period)
            slow_prev = _sma(closes[:-1], self.slow_period)

            holds_position = portfolio.positions.get(symbol) is not None and portfolio.positions[symbol].quantity > 0

            golden_cross = fast_prev <= slow_prev and fast_now > slow_now
            death_cross = fast_prev >= slow_prev and fast_now < slow_now

            if golden_cross:
                signals.append(Signal(
                    symbol=symbol, action="BUY", strategy=self.name,
                    confidence=min(1.0, abs(fast_now - slow_now) / slow_now) if slow_now else 0.5,
                    rationale=(
                        f"golden cross: SMA{self.fast_period}={fast_now:.4f} crossed above "
                        f"SMA{self.slow_period}={slow_now:.4f}"
                    ),
                ))
            elif death_cross and holds_position:
                signals.append(Signal(
                    symbol=symbol, action="SELL", strategy=self.name,
                    confidence=min(1.0, abs(fast_now - slow_now) / slow_now) if slow_now else 0.5,
                    rationale=(
                        f"death cross: SMA{self.fast_period}={fast_now:.4f} crossed below "
                        f"SMA{self.slow_period}={slow_now:.4f}"
                    ),
                ))
            else:
                signals.append(self._hold(
                    symbol,
                    f"no crossover: SMA{self.fast_period}={fast_now:.4f}, SMA{self.slow_period}={slow_now:.4f}",
                ))
        return signals
