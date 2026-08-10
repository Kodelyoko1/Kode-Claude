"""
Free intraday price feed for Gold (GC) and WTI Crude Oil (CL) futures.

No API key required — pulls the public Yahoo Finance chart endpoint (the
same JSON the finance.yahoo.com chart widget itself calls). Results are
cached to disk so repeated cycles inside the same killzone don't hammer
the endpoint; a cache entry is reused until it's older than its interval's
`_MAX_AGE` bucket.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Optional

import requests

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
from autonomous import storage

YF_SYMBOLS = {"GC": "GC=F", "CL": "CL=F"}

YF_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

# How long a cached fetch is considered fresh, per interval.
_MAX_AGE_SEC = {
    "1m": 90,
    "5m": 4 * 60,
    "15m": 10 * 60,
}
_RANGE_FOR_INTERVAL = {
    "1m": "1d",
    "5m": "5d",
    "15m": "1mo",
}


class FeedError(Exception):
    pass


def _http_hint(status: int) -> str:
    """Turn an HTTP status into an actionable explanation. Yahoo's chart
    endpoint fails in several distinct ways and they need different fixes."""
    return {
        401: "Yahoo requires a consent cookie for this region/IP — commonly "
             "seen from datacentre IPs; usually works from a home connection.",
        403: "Blocked (firewall/proxy, or Yahoo rejected the User-Agent).",
        404: "Symbol not found at Yahoo — check YF_SYMBOLS mapping.",
        429: "Rate limited by Yahoo — the on-disk cache should cover this; "
             "retry in a few minutes.",
    }.get(status, f"HTTP {status} from Yahoo.")


def _fetch_from_yahoo(asset: str, interval: str) -> list[dict]:
    symbol = YF_SYMBOLS.get(asset)
    if not symbol:
        raise FeedError(f"Unknown asset '{asset}' — expected GC or CL")

    params = {"interval": interval, "range": _RANGE_FOR_INTERVAL.get(interval, "5d")}
    payload = None
    errors: list[str] = []

    # query1 and query2 are mirrors; one is sometimes up when the other isn't.
    for host in ("query1", "query2"):
        url = f"https://{host}.finance.yahoo.com/v8/finance/chart/{symbol}"
        try:
            resp = requests.get(url, params=params, headers=YF_HEADERS, timeout=20)
        except requests.RequestException as exc:
            errors.append(f"{host}: network error ({type(exc).__name__})")
            continue
        if resp.status_code != 200:
            errors.append(f"{host}: {_http_hint(resp.status_code)}")
            continue
        try:
            payload = resp.json()
            break
        except ValueError:
            errors.append(f"{host}: response was not JSON "
                          f"(got {resp.headers.get('content-type', '?')})")

    if payload is None:
        raise FeedError(f"{symbol} {interval} — all endpoints failed. " + " | ".join(errors))

    result = (payload.get("chart") or {}).get("result") or []
    if not result:
        err = (payload.get("chart") or {}).get("error")
        raise FeedError(f"No chart data for {symbol}: {err}")

    r = result[0]
    timestamps = r.get("timestamp") or []
    quote = ((r.get("indicators") or {}).get("quote") or [{}])[0]
    opens = quote.get("open") or []
    highs = quote.get("high") or []
    lows = quote.get("low") or []
    closes = quote.get("close") or []
    vols = quote.get("volume") or []

    candles = []
    for i, ts in enumerate(timestamps):
        o, h, l, c = (
            opens[i] if i < len(opens) else None,
            highs[i] if i < len(highs) else None,
            lows[i] if i < len(lows) else None,
            closes[i] if i < len(closes) else None,
        )
        if None in (o, h, l, c):
            continue  # Yahoo pads incomplete/pre-market bars with nulls
        candles.append({
            "t": int(ts),
            "o": float(o),
            "h": float(h),
            "l": float(l),
            "c": float(c),
            "v": float(vols[i]) if i < len(vols) and vols[i] is not None else 0.0,
        })

    if not candles:
        raise FeedError(
            f"{symbol} {interval}: Yahoo returned {len(timestamps)} timestamps but no "
            f"usable OHLC bars (all null). Market may be closed, or the JSON shape changed."
        )
    return candles


def get_candles(asset: str, interval: str = "15m", force: bool = False) -> list[dict]:
    """
    Return OHLC candles for `asset` ("GC" or "CL") at `interval`
    ("1m"/"5m"/"15m"), newest last. Falls back to the last good cache on
    a fetch failure so a transient Yahoo outage doesn't blank a killzone.
    """
    cached = storage.load(f"ip_candles/{asset}_{interval}.json", {})
    age = time.time() - cached.get("fetched_at", 0)
    max_age = _MAX_AGE_SEC.get(interval, 5 * 60)

    if not force and cached.get("candles") and age < max_age:
        return cached["candles"]

    try:
        candles = _fetch_from_yahoo(asset, interval)
        if candles:
            storage.save(f"ip_candles/{asset}_{interval}.json", {
                "fetched_at": time.time(),
                "candles": candles,
            })
            return candles
    except Exception as exc:
        if cached.get("candles"):
            # Serve stale cache rather than fail the whole cycle.
            return cached["candles"]
        raise FeedError(f"{asset} {interval} fetch failed and no cache available: {exc}") from exc

    if cached.get("candles"):
        return cached["candles"]
    raise FeedError(f"{asset} {interval}: Yahoo returned no candles")


def latest_price(asset: str) -> Optional[float]:
    try:
        candles = get_candles(asset, "1m")
    except FeedError:
        return None
    return candles[-1]["c"] if candles else None
