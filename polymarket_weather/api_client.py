"""
PolyMarket API client — wraps both the public Gamma API (market discovery/metadata)
and the CLOB API (prices, order books, authenticated order placement).

Public endpoints work with no credentials.
Order placement requires PW_PRIVATE_KEY + optional L2 API creds in .env.
Set PW_LIVE_TRADING=1 to actually submit orders (default: dry-run only).
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

GAMMA_API  = "https://gamma-api.polymarket.com"
CLOB_API   = "https://clob.polymarket.com"
CHAIN_ID   = 137  # Polygon mainnet

_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": "WholesaleOmniverse-PolyWeather/1.0"})


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------

@dataclass
class Market:
    condition_id: str
    question: str
    slug: str
    end_date: str
    tokens: list[dict]           # [{token_id, outcome}]
    volume: float = 0.0
    liquidity: float = 0.0
    closed: bool = False
    tags: list[str] = field(default_factory=list)

    def yes_token_id(self) -> Optional[str]:
        for t in self.tokens:
            if t.get("outcome", "").upper() == "YES":
                return t["token_id"]
        return self.tokens[0]["token_id"] if self.tokens else None

    def no_token_id(self) -> Optional[str]:
        for t in self.tokens:
            if t.get("outcome", "").upper() == "NO":
                return t["token_id"]
        return self.tokens[1]["token_id"] if len(self.tokens) > 1 else None


@dataclass
class PricePoint:
    timestamp: int
    price: float   # 0–1, where 1 = $1 = YES resolved


@dataclass
class OrderBook:
    token_id: str
    bids: list[dict]   # [{price, size}] sorted desc
    asks: list[dict]   # [{price, size}] sorted asc
    spread: float = 0.0

    def best_bid(self) -> float:
        return float(self.bids[0]["price"]) if self.bids else 0.0

    def best_ask(self) -> float:
        return float(self.asks[0]["price"]) if self.asks else 1.0

    def mid_price(self) -> float:
        return (self.best_bid() + self.best_ask()) / 2


# ---------------------------------------------------------------------------
# Public API helpers (no auth required)
# ---------------------------------------------------------------------------

def _get(url: str, params: dict | None = None, timeout: int = 20) -> dict | list:
    resp = _SESSION.get(url, params=params, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def get_weather_markets(limit: int = 200, closed: bool = False) -> list[Market]:
    """Return all PolyMarket markets tagged 'weather' via the Gamma API."""
    params = {
        "tag_slug": "weather",
        "closed": str(closed).lower(),
        "limit": limit,
    }
    data = _get(f"{GAMMA_API}/markets", params=params)
    markets = []
    for m in data if isinstance(data, list) else data.get("data", []):
        tokens = []
        for t in m.get("tokens", []) or m.get("clobTokenIds", []):
            if isinstance(t, dict):
                tokens.append(t)
            else:
                # clobTokenIds is a list of raw IDs; pair with outcomes
                pass
        # Gamma sometimes nests token info differently
        if not tokens:
            outcomes = m.get("outcomes", ["Yes", "No"])
            clob_ids = m.get("clobTokenIds") or []
            tokens = [
                {"token_id": tid, "outcome": out}
                for tid, out in zip(clob_ids, outcomes)
            ]
        markets.append(Market(
            condition_id=m.get("conditionId", m.get("condition_id", "")),
            question=m.get("question", ""),
            slug=m.get("slug", ""),
            end_date=m.get("endDate", m.get("end_date", "")),
            tokens=tokens,
            volume=float(m.get("volume", 0) or 0),
            liquidity=float(m.get("liquidity", 0) or 0),
            closed=m.get("closed", False),
            tags=[t.get("slug", "") for t in (m.get("tags") or [])],
        ))
    return markets


def get_market_price(token_id: str, side: str = "buy") -> float:
    """Best ask (side='buy') or best bid (side='sell') from CLOB. Returns 0–1."""
    try:
        data = _get(f"{CLOB_API}/price", params={"token_id": token_id, "side": side})
        return float(data.get("price", 0.5))
    except Exception:
        return 0.5


def get_order_book(token_id: str) -> OrderBook:
    """Fetch full order book for a token."""
    data = _get(f"{CLOB_API}/book", params={"token_id": token_id})
    bids = [{"price": float(b["price"]), "size": float(b["size"])}
            for b in data.get("bids", [])]
    asks = [{"price": float(a["price"]), "size": float(a["size"])}
            for a in data.get("asks", [])]
    bids.sort(key=lambda x: -x["price"])
    asks.sort(key=lambda x:  x["price"])
    book = OrderBook(token_id=token_id, bids=bids, asks=asks)
    if bids and asks:
        book.spread = asks[0]["price"] - bids[0]["price"]
    return book


def get_price_history(
    market: str,
    start_ts: int,
    end_ts: int,
    fidelity: int = 60,
) -> list[PricePoint]:
    """
    Hourly (fidelity=60) or minute-level price history from CLOB.
    `market` is the conditionId (not the token_id).
    """
    params = {
        "market": market,
        "startTs": start_ts,
        "endTs": end_ts,
        "fidelity": fidelity,
    }
    try:
        data = _get(f"{CLOB_API}/prices-history", params=params)
        history = data.get("history", [])
        return [
            PricePoint(timestamp=int(p["t"]), price=float(p["p"]))
            for p in history
        ]
    except Exception:
        return []


def get_midpoint_price(token_id: str) -> float:
    """Convenience: midpoint of best bid/ask."""
    book = get_order_book(token_id)
    return book.mid_price()


# ---------------------------------------------------------------------------
# Authenticated order placement
# ---------------------------------------------------------------------------

class PolyMarketTrader:
    """
    Wraps py_clob_client for authenticated L2 order placement.
    Gracefully degrades to dry-run if credentials are missing.
    """

    def __init__(self):
        self.private_key    = os.getenv("PW_PRIVATE_KEY", "")
        self.api_key        = os.getenv("PW_API_KEY", "")
        self.api_secret     = os.getenv("PW_API_SECRET", "")
        self.api_passphrase = os.getenv("PW_API_PASSPHRASE", "")
        self.live           = os.getenv("PW_LIVE_TRADING", "0").strip() == "1"
        self._client        = None

    def _get_client(self):
        if self._client:
            return self._client
        if not self.private_key:
            return None
        try:
            from py_clob_client.client import ClobClient
            from py_clob_client.clob_types import ApiCreds
            creds = None
            if self.api_key:
                creds = ApiCreds(
                    api_key=self.api_key,
                    api_secret=self.api_secret,
                    api_passphrase=self.api_passphrase,
                )
            self._client = ClobClient(
                host=CLOB_API,
                key=self.private_key,
                chain_id=CHAIN_ID,
                signature_type=1 if creds else 0,
                creds=creds,
            )
        except ImportError:
            pass
        return self._client

    def place_limit_order(
        self,
        token_id: str,
        side: str,         # "BUY" or "SELL"
        price: float,      # limit price 0–1
        size: float,       # USDC amount
    ) -> dict:
        """
        Place a limit order. Returns order receipt dict.
        In dry-run mode returns a simulated receipt without touching the API.
        """
        receipt = {
            "token_id": token_id,
            "side": side,
            "price": price,
            "size": size,
            "timestamp": int(time.time()),
            "dry_run": not self.live,
        }
        if not self.live:
            receipt["status"] = "dry_run"
            receipt["order_id"] = f"DRY-{int(time.time())}"
            return receipt

        client = self._get_client()
        if not client:
            receipt["status"] = "error"
            receipt["error"] = "No PW_PRIVATE_KEY set"
            return receipt

        try:
            from py_clob_client.order_builder.constants import BUY, SELL
            from py_clob_client.clob_types import OrderArgs, PartialCreateOrderOptions

            order_args = OrderArgs(
                token_id=token_id,
                price=price,
                size=size,
                side=BUY if side.upper() == "BUY" else SELL,
            )
            signed_order = client.create_order(order_args)
            resp = client.post_order(signed_order)
            receipt["status"]   = resp.get("status", "submitted")
            receipt["order_id"] = resp.get("orderID", "")
        except Exception as exc:
            receipt["status"] = "error"
            receipt["error"]  = str(exc)
        return receipt

    def get_positions(self) -> list[dict]:
        """List open positions from the authenticated account."""
        client = self._get_client()
        if not client:
            return []
        try:
            return client.get_positions() or []
        except Exception:
            return []

    def cancel_order(self, order_id: str) -> bool:
        client = self._get_client()
        if not client:
            return False
        try:
            client.cancel_order({"orderID": order_id})
            return True
        except Exception:
            return False
