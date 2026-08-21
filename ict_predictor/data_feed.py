"""
Intraday price feed for Gold (GC) and WTI Crude Oil (CL).

TWO SOURCES, and which one is used matters enormously:

  1. MT5 (preferred whenever a terminal is connected) — bars for the exact
     symbol the agent will place orders on.
  2. Yahoo Finance chart JSON (fallback, no API key) — COMEX futures
     (GC=F / CL=F).

These are NOT interchangeable. MT5 brokers typically quote *spot* gold
(XAUUSD) while Yahoo's GC=F is the *futures* contract, and the two differ
by the cost of carry — observed at ~62 points (1.44%) on a live demo
account. Computing an entry/stop/target from futures prices and then
submitting that order against spot produces levels on the wrong side of the
market: a BUY_LIMIT lands above spot and fills instantly or is rejected.

So: analyse the instrument you trade. When MT5 is connected the candles
come from MT5 for the configured broker symbol. Yahoo is used only when
MT5 is unavailable (e.g. analysis-only runs on Linux), and in that mode
the levels are indicative, not order-ready.

Results are cached to disk so repeated cycles inside a killzone don't
hammer either source.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Optional

import requests

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
from autonomous import storage
from ict_predictor import killzone

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

# How many bars to pull from MT5 per interval. The 15M frame needs enough
# history for swing-point/liquidity mapping; the precision frame less so.
_MT5_BAR_COUNT = {"1m": 500, "5m": 500, "15m": 400}

# "auto" (default) = MT5 when connected, else Yahoo. "mt5"/"yahoo" force one.
PRICE_SOURCE = os.getenv("IP_PRICE_SOURCE", "auto").strip().lower()

# Hard ceiling on how old a cached series may be before we refuse to trade off
# it. 20 minutes spans a transient outage without letting the agent reason
# about a market that has since moved.
MAX_STALE_SEC = int(os.getenv("IP_MAX_STALE_SEC", "1200"))

# How far behind the live market the newest MT5 BAR may be before the series is
# refused. This is a different failure from a stale disk cache: the terminal
# answers immediately and confidently with history it simply hasn't finished
# downloading. Observed on a freshly selected symbol - a live XAUUSD bid of
# 4,612.98 alongside a newest 15M close of 4,324.56, 6.2% apart. Set to 0 to
# disable the check.
MAX_BAR_AGE_SEC = int(os.getenv("IP_MT5_MAX_BAR_AGE_SEC", "0")) or None


class FeedError(Exception):
    pass


_INTERVAL_SEC = {"1m": 60, "5m": 300, "15m": 900}


def _round_offset(raw_seconds: float) -> int:
    """Round a raw offset to whole hours; 0 if implausible (>|14h|), so a bad
    estimate can never corrupt timestamps."""
    hours = round(raw_seconds / 3600.0)
    return 0 if abs(hours) > 14 else int(hours * 3600)


def _server_utc_offset(last_bar_time: int, interval: str) -> int:
    """
    Estimate the broker server's offset from UTC from the newest bar.

    ONLY valid when that bar really is the one currently forming. It cannot
    tell "the server is UTC+3" apart from "these bars are 3 hours stale" -
    both look identical from the bar alone, and the stale case silently
    shifts every timestamp in the series. Prefer _tick_utc_offset(); this
    stays as the fallback for when no tick is available.
    """
    step = _INTERVAL_SEC.get(interval, 900)
    return _round_offset(last_bar_time - (time.time() - step))


def _tick_utc_offset(tick_time: int) -> int:
    """
    Offset from a live tick, which is stamped in server time like the bars but,
    unlike them, is current by definition. This breaks the ambiguity above:
    with the offset known independently, bar staleness becomes measurable.
    """
    return _round_offset(tick_time - time.time())


def _max_bar_age(interval: str) -> int:
    """Grace before the newest bar counts as stale. Two intervals covers a bar
    that has not closed yet plus one still in flight; the 10-minute floor keeps
    the 1M frame from tripping on ordinary quiet stretches."""
    if MAX_BAR_AGE_SEC:
        return MAX_BAR_AGE_SEC
    return max(2 * _INTERVAL_SEC.get(interval, 900), 600)


def _fetch_from_mt5(asset: str, interval: str) -> list[dict]:
    """
    Pull bars straight from the MT5 terminal for the symbol we actually
    trade. Raises FeedError if MT5 isn't usable so callers can fall back.
    """
    from ict_predictor import mt5_execution

    if not mt5_execution.MT5_PACKAGE_AVAILABLE:
        raise FeedError("MetaTrader5 package not installed")

    import MetaTrader5 as mt5

    timeframes = {
        "1m": mt5.TIMEFRAME_M1,
        "5m": mt5.TIMEFRAME_M5,
        "15m": mt5.TIMEFRAME_M15,
    }
    if interval not in timeframes:
        raise FeedError(f"MT5: unsupported interval '{interval}'")

    symbol = mt5_execution.symbol_for(asset)
    if not mt5_execution._connect():
        raise FeedError("MT5 terminal not connected")

    try:
        # Must be in Market Watch before it will return bars.
        mt5.symbol_select(symbol, True)
        rates = mt5.copy_rates_from_pos(
            symbol, timeframes[interval], 0, _MT5_BAR_COUNT.get(interval, 400)
        )
        if rates is None or len(rates) == 0:
            raise FeedError(f"MT5: no bars for {symbol} {interval} "
                            f"({mt5.last_error()})")
        # MT5 stamps bars in BROKER SERVER time, not UTC (MetaQuotes servers
        # run EET/EEST = UTC+2/+3). Left raw, every time shown in a report is
        # off by that offset.
        #
        # Take the offset from a live tick rather than the newest bar. The tick
        # is current by definition, so it pins the offset without assuming the
        # bars are up to date - and with the offset pinned independently, how
        # far behind the bars are becomes a measurable number instead of being
        # absorbed into the offset.
        last_bar_server = int(rates[-1]["time"])
        tick = mt5.symbol_info_tick(symbol)
        tick_time = int(getattr(tick, "time", 0) or 0)
        if tick_time:
            offset = _tick_utc_offset(tick_time)
            bar_lag = tick_time - last_bar_server
        else:
            offset = _server_utc_offset(last_bar_server, interval)
            bar_lag = 0

        # Refuse stale history rather than analysing it. Only while the market
        # is open: over a weekend the newest bar is legitimately hours old and
        # that is not a fault.
        limit = _max_bar_age(interval)
        if bar_lag > limit and killzone.market_is_open():
            raise FeedError(
                f"MT5: {symbol} {interval} history is {bar_lag/60:.0f} min "
                f"behind the live tick (limit {limit/60:.0f} min). The terminal "
                f"has not finished downloading bars for this symbol. Open its "
                f"chart in MT5 and let the history load, then re-run.")
        return [{
            "t": int(r["time"]) - offset,
            "o": float(r["open"]),
            "h": float(r["high"]),
            "l": float(r["low"]),
            "c": float(r["close"]),
            "v": float(r["tick_volume"]),
        } for r in rates]
    finally:
        mt5_execution._disconnect()


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


def get_history(asset: str, interval: str, bars: int) -> list[dict]:
    """
    Deep history for backtesting — bypasses the live cache and pulls as many
    bars as MT5 will give. Only MT5 is supported here: Yahoo's intraday
    windows are far too short to backtest, and its futures prices are the
    wrong instrument anyway.
    """
    from ict_predictor import mt5_execution

    if not mt5_execution.MT5_PACKAGE_AVAILABLE:
        raise FeedError(
            "Backtesting needs MetaTrader5 for deep history (Windows only). "
            "Yahoo's intraday range is too short and quotes futures, not the "
            "spot symbol you trade.")

    import MetaTrader5 as mt5
    timeframes = {"1m": mt5.TIMEFRAME_M1, "5m": mt5.TIMEFRAME_M5,
                  "15m": mt5.TIMEFRAME_M15}
    if interval not in timeframes:
        raise FeedError(f"unsupported interval '{interval}'")

    symbol = mt5_execution.symbol_for(asset)
    if not mt5_execution._connect():
        raise FeedError("MT5 terminal not connected")
    try:
        mt5.symbol_select(symbol, True)

        # Clamp to the terminal's own max-bars setting (Tools > Options >
        # Charts > "Max bars in chart", commonly 100k). Asking for more does
        # not return fewer bars — copy_rates_from_pos fails outright with
        # -2 "Invalid params", which reads like a bug in the call rather than
        # a limit being exceeded.
        maxbars = 0
        try:
            info = mt5.terminal_info()
            maxbars = int(getattr(info, "maxbars", 0) or 0)
        except Exception:
            pass
        limit = min(bars, maxbars) if maxbars else min(bars, 100_000)
        requested, bars = bars, max(limit, 100)

        rates = mt5.copy_rates_from_pos(symbol, timeframes[interval], 0, bars)

        # Some builds still refuse near the ceiling; back off rather than fail.
        attempt = bars
        while (rates is None or len(rates) == 0) and attempt > 1000:
            attempt //= 2
            rates = mt5.copy_rates_from_pos(symbol, timeframes[interval], 0, attempt)

        if rates is None or len(rates) == 0:
            raise FeedError(
                f"no history for {symbol} {interval}: {mt5.last_error()}. "
                f"Requested {requested:,} bars (clamped to {bars:,}"
                f"{f', terminal maxbars={maxbars:,}' if maxbars else ''}). "
                f"The symbol may also simply have no data at this timeframe — "
                f"open its chart in MT5 once to make the terminal download history.")
        offset = _server_utc_offset(int(rates[-1]["time"]), interval)
        return [{"t": int(r["time"]) - offset, "o": float(r["open"]),
                 "h": float(r["high"]), "l": float(r["low"]),
                 "c": float(r["close"]), "v": float(r["tick_volume"])}
                for r in rates]
    finally:
        mt5_execution._disconnect()


def active_source() -> str:
    """Which feed will actually be used: 'mt5' or 'yahoo'."""
    if PRICE_SOURCE in ("mt5", "yahoo"):
        return PRICE_SOURCE
    try:
        from ict_predictor import mt5_execution
        return "mt5" if mt5_execution.MT5_PACKAGE_AVAILABLE else "yahoo"
    except Exception:
        return "yahoo"


def get_candles(asset: str, interval: str = "15m", force: bool = False) -> list[dict]:
    """
    Return OHLC candles for `asset` ("GC" or "CL") at `interval`
    ("1m"/"5m"/"15m"), newest last.

    Prefers MT5 (the instrument actually traded); falls back to Yahoo
    futures only when MT5 is unusable. Cached per source — the two are
    different instruments and must never be mixed in one series. Serves a
    stale cache rather than failing a killzone on a transient outage.
    """
    source = active_source()
    key = f"ip_candles/{asset}_{interval}_{source}.json"
    cached = storage.load(key, {})
    age = time.time() - cached.get("fetched_at", 0)
    max_age = _MAX_AGE_SEC.get(interval, 5 * 60)

    if not force and cached.get("candles") and age < max_age:
        return cached["candles"]

    errors = []
    # Ordered attempts. "auto" may fall through MT5 -> Yahoo; an explicit
    # setting never silently switches instruments.
    if PRICE_SOURCE == "mt5":
        attempts = [("mt5", _fetch_from_mt5)]
    elif PRICE_SOURCE == "yahoo":
        attempts = [("yahoo", _fetch_from_yahoo)]
    else:
        attempts = [("mt5", _fetch_from_mt5), ("yahoo", _fetch_from_yahoo)]

    for name, fetch in attempts:
        try:
            candles = fetch(asset, interval)
        except Exception as exc:
            errors.append(f"{name}: {exc}")
            continue
        if candles:
            storage.save(f"ip_candles/{asset}_{interval}_{name}.json", {
                "fetched_at": time.time(),
                "source": name,
                "candles": candles,
            })
            return candles
        errors.append(f"{name}: returned no candles")

    # A stale cache is better than nothing for a brief outage, but analysing
    # hours-old bars as if they were live produces confident, wrong levels.
    # Beyond MAX_STALE_SEC, refuse instead.
    if cached.get("candles"):
        if age <= MAX_STALE_SEC:
            return cached["candles"]
        raise FeedError(
            f"{asset} {interval}: live fetch failed and cache is "
            f"{age/60:.0f} min old (limit {MAX_STALE_SEC/60:.0f} min) — refusing "
            f"to analyse stale bars. Sources tried: " + " | ".join(errors))
    raise FeedError(f"{asset} {interval} fetch failed and no cache available: "
                    + " | ".join(errors))


def latest_price(asset: str) -> Optional[float]:
    try:
        candles = get_candles(asset, "1m")
    except FeedError:
        return None
    return candles[-1]["c"] if candles else None
