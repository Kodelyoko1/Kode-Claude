from .base import BaseConnector, ConnectorError
from .paper_connector import PaperConnector

__all__ = ["BaseConnector", "ConnectorError", "PaperConnector"]


def get_connector(name: str, settings=None):
    """
    Factory: build a connector by name ("paper" | "ccxt" | "yfinance").
    ccxt/yfinance are imported lazily inside this function so the paper
    connector — and everything that only needs paper trading — works with
    zero extra dependencies installed.
    """
    from ..config.settings import get_settings
    settings = settings or get_settings()

    if name == "paper":
        return PaperConnector(starting_cash=settings.starting_cash)
    if name == "ccxt":
        from .ccxt_connector import CCXTConnector
        return CCXTConnector(
            exchange_id=settings.exchange_id,
            api_key=settings.exchange_api_key,
            api_secret=settings.exchange_api_secret,
            sandbox=settings.exchange_sandbox,
        )
    if name == "yfinance":
        from .yfinance_connector import YFinanceConnector
        return YFinanceConnector(starting_cash=settings.starting_cash)

    raise ValueError(f"Unknown connector: {name!r} (expected paper|ccxt|yfinance)")
