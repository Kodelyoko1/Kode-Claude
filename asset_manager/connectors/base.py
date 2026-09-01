"""
BaseConnector — the abstract interface every data/broker adapter implements.

Strategies, the risk manager, and the execution engines all talk to a
connector only through this interface, so swapping `PaperConnector` for
`CCXTConnector` or `YFinanceConnector` never touches the rest of the
pipeline. A connector is responsible for two things: (1) market data
(candles + current price) and (2) portfolio/account state + order
placement for LIVE connectors. Paper/dry-run order placement is handled by
`execution.paper_engine`, not by the connector.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from ..schemas import Candle, PortfolioState


class ConnectorError(RuntimeError):
    """Raised for connector-level failures (auth, rate limit, network)."""


class BaseConnector(ABC):
    """Abstract interface for a market-data source / broker."""

    name: str = "base"
    #: True for connectors that can only ever simulate (never place a real order)
    is_paper_only: bool = True

    @abstractmethod
    def fetch_ohlcv(self, symbol: str, timeframe: str = "1d", limit: int = 200) -> list[Candle]:
        """Return up to `limit` most-recent OHLCV candles, oldest first."""
        raise NotImplementedError

    @abstractmethod
    def fetch_price(self, symbol: str) -> float:
        """Return the current (last traded / last close) price for `symbol`."""
        raise NotImplementedError

    @abstractmethod
    def fetch_portfolio(self) -> PortfolioState:
        """Return the current cash + positions snapshot."""
        raise NotImplementedError

    def fetch_prices(self, symbols: list[str]) -> dict[str, float]:
        """Convenience batch helper; connectors may override for efficiency."""
        return {s: self.fetch_price(s) for s in symbols}

    def close(self) -> None:
        """Release any underlying network resources. No-op by default."""
        return None

    def __enter__(self) -> "BaseConnector":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
