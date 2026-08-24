"""
Kalshi API client — restricted to CFTC-compliant non-sports/macro markets
(interest rates, CPI/inflation, weather, and similar economic-data
contracts). Kalshi does not list NFL/CFB game-outcome contracts, and this
client enforces that boundary defensively even so: every fetched market is
filtered through _is_sports_market() and dropped if it looks even remotely
like a sports outcome, so a future catalog change on Kalshi's side can never
smuggle a football market into this "macro sleeve."

This is therefore NOT a football-vs-Kalshi arbitrage feed — there is no
matched market to arbitrage against. It's a separate, unrelated strategy
sleeve reported alongside the sportsbook/PrizePicks signals (see report.py).

Public market data requires no credentials. Live order submission is
dry-run by default (LQ_KALSHI_LIVE=1 to arm it) and needs KALSHI_API_KEY_ID
+ KALSHI_PRIVATE_KEY_PATH (RSA key registered with Kalshi) — same
dry-run-unless-explicitly-armed pattern as PW_LIVE_TRADING (polymarket_weather)
and IP_MT5_LIVE (ict_predictor).
"""
from __future__ import annotations

import base64
import os
import re
import time
from datetime import datetime, timezone

import requests

KALSHI_API_BASE = os.environ.get("KALSHI_API_BASE", "https://api.elections.kalshi.com/trade-api/v2")

_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": "WholesaleOmniverse-LAFootballQuant/1.0"})

DEFAULT_CATEGORIES = ["Rates", "Economics", "Climate and Weather"]

_SPORTS_RE = re.compile(
    r'\b(nfl|ncaaf|college football|super bowl|touchdown|quarterback|'
    r'nba|nhl|mlb|world series|playoff game|win the game|cover the spread|'
    r'moneyline|point spread)\b',
    re.IGNORECASE,
)


def _is_sports_market(title: str, subtitle: str = "") -> bool:
    """Defense-in-depth filter — see module docstring. Kalshi shouldn't
    surface sports outcome markets at all, but never trust a single filter."""
    return bool(_SPORTS_RE.search(f"{title} {subtitle}"))


def configured_categories() -> list[str]:
    raw = os.environ.get("LQ_KALSHI_CATEGORIES", ",".join(DEFAULT_CATEGORIES))
    return [c.strip() for c in raw.split(",") if c.strip()]


def fetch_macro_markets(limit: int = 100) -> dict:
    """Fetch open markets in the configured macro categories. Returns
    {"ok": True, "markets": [...]} or {"ok": False, "error": ..., "markets": []}."""
    categories = configured_categories()
    markets: list[dict] = []
    try:
        cursor = None
        for _ in range(5):  # a handful of pages is plenty for the digest sleeve
            params = {"status": "open", "limit": min(limit, 200)}
            if cursor:
                params["cursor"] = cursor
            resp = _SESSION.get(f"{KALSHI_API_BASE}/markets", params=params, timeout=20)
            resp.raise_for_status()
            data = resp.json()
            for m in data.get("markets", []):
                title = m.get("title", "")
                subtitle = m.get("subtitle", "")
                cat = m.get("category", "")
                if categories and cat not in categories:
                    continue
                if _is_sports_market(title, subtitle):
                    continue
                markets.append({
                    "ticker":       m.get("ticker", ""),
                    "title":        title,
                    "category":     cat,
                    "yes_bid":      m.get("yes_bid"),
                    "yes_ask":      m.get("yes_ask"),
                    "no_bid":       m.get("no_bid"),
                    "no_ask":       m.get("no_ask"),
                    "volume_24h":   m.get("volume_24h", 0),
                    "close_time":   m.get("close_time", ""),
                    "fetched_at":   datetime.now(timezone.utc).isoformat(),
                })
                if len(markets) >= limit:
                    break
            cursor = data.get("cursor")
            if not cursor or len(markets) >= limit:
                break
        return {"ok": True, "markets": markets}
    except requests.RequestException as exc:
        return {"ok": False, "error": str(exc), "markets": markets}


class KalshiTrader:
    """Authenticated order placement. Dry-run unless LQ_KALSHI_LIVE=1 AND a
    signing key is available — degrades gracefully otherwise (matches
    polymarket_weather.api_client.PolyMarketTrader)."""

    def __init__(self):
        self.key_id = os.environ.get("KALSHI_API_KEY_ID", "")
        self.key_path = os.environ.get("KALSHI_PRIVATE_KEY_PATH", "")
        self.live = os.environ.get("LQ_KALSHI_LIVE", "0").strip() == "1"
        self._private_key = None

    def _load_key(self):
        if self._private_key is not None:
            return self._private_key
        if not self.key_path or not os.path.exists(self.key_path):
            return None
        try:
            from cryptography.hazmat.primitives import serialization
            with open(self.key_path, "rb") as f:
                self._private_key = serialization.load_pem_private_key(f.read(), password=None)
        except ImportError:
            self._private_key = None
        except Exception:
            self._private_key = None
        return self._private_key

    def _signed_headers(self, method: str, path: str) -> dict:
        key = self._load_key()
        if not key:
            return {}
        try:
            from cryptography.hazmat.primitives import hashes
            from cryptography.hazmat.primitives.asymmetric import padding
            ts_ms = str(int(time.time() * 1000))
            msg = f"{ts_ms}{method}{path}".encode("utf-8")
            sig = key.sign(
                msg,
                padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
                hashes.SHA256(),
            )
            return {
                "KALSHI-ACCESS-KEY": self.key_id,
                "KALSHI-ACCESS-SIGNATURE": base64.b64encode(sig).decode(),
                "KALSHI-ACCESS-TIMESTAMP": ts_ms,
            }
        except Exception:
            return {}

    def place_order(self, ticker: str, side: str, count: int, price_cents: int) -> dict:
        """Place a limit order. Returns a receipt; dry-run unless armed and
        fully credentialed, mirroring every other live-money agent in this repo."""
        receipt = {
            "ticker": ticker, "side": side, "count": count,
            "price_cents": price_cents, "timestamp": int(time.time()),
            "dry_run": not self.live,
        }
        if not self.live:
            receipt["status"] = "dry_run"
            receipt["order_id"] = f"DRY-{int(time.time())}"
            return receipt

        headers = self._signed_headers("POST", "/trade-api/v2/portfolio/orders")
        if not headers or not self.key_id:
            receipt["status"] = "error"
            receipt["error"] = "LQ_KALSHI_LIVE=1 but no valid KALSHI_API_KEY_ID/KALSHI_PRIVATE_KEY_PATH"
            return receipt

        try:
            resp = _SESSION.post(
                f"{KALSHI_API_BASE}/portfolio/orders",
                json={
                    "ticker": ticker, "action": "buy", "side": side,
                    "count": count, "type": "limit",
                    "yes_price" if side == "yes" else "no_price": price_cents,
                },
                headers=headers, timeout=20,
            )
            resp.raise_for_status()
            body = resp.json()
            receipt["status"] = "submitted"
            receipt["order_id"] = body.get("order", {}).get("order_id", "")
        except requests.RequestException as exc:
            receipt["status"] = "error"
            receipt["error"] = str(exc)
        return receipt
