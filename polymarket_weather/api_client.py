"""
Kalshi API client — replaces the PolyMarket CLOB/Gamma client.

Market discovery and orderbooks are public (no auth required).
Order placement requires KALSHI_KEY_ID + KALSHI_PRIVATE_KEY in .env.
Set PW_LIVE_TRADING=1 to submit real orders (default: dry-run only).

Credentials — set in Render dashboard (never commit):
  KALSHI_KEY_ID       = your API key ID from kalshi.com/profile/api
  KALSHI_PRIVATE_KEY  = PEM private key string (newlines as \\n)
                        OR set KALSHI_PRIVATE_KEY_PATH to a .pem file path
"""
from __future__ import annotations

import base64
import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

KALSHI_BASE = "https://trading-api.kalshi.com/trade-api/v2"
KALSHI_DEMO = "https://demo-api.kalshi.co/trade-api/v2"

_SESSION = requests.Session()
_SESSION.headers.update({
    "Content-Type": "application/json",
    "User-Agent": "WholesaleOmniverse-KalshiWeather/1.0",
})

# Diagnostics written by get_weather_markets() and read by the /scan endpoint
_last_fetch_info: dict = {
    "strategy": "none",
    "raw_fetched": 0,
    "weather_kept": 0,
    "source": "none",
}

# ---------------------------------------------------------------------------
# Kalshi weather series + location mappings
# ---------------------------------------------------------------------------

# Kalshi weather series tickers — fetched in priority order
WEATHER_SERIES = [
    "KXHIGHTEMP",   # daily high temperature above threshold
    "KXLOWTEMP",    # daily low temperature below threshold
    "KXRAIN",       # will it rain
    "KXSNOW",       # will it snow
    "KXWIND",       # will wind exceed threshold
]

# Kalshi location codes → CITY_COORDS keys (data_pipeline.py)
KALSHI_LOC_MAP: dict[str, str] = {
    # New York
    "NYC": "new_york", "NYS": "new_york", "NY": "new_york",
    # Chicago
    "CHI": "chicago",  "CHIL": "chicago", "ORD": "chicago",
    # Miami
    "MIA": "miami",    "MIAM": "miami",
    # Atlanta
    "ATL": "atlanta",
    # Dallas / Fort Worth
    "DFW": "dallas",   "DAL": "dallas",
    # Phoenix
    "PHX": "phoenix",
    # Houston
    "HOU": "houston",
    # Philadelphia
    "PHI": "philadelphia",
    # San Antonio
    "SA":  "san_antonio", "SATX": "san_antonio",
    # Los Angeles
    "LAX": "los_angeles", "LA": "los_angeles",
    # Seattle
    "SEA": "seattle",
    # Boston
    "BOS": "boston",
    # Denver
    "DEN": "denver",
    # Minneapolis
    "MSP": "minneapolis",
    # Detroit
    "DTW": "detroit",
    # Washington DC
    "IAD": "washington_dc", "DCA": "washington_dc",
}

# Map series ticker → event_type for the ML model
SERIES_EVENT_MAP: dict[str, str] = {
    "KXHIGHTEMP": "temp_above_90f",
    "KXLOWTEMP":  "temp_above_32f",
    "KXRAIN":     "precip_any",
    "KXSNOW":     "precip_any",
    "KXWIND":     "wind_above_25mph",
}


# ---------------------------------------------------------------------------
# Data containers (interface-compatible with old PolyMarket client)
# ---------------------------------------------------------------------------

@dataclass
class Market:
    condition_id: str       # = ticker for Kalshi
    question: str
    slug: str               # = ticker for Kalshi
    end_date: str
    tokens: list[dict]      # [{"token_id": "TICKER:yes", "outcome": "YES"},
                            #  {"token_id": "TICKER:no",  "outcome": "NO"}]
    volume: float = 0.0
    liquidity: float = 0.0
    closed: bool = False
    tags: list[str] = field(default_factory=list)
    # Kalshi extras
    ticker: str = ""
    series: str = ""
    yes_price_cents: int = 50   # 1-99
    no_price_cents: int = 50

    def yes_token_id(self) -> Optional[str]:
        """Return YES side identifier — ticker:yes."""
        return f"{self.ticker}:yes" if self.ticker else None

    def no_token_id(self) -> Optional[str]:
        """Return NO side identifier — ticker:no."""
        return f"{self.ticker}:no" if self.ticker else None


