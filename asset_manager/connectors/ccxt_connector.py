"""
CCXTConnector — crypto exchange market data + (optional) live order routing
via the `ccxt` library (https://github.com/ccxt/ccxt).

`ccxt` is an optional dependency (see requirements-asset-manager.txt). It is
imported lazily so importing `asset_manager.connectors` never fails for
users who only run in paper mode.

Credentials come from Settings (AM_EXCHANGE_API_KEY / AM_EXCHANGE_API_SECRET
in `.env`) — never hardcoded, per repo policy. When no key/secret is set,
this connector still works for public market data (fetch_ohlcv/fetch_price);
only fetch_portfolio() and place_order() require authenticated credentials.
"""
from __future__ import annotations

from typing import Optional

from ..schemas import Candle, PortfolioState, Position
from .base import BaseConnector, ConnectorError

try:
    import ccxt
except ImportError:  # pragma: no cover - exercised only when ccxt isn't installed
    ccxt = None


class CCXTConnector(BaseConnector):
    name = "ccxt"
    is_paper_only = False

    def __init__(
        self,
        exchange_id: str = "binance",
        api_key: str = "",
        api_secret: str = "",
        sandbox: bool = True,
    ):
        if ccxt is None:
            raise ConnectorError(
                "ccxt is not installed. Run `pip install -r requirements-asset-manager.txt` "
                "to use AM_CONNECTOR=ccxt, or use AM_CONNECTOR=paper for a zero-dependency run."
            )
        if not hasattr(ccxt, exchange_id):
            raise ConnectorError(f"ccxt has no exchange named {exchange_id!r}")

        exchange_class = getattr(ccxt, exchange_id)
        self.exchange = exchange_class({
            "apiKey": api_key or None,
            "secret": api_secret or None,
            "enableRateLimit": True,
        })
        if sandbox and hasattr(self.exchange, "set_sandbox_mode"):
            try:
                self.exchange.set_sandbox_mode(True)
            except Exception:
                pass  # not every exchange supports a sandbox; fall through to live-readonly

        self._authenticated = bool(api_key and api_secret)

    def fetch_ohlcv(self, symbol: str, timeframe: str = "1d", limit: int = 200) -> list[Candle]:
        try:
            raw = self.exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        except Exception as e:  # ccxt raises many exchange-specific subclasses
            raise ConnectorError(f"fetch_ohlcv({symbol}) failed: {e}") from e

        return [
            Candle(
                timestamp=self.exchange.iso8601(ts),
                open=o, high=h, low=l, close=c, volume=v or 0.0,
            )
            for ts, o, h, l, c, v in raw
        ]

    def fetch_price(self, symbol: str) -> float:
        try:
            ticker = self.exchange.fetch_ticker(symbol)
        except Exception as e:
            raise ConnectorError(f"fetch_price({symbol}) failed: {e}") from e
        price = ticker.get("last") or ticker.get("close")
        if price is None:
            raise ConnectorError(f"exchange returned no last/close price for {symbol}")
        return float(price)

    def fetch_portfolio(self) -> PortfolioState:
        if not self._authenticated:
            raise ConnectorError(
                "fetch_portfolio() requires AM_EXCHANGE_API_KEY/AM_EXCHANGE_API_SECRET "
                "(read-only order-book/ticker data doesn't need credentials, account "
                "balance does)."
            )
        try:
            balance = self.exchange.fetch_balance()
        except Exception as e:
            raise ConnectorError(f"fetch_portfolio() failed: {e}") from e

        cash = float(balance.get("free", {}).get("USDT", 0.0) or 0.0)
        positions: dict[str, Position] = {}
        totals = balance.get("total", {}) or {}
        for asset, qty in totals.items():
            if asset in ("USDT", "USD", "USDC") or not qty:
                continue
            symbol = f"{asset}/USDT"
            try:
                price = self.fetch_price(symbol)
            except ConnectorError:
                continue
            positions[symbol] = Position(
                symbol=symbol, quantity=float(qty),
                avg_entry_price=price, current_price=price,
            )
        return PortfolioState(cash=cash, positions=positions)

    def place_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        order_type: str = "market",
        limit_price: Optional[float] = None,
    ) -> dict:
        """Places a REAL order on the exchange. Only ever called by
        execution.live_engine.LiveExecutionEngine, which is itself only
        reachable when Settings.paper_trading is False."""
        if not self._authenticated:
            raise ConnectorError("place_order() requires exchange API credentials")
        try:
            if order_type == "market":
                return self.exchange.create_order(symbol, "market", side.lower(), quantity)
            return self.exchange.create_order(symbol, "limit", side.lower(), quantity, limit_price)
        except Exception as e:
            raise ConnectorError(f"place_order({symbol}, {side}, {quantity}) failed: {e}") from e

    def close(self) -> None:
        if ccxt is not None and hasattr(self.exchange, "close"):
            try:
                self.exchange.close()
            except Exception:
                pass
