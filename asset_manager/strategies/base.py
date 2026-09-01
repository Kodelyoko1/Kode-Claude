"""
BaseStrategy — the abstract interface every tactical strategy implements.

A strategy is a pure function of (market data, current portfolio) ->
list[Signal]. Strategies never touch a connector, never check risk limits,
and never place orders — that separation is what lets `risk.manager` and
`execution.*` treat every strategy identically, and what lets
`tests/test_strategies.py` unit-test signal generation with no I/O at all.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from ..schemas import Candle, PortfolioState, Signal


class BaseStrategy(ABC):
    name: str = "base"

    @abstractmethod
    def generate_signals(
        self,
        market_data: dict[str, list[Candle]],
        portfolio: PortfolioState,
    ) -> list[Signal]:
        """
        `market_data` maps symbol -> oldest-first list of Candle. Must
        return exactly one Signal per symbol present in `market_data`
        (HOLD is a valid, expected outcome — it is not "no signal").
        """
        raise NotImplementedError

    def _hold(self, symbol: str, rationale: str) -> Signal:
        return Signal(symbol=symbol, action="HOLD", rationale=rationale, strategy=self.name)