@dataclass
class PricePoint:
    timestamp: int
    price: float   # 0–1 probability


@dataclass
class OrderBook:
    token_id: str   # "TICKER:yes" or "TICKER:no" — the side this book is for
    bids: list[dict]    # [{price, size}] sorted desc — in 0-1 prob space
    asks: list[dict]    # [{price, size}] sorted asc
    spread: float = 0.0

    def best_bid(self) -> float:
        return float(self.bids[0]["price"]) if self.bids else 0.0

    def best_ask(self) -> float:
        return float(self.asks[0]["price"]) if self.asks else 1.0

    def mid_price(self) -> float:
        return (self.best_bid() + self.best_ask()) / 2


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _get(url: str, params: dict | None = None, timeout: int = 20) -> dict | list:
    resp = _SESSION.get(url, params=params, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def _post(url: str, payload: dict, headers: dict | None = None, timeout: int = 20) -> dict:
    resp = _SESSION.post(url, json=payload, headers=headers or {}, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# RSA auth for authenticated endpoints
# ---------------------------------------------------------------------------

def _load_private_key():
    """Load the RSA private key from env var or file. Returns a cryptography key object."""
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    pem: bytes = b""
    raw = os.getenv("KALSHI_PRIVATE_KEY", "").strip()
    if raw:
        # Env var may have literal \n instead of real newlines
        pem = raw.replace("\\n", "\n").encode()
    else:
        path = os.getenv("KALSHI_PRIVATE_KEY_PATH", "").strip()
        if path and Path(path).exists():
            pem = Path(path).read_bytes()

    if not pem:
        return None
    return load_pem_private_key(pem, password=None)


def _make_auth_headers(method: str, path: str, body: str = "") -> dict:
    """
    Build Kalshi RSA auth headers.
    Signature covers: timestamp_ms + METHOD + path (no host, no body).
    """
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding as asym_padding

    key_id = os.getenv("KALSHI_KEY_ID", "").strip()
    if not key_id:
        return {}

    private_key = _load_private_key()
    if private_key is None:
        return {}

    timestamp_ms = str(int(time.time() * 1000))
    msg_str = timestamp_ms + method.upper() + path
    signature = private_key.sign(
        msg_str.encode(),
        asym_padding.PKCS1v15(),
        hashes.SHA256(),
    )
    return {
        "KALSHI-ACCESS-KEY":       key_id,
        "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode(),
        "KALSHI-ACCESS-TIMESTAMP": timestamp_ms,
    }


# ---------------------------------------------------------------------------
# Market discovery
# ---------------------------------------------------------------------------

def _parse_kalshi_market(m: dict) -> Optional[Market]:
    """Convert a Kalshi /markets API dict to a Market dataclass."""
    ticker  = m.get("ticker", "")
    series  = m.get("series_ticker", "")
    status  = m.get("status", "")
    closed  = status not in ("open", "unopened")
    title   = m.get("title") or m.get("subtitle") or ticker

    # Prices in cents (1-99); fall back to 50 if missing
    yes_bid = int(m.get("yes_bid") or 0)
    yes_ask = int(m.get("yes_ask") or 99)
    no_bid  = int(m.get("no_bid")  or 0)
    no_ask  = int(m.get("no_ask")  or 99)

    yes_mid_cents = (yes_bid + yes_ask) // 2 if yes_bid and yes_ask else 50
    no_mid_cents  = (no_bid  + no_ask)  // 2 if no_bid  and no_ask  else 50

    close_time = (
        m.get("close_time") or
        m.get("expected_expiration_time") or
        m.get("end_date") or ""
    )
    # Normalise to YYYY-MM-DD
    end_date = close_time[:10] if close_time else ""

    liquidity     = float(m.get("liquidity")      or m.get("dollar_open_interest") or
                          m.get("open_interest")   or 0)
    volume        = float(m.get("volume")          or 0)

    return Market(
        condition_id    = ticker,
        question        = title,
        slug            = ticker,
        end_date        = end_date,
        tokens          = [
            {"token_id": f"{ticker}:yes", "outcome": "YES"},
            {"token_id": f"{ticker}:no",  "outcome": "NO"},
        ],
        volume          = volume,
        liquidity       = liquidity,
        closed          = closed,
        tags            = [series] if series else [],
        ticker          = ticker,
        series          = series,
        yes_price_cents = yes_mid_cents,
        no_price_cents  = no_mid_cents,
    )


def get_weather_markets(limit: int = 200, closed: bool = False) -> list[Market]:
    """
    Return Kalshi weather markets, one fetch per WEATHER_SERIES entry.

    Falls back to on-disk synthetic cache if all series fail.
    """
    global _last_fetch_info

    status_filter = "settled" if closed else "open"
    all_markets: list[Market] = []
    series_fetched = []

    for series in WEATHER_SERIES:
        try:
            data = _get(f"{KALSHI_BASE}/markets", params={
                "series_ticker": series,
                "status":        status_filter,
                "limit":         min(limit, 1000),
            })
            rows = data.get("markets", data) if isinstance(data, dict) else data
            for m in rows:
                parsed = _parse_kalshi_market(m)
                if parsed:
                    all_markets.append(parsed)
            series_fetched.append(series)
        except Exception:
            continue

    if all_markets:
        strategy = "series: " + ",".join(series_fetched)
        _last_fetch_info.update({
            "strategy":     strategy,
            "raw_fetched":  len(all_markets),
            "weather_kept": len(all_markets),
            "source":       "live",
        })
        return all_markets

    # Fallback: synthetic cache
    cached = _load_cached_markets(closed=closed)
    _last_fetch_info.update({
        "strategy":     "cache",
        "raw_fetched":  len(cached),
        "weather_kept": len(cached),
        "source":       "cache",
    })
    return cached


def _load_cached_markets(closed: bool = False) -> list[Market]:
    """Load markets from on-disk synthetic cache."""
    cache = Path(__file__).parent.parent / "data" / "pw_historical" / "markets_cache.json"
    if not cache.exists():
        return []
    try:
        raw = json.loads(cache.read_text())
    except (OSError, json.JSONDecodeError):
        return []
    markets = []
    for m in raw:
        if m.get("closed", False) != closed and not m.get("_synthetic"):
            continue
        ticker = m.get("condition_id", m.get("slug", ""))
        markets.append(Market(
            condition_id = ticker,
            question     = m.get("question", ""),
            slug         = ticker,
            end_date     = m.get("end_date", ""),
            tokens       = [
                {"token_id": f"{ticker}:yes", "outcome": "YES"},
                {"token_id": f"{ticker}:no",  "outcome": "NO"},
            ],
            volume       = float(m.get("volume",    0) or 0),
            liquidity    = float(m.get("liquidity", 0) or 0),
            closed       = m.get("closed", False),
            tags         = [t.get("slug", t) if isinstance(t, dict) else t
                            for t in (m.get("tags") or [])],
            ticker       = ticker,
            series       = "",
        ))
    return markets


# ---------------------------------------------------------------------------
# Orderbook
# ---------------------------------------------------------------------------

def get_order_book(token_id: str) -> OrderBook:
    """
    Fetch orderbook for a Kalshi market side.
    `token_id` is "TICKER:yes" or "TICKER:no" — we always query YES-side depth
    and flip the frame for NO.

    Falls back to a synthetic spread when the API is unreachable.
    """
    # Split ticker and side
    if ":" in token_id:
        ticker, side = token_id.rsplit(":", 1)
    else:
        ticker, side = token_id, "yes"

    want_yes = side.lower() == "yes"

    try:
        data = _get(f"{KALSHI_BASE}/markets/{ticker}/orderbook", params={"depth": 20})
        ob   = data.get("orderbook", data)

        # Kalshi orderbook: {"yes": [[price_cents, size], ...], "no": [...]}
        # Prices are integers 1-99; sizes are number of contracts
        raw_yes = ob.get("yes", [])   # bids on YES (descending)
        raw_no  = ob.get("no",  [])   # bids on NO  (descending)

        def to_levels(raw: list) -> list[dict]:
            return [{"price": p / 100.0, "size": float(s)} for p, s in raw if p]

        yes_bids = sorted(to_levels(raw_yes), key=lambda x: -x["price"])
        no_bids  = sorted(to_levels(raw_no),  key=lambda x: -x["price"])

        # YES ask is implied by the best NO bid: ask_yes = 1 - best_no_bid
        yes_asks = [{"price": round(1.0 - nb["price"], 4), "size": nb["size"]}
                    for nb in no_bids[:5]] if no_bids else []
        no_asks  = [{"price": round(1.0 - yb["price"], 4), "size": yb["size"]}
                    for yb in yes_bids[:5]] if yes_bids else []

        if want_yes:
            bids, asks = yes_bids, sorted(yes_asks, key=lambda x: x["price"])
        else:
            bids, asks = no_bids,  sorted(no_asks,  key=lambda x: x["price"])

    except Exception:
        mid = _synthetic_price(ticker, want_yes)
        spread = 0.04
        bids = [{"price": round(mid - spread / 2, 4), "size": 100.0}]
        asks = [{"price": round(mid + spread / 2, 4), "size": 100.0}]

    book = OrderBook(token_id=token_id, bids=bids, asks=asks)
    if bids and asks:
        book.spread = asks[0]["price"] - bids[0]["price"]
    return book


def _synthetic_price(ticker: str, want_yes: bool) -> float:
    """Deterministic fallback price from cache or hash."""
    import hashlib
    cache = Path(__file__).parent.parent / "data" / "pw_historical" / "markets_cache.json"
    if cache.exists():
        try:
            for m in json.loads(cache.read_text()):
                if m.get("condition_id") == ticker or m.get("slug") == ticker:
                    p = float(m.get("_mid_price", 0.5) or 0.5)
                    return p if want_yes else 1.0 - p
        except Exception:
            pass
    h = int(hashlib.md5(ticker.encode()).hexdigest(), 16)
    p = 0.15 + (h % 700) / 1000.0
    return p if want_yes else 1.0 - p


def get_midpoint_price(token_id: str) -> float:
    """Convenience: mid-price of best bid/ask."""
    return get_order_book(token_id).mid_price()


def get_market_price(token_id: str, side: str = "buy") -> float:
    """Best ask (side='buy') or best bid (side='sell'). Returns 0–1."""
    book = get_order_book(token_id)
    return book.best_ask() if side == "buy" else book.best_bid()


# ---------------------------------------------------------------------------
# Location + series helpers (used by agent.py)
# ---------------------------------------------------------------------------

def extract_city_from_ticker(ticker: str) -> Optional[str]:
    """
    Extract city key from a Kalshi ticker.
    Tickers look like KXHIGHTEMP-25AUG26-T85-NYC or KXRAIN-25AUG26-MIA.
    The last segment before any threshold segment is the location code.
    """
    parts = ticker.upper().split("-")
    # Location is typically the last part; sometimes it's the part after the threshold
    for part in reversed(parts):
        if part in KALSHI_LOC_MAP:
            return KALSHI_LOC_MAP[part]
    # Try all parts (some tickers have the location in the middle)
    for part in parts:
        if part in KALSHI_LOC_MAP:
            return KALSHI_LOC_MAP[part]
    return None


def extract_series_from_ticker(ticker: str) -> str:
    """Return the series prefix from a ticker, e.g. 'KXHIGHTEMP'."""
    parts = ticker.upper().split("-")
    return parts[0] if parts else ""


def series_to_event_type(series: str) -> str:
    """Map series ticker → event_type for ML model."""
    return SERIES_EVENT_MAP.get(series.upper(), "temp_above_90f")


# ---------------------------------------------------------------------------
# Authenticated order placement
# ---------------------------------------------------------------------------

class KalshiTrader:
    """
    Wraps Kalshi Trade API v2 for authenticated order placement.
    Gracefully degrades to dry-run when credentials are absent.

    Env vars (set in Render dashboard — never commit):
      KALSHI_KEY_ID       API key ID from kalshi.com/profile/api
      KALSHI_PRIVATE_KEY  PEM private key (newlines as \\n)
    """

    def __init__(self):
        self.key_id  = os.getenv("KALSHI_KEY_ID", "").strip()
        self.live    = os.getenv("PW_LIVE_TRADING", "0").strip() == "1"
        # Demo mode: use demo API when KALSHI_DEMO=1
        self._base   = KALSHI_DEMO if os.getenv("KALSHI_DEMO", "0") == "1" else KALSHI_BASE
        self._has_key = bool(self.key_id and _load_private_key() is not None)

    def _auth_headers(self, method: str, path: str) -> dict:
        return _make_auth_headers(method, path)

    def place_limit_order(
        self,
        token_id: str,    # "TICKER:yes" or "TICKER:no"
        side: str,        # always "BUY" from agent.py
        price: float,     # 0-1 probability (mid-price from orderbook)
        size: float,      # dollar amount to risk
    ) -> dict:
        """
        Place a limit order on Kalshi.

        Converts dollar size → contract count:
          count = round(size / price_per_contract)
          price_per_contract = price_cents / 100

        In dry-run mode returns a simulated receipt.
        """
        # Parse side from token_id
        if ":" in token_id:
            ticker, kalshi_side = token_id.rsplit(":", 1)
        else:
            ticker, kalshi_side = token_id, "yes"

        # Clamp price to valid Kalshi range (1-99 cents)
        price_clamped = max(0.01, min(0.99, price))
        price_cents   = max(1, min(99, round(price_clamped * 100)))

        # Contract count: how many $1-max contracts we can buy with `size` dollars
        cost_per_contract = price_cents / 100.0
        count = max(1, round(size / cost_per_contract))

        receipt = {
            "token_id":    token_id,
            "ticker":      ticker,
            "kalshi_side": kalshi_side,
            "side":        side,
            "price":       price_clamped,
            "price_cents": price_cents,
            "count":       count,
            "size":        round(count * cost_per_contract, 2),
            "timestamp":   int(time.time()),
            "dry_run":     not self.live,
        }

        if not self.live:
            receipt["status"]   = "dry_run"
            receipt["order_id"] = f"DRY-{int(time.time())}"
            return receipt

        if not self._has_key:
            receipt["status"] = "error"
            receipt["error"]  = "No KALSHI_KEY_ID / KALSHI_PRIVATE_KEY configured"
            return receipt

        path    = "/trade-api/v2/portfolio/orders"
        payload = {
            "ticker":     ticker,
            "side":       kalshi_side.lower(),
            "action":     "buy",
            "type":       "limit",
            "count":      count,
            # Kalshi expects yes_price for YES orders, no_price for NO orders
            (f"{kalshi_side.lower()}_price"): price_cents,
        }

        try:
            headers = self._auth_headers("POST", path)
            resp = _post(f"{self._base}/portfolio/orders", payload, headers=headers)
            order = resp.get("order", resp)
            receipt["status"]   = order.get("status", "submitted")
            receipt["order_id"] = order.get("order_id", order.get("id", ""))
        except Exception as exc:
            receipt["status"] = "error"
            receipt["error"]  = str(exc)

        return receipt

    def get_positions(self) -> list[dict]:
        """List open positions from the authenticated account."""
        if not self._has_key:
            return []
        path = "/trade-api/v2/portfolio/positions"
        try:
            headers = self._auth_headers("GET", path)
            resp    = _get(f"{self._base}/portfolio/positions", headers)  # type: ignore
            return resp.get("market_positions", [])
        except Exception:
            return []

    def cancel_order(self, order_id: str) -> bool:
        if not self._has_key:
            return False
        path = f"/trade-api/v2/portfolio/orders/{order_id}"
        try:
            from cryptography.hazmat.primitives import hashes
            from cryptography.hazmat.primitives.asymmetric import padding as asym_padding
            headers = self._auth_headers("DELETE", path)
            resp = _SESSION.delete(f"{self._base}/portfolio/orders/{order_id}",
                                   headers=headers, timeout=20)
            return resp.status_code in (200, 204)
        except Exception:
            return False


# ---------------------------------------------------------------------------
# Compatibility stubs — functions that existed in the PolyMarket client
# but have no direct Kalshi equivalent; return safe empty values.
# ---------------------------------------------------------------------------

def get_price_history(
    market: str,
    start_ts: int,
    end_ts: int,
    fidelity: int = 60,
) -> list[PricePoint]:
    """
    Kalshi does not expose a public historical price API.
    Returns an empty list; the data_pipeline skips this gracefully.
    """
    return []


# PolyMarketTrader alias for any leftover import
PolyMarketTrader = KalshiTrader
