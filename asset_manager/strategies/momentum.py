"""
MomentumStrategy — ranks the universe by trailing return over `lookback`
bars and goes long the top `top_n` performers at a fixed `alloc_weight`
each; everything else already held gets a SELL to exit, and everything else
not held stays HOLD.
"""
from __future__ import annotations

from ..schemas import Candle, PortfolioState, Signal
from .base import BaseStrategy


class MomentumStrategy(BaseStrategy):
    name = "momentum"

    def __init__(self, lookback: int = 14, top_n: int = 2, alloc_weight: float = 0.20):
        if lookback < 2:
            raise ValueError("lookback must be >= 2 (need at least 2 bars to measure a return)")
        if top_n < 1:
            raise ValueError("top_n must be >= 1")
        if not (0.0 < alloc_weight <= 1.0):
            raise ValueError(f"alloc_weight must be in (0, 1], got {alloc_weight}")
        self.lookback = lookback
        self.top_n = top_n
        self.alloc_weight = alloc_weight

    def _trailing_return(self, candles: list[Candle]) -> float | None:
        if len(candles) < self.lookback:
            return None
        window = candles[-self.lookback:]
        start, end = window[0].close, window[-1].close
        if start <= 0:
            return None
        return (end - start) / start

    def generate_signals(
        self,
        market_data: dict[str, list[Candle]],
        portfolio: PortfolioState,
    ) -> list[Signal]:
        returns: dict[str, float] = {}
        insufficient: set[str] = set()

        for symbol, candles in market_data.items():
            r = self._trailing_return(candles)
            if r is None:
                insufficient.add(symbol)
            else:
                returns[symbol] = r

        ranked = sorted(returns.items(), key=lambda kv: kv[1], reverse=True)
        top_symbols = {sym for sym, _ in ranked[: self.top_n] if returns[sym] > 0}

        signals: list[Signal] = []
        for symbol, candles in market_data.items():
            if symbol in insufficient:
                signals.append(self._hold(
                    symbol,
                    f"insufficient history: {len(candles)} candles, need >= {self.lookback}",
                ))
                continue

            r = returns[symbol]
            holds_position = portfolio.positions.get(symbol) is not None and portfolio.positions[symbol].quantity > 0
            rank = [s for s, _ in ranked].index(symbol) + 1

            if symbol in top_symbols:
                signals.append(Signal(
                    symbol=symbol, action="BUY", target_weight=self.alloc_weight,
                    confidence=min(1.0, max(0.0, r)), strategy=self.name,
                    rationale=(
                        f"rank {rank}/{len(ranked)} by {self.lookback}-bar return ({r:+.2%}); "
                        f"in top {self.top_n} and positive"
                    ),
                ))
            elif holds_position:
                signals.append(Signal(
                    symbol=symbol, action="SELL", strategy=self.name,
                    confidence=min(1.0, max(0.0, -r)) if r < 0 else 0.3,
                    rationale=(
                        f"rank {rank}/{len(ranked)} by {self.lookback}-bar return ({r:+.2%}); "
                        f"fell out of top {self.top_n}, exiting held position"
                    ),
                ))
            else:
                signals.append(self._hold(
                    symbol,
                    f"rank {rank}/{len(ranked)} by {self.lookback}-bar return ({r:+.2%}); not in top {self.top_n}",
                ))
        return signals
