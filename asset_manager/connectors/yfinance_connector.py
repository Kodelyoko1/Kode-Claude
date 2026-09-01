"""
YFinanceConnector — free equities market data via `yfinance`, with optional
live order routing through the Alpaca paper/live trading REST API.

`yfinance` has no brokerage account behind it, so:
  - fetch_ohlcv / fetch_price use yfinance (no API key required at all).
  - fetch_portfolio() falls back to the same SQLite-backed paper ledger the
    PaperConnector uses, UNLESS Alpaca credentials are configured, in which
    case it reads the real Alpaca account.
  - place_order() requires Alpaca credentials (AM_ALPACA_API_KEY /
    AM_ALPACA_API_SECRET) and is only ever reached via
    execution.live_engine.LiveExecutionEngine.

`yfinance` and `requests` are imported lazily so importing
`asset_manager.connectors` never fails for paper-only users.
"""
from __future__ import annotations

from typing import Optional

from ..schemas import Candle, PortfolioState, Position
from .base import BaseConnector, ConnectorError

try:
    import yfinance as yf
except ImportError:  # pragma: no cover
    yf = None


class YFinanceConnector(BaseConnector):
    name = "yfinance"
    is_paper_only = False

    def __init__(
        self,
        starting_cash: float = 10_000.0,
        alpaca_api_key: str = "",
        alpaca_api_secret: str = "",
        alpaca_base_url: str = "https://paper-api.alpaca.markets",
    ):
        if yf is None:
            raise ConnectorError(
                "yfinance is not installed. Run `pip install -r requirements-asset-manager.txt` "
                "to use AM_CONNECTOR=yfinance, or use AM_CONNECTOR=paper for a zero-dependency run."
            )
        self.starting_cash = starting_cash
        self.alpaca_api_key = alpaca_api_key
        self.alpaca_api_secret = alpaca_api_secret
        self.alpaca_base_url = alpaca_base_url.rstrip("/")
        self._alpaca_configured = bool(alpaca_api_key and alpaca_api_secret)

    def _alpaca_headers(self) -> dict:
        return {
            "APCA-API-KEY-ID": self.alpaca_api_key,
            "APCA-API-SECRET-KEY": self.alpaca_api_secret,
        }

    def fetch_ohlcv(self, symbol: str, timeframe: str = "1d", limit: int = 200) -> list[Candle]:
        interval = _timeframe_to_yf_interval(timeframe)
        period = _period_for(interval, limit)
        try:
            df = yf.Ticker(symbol).history(period=period, interval=interval)
        except Exception as e:
            raise ConnectorError(f"fetch_ohlcv({symbol}) failed: {e}") from e
        if df is None or df.empty:
            raise ConnectorError(f"yfinance returned no data for {symbol!r} (bad ticker?)")

        df = df.tail(limit)
        candles = []
        for ts, row in df.iterrows():
            candles.append(Candle(
                timestamp=ts.to_pydatetime(),
                open=float(row["Open"]), high=float(row["High"]),
                low=float(row["Low"]), close=float(row["Close"]),
                volume=float(row.get("Volume", 0.0) or 0.0),
            ))
        return candles

    def fetch_price(self, symbol: str) -> float:
        try:
            fast = yf.Ticker(symbol).fast_info
            price = fast.get("lastPrice") if isinstance(fast, dict) else getattr(fast, "last_price", None)
        except Exception as e:
            raise ConnectorError(f"fetch_price({symbol}) failed: {e}") from e
        if price is None:
            # fall back to the last daily close
            candles = self.fetch_ohlcv(symbol, timeframe="1d", limit=1)
            if not candles:
                raise ConnectorError(f"no price available for {symbol}")
            return candles[-1].close
        return float(price)

    def fetch_portfolio(self) -> PortfolioState:
        if self._alpaca_configured:
            return self._fetch_alpaca_portfolio()
        from .. import storage
        snapshot = storage.load_latest_portfolio()
        if snapshot is not None:
            return snapshot
        return PortfolioState(cash=self.starting_cash, positions={}, day_start_equity=self.starting_cash)

    def _fetch_alpaca_portfolio(self) -> PortfolioState:
        import requests
        try:
            acct = requests.get(f"{self.alpaca_base_url}/v2/account", headers=self._alpaca_headers(), timeout=15)
            acct.raise_for_status()
            positions_resp = requests.get(f"{self.alpaca_base_url}/v2/positions", headers=self._alpaca_headers(), timeout=15)
            positions_resp.raise_for_status()
        except requests.RequestException as e:
            raise ConnectorError(f"Alpaca account fetch failed: {e}") from e

        acct_data = acct.json()
        positions = {}
        for p in positions_resp.json():
            symbol = p["symbol"]
            positions[symbol] = Position(
                symbol=symbol,
                quantity=float(p["qty"]),
                avg_entry_price=float(p["avg_entry_price"]),
                current_price=float(p.get("current_price", p["avg_entry_price"])),
            )
        return PortfolioState(cash=float(acct_data["cash"]), positions=positions)

    def place_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        order_type: str = "market",
        limit_price: Optional[float] = None,
    ) -> dict:
        """Places a REAL order via Alpaca. Only ever called by
        execution.live_engine.LiveExecutionEngine."""
        if not self._alpaca_configured:
            raise ConnectorError(
                "place_order() requires AM_ALPACA_API_KEY/AM_ALPACA_API_SECRET "
                "(yfinance itself has no brokerage — equities live trading routes through Alpaca)."
            )
        import requests
        payload = {
            "symbol": symbol,
            "qty": str(quantity),
            "side": side.lower(),
            "type": order_type,
            "time_in_force": "day",
        }
        if order_type == "limit":
            if limit_price is None:
                raise ConnectorError("limit orders require limit_price")
            payload["limit_price"] = str(limit_price)
        try:
            resp = requests.post(
                f"{self.alpaca_base_url}/v2/orders",
                headers=self._alpaca_headers(), json=payload, timeout=15,
            )
            resp.raise_for_status()
        except requests.RequestException as e:
            raise ConnectorError(f"Alpaca place_order({symbol}, {side}, {quantity}) failed: {e}") from e
        return resp.json()


def _timeframe_to_yf_interval(timeframe: str) -> str:
    # yfinance accepts: 1m,2m,5m,15m,30m,60m,90m,1h,1d,5d,1wk,1mo,3mo
    aliases = {"1h": "60m", "1w": "1wk", "1mo": "1mo"}
    return aliases.get(timeframe, timeframe)


def _period_for(interval: str, limit: int) -> str:
    """Pick a yfinance `period` string wide enough to contain `limit` bars
    of `interval` size. Intraday intervals under 1d are capped by Yahoo at
    ~60 days of history regardless of what's requested."""
    if interval.endswith("m") or interval == "60m":
        return "60d"
    if interval == "1d":
        days = max(limit * 2, 30)
        return f"{days}d" if days <= 3650 else "10y"
    return "5y"
