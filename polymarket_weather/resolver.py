"""
polymarket_weather/resolver.py
Collects resolved Kalshi weather market data to improve model accuracy.

Flow:
  1. Fetch settled weather markets from Kalshi (WEATHER_SERIES, status=settled)
  2. Extract YES/NO outcome from market.result field
  3. Align each market with historical Open-Meteo weather for city + date
  4. Save labeled records to data/pw_resolved/resolved.jsonl for training
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import requests

ROOT         = Path(__file__).parent.parent
RESOLVED_DIR = ROOT / "data" / "pw_resolved"
RESOLVED_DIR.mkdir(parents=True, exist_ok=True)

RESOLVED_FILE = RESOLVED_DIR / "resolved.jsonl"
SEEN_IDS_FILE = RESOLVED_DIR / "seen_ids.json"

KALSHI_BASE   = "https://trading-api.kalshi.com/trade-api/v2"
OPEN_METEO    = "https://archive-api.open-meteo.com/v1/archive"

WEATHER_SERIES = ["KXHIGHTEMP", "KXLOWTEMP", "KXRAIN", "KXSNOW", "KXWIND"]

# Weather-related keywords to filter markets
WEATHER_KEYWORDS = [
    "temperature", "temp", "rain", "rainfall", "precipitation",
    "snow", "wind", "hurricane", "tornado", "flood", "heat",
    "cold", "freeze", "frost", "storm", "weather",
]

# City name → lat/lon mapping (matches data_pipeline.py CITY_COORDS)
CITY_COORDS: dict[str, tuple[float, float]] = {
    "new_york":      (40.7128, -74.0060),
    "chicago":       (41.8781, -87.6298),
    "miami":         (25.7617, -80.1918),
    "atlanta":       (33.7490, -84.3880),
    "dallas":        (32.7767, -96.7970),
    "los_angeles":   (34.0522, -118.2437),
    "houston":       (29.7604, -95.3698),
    "phoenix":       (33.4484, -112.0740),
    "philadelphia":  (39.9526, -75.1652),
    "seattle":       (47.6062, -122.3321),
}

CITY_ALIASES: dict[str, str] = {
    "new york":    "new_york",
    "los angeles": "los_angeles",
    "la":          "los_angeles",
    "nyc":         "new_york",
    "chicago":     "chicago",
    "miami":       "miami",
    "atlanta":     "atlanta",
    "dallas":      "dallas",
    "houston":     "houston",
    "phoenix":     "phoenix",
    "philly":      "philadelphia",
    "philadelphia":"philadelphia",
    "seattle":     "seattle",
}


# ---------------------------------------------------------------------------
# ID tracking (deduplication)
# ---------------------------------------------------------------------------

def _load_seen_ids() -> set[str]:
    if SEEN_IDS_FILE.exists():
        try:
            return set(json.loads(SEEN_IDS_FILE.read_text()))
        except Exception:
            pass
    return set()


def _save_seen_ids(seen: set[str]) -> None:
    SEEN_IDS_FILE.write_text(json.dumps(sorted(seen)))


# ---------------------------------------------------------------------------
# Outcome extraction
# ---------------------------------------------------------------------------

def _extract_outcome(market: dict) -> Optional[int]:
    """
    Return 1 if YES won, 0 if NO won, None if undetermined.
    Kalshi provides an explicit `result` field: "yes", "no", or "" (open).
    """
    result = str(market.get("result") or "").lower().strip()
    if result == "yes":
        return 1
    if result == "no":
        return 0
    return None


# ---------------------------------------------------------------------------
# City detection
# ---------------------------------------------------------------------------

def _detect_city(text: str) -> Optional[str]:
    """Return normalized city key (e.g. 'new_york') from market question text."""
    lower = text.lower()
    for alias, key in CITY_ALIASES.items():
        # word-boundary match
        pattern = r"\b" + re.escape(alias) + r"\b"
        if re.search(pattern, lower):
            return key
    return None


# ---------------------------------------------------------------------------
# Weather alignment
# ---------------------------------------------------------------------------

def _align_weather(city: str, date_str: str) -> Optional[dict]:
    """
    Fetch one day of historical weather from Open-Meteo for city + date.
    Returns dict with temperature_2m_max, precipitation_sum, windspeed_10m_max.
    """
    if city not in CITY_COORDS:
        return None

    lat, lon = CITY_COORDS[city]
    params = {
        "latitude":  lat,
        "longitude": lon,
        "start_date": date_str,
        "end_date":   date_str,
        "daily":      "temperature_2m_max,precipitation_sum,windspeed_10m_max",
        "timezone":   "America/New_York",
        "temperature_unit": "fahrenheit",
    }
    try:
        resp = requests.get(OPEN_METEO, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        daily = data.get("daily", {})
        return {
            "temperature_2m_max":  (daily.get("temperature_2m_max") or [None])[0],
            "precipitation_sum":   (daily.get("precipitation_sum")  or [None])[0],
            "windspeed_10m_max":   (daily.get("windspeed_10m_max")  or [None])[0],
        }
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Market resolution date
# ---------------------------------------------------------------------------

def _resolution_date(market: dict) -> Optional[str]:
    """Extract YYYY-MM-DD from Kalshi close_time / expected_expiration_time fields."""
    for key in ("close_time", "expected_expiration_time", "expiration_time",
                "endDate", "resolutionTime", "end_date_iso"):
        val = market.get(key)
        if val:
            try:
                dt = datetime.fromisoformat(str(val).replace("Z", "+00:00"))
                return dt.strftime("%Y-%m-%d")
            except Exception:
                continue
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def collect_new_resolutions(days_back: int = 60) -> dict:
    """
    Fetch recently settled Kalshi weather markets, extract outcomes, align weather, save.

    Returns:
        {"new_records": int, "total_records": int, "errors": int, "markets_checked": int}
    """
    seen = _load_seen_ids()
    new_records = 0
    errors      = 0
    checked     = 0

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days_back)).strftime("%Y-%m-%d")

    all_markets: list[dict] = []

    # Fetch settled markets from Kalshi for each weather series
    for series in WEATHER_SERIES:
        try:
            resp = requests.get(
                f"{KALSHI_BASE}/markets",
                params={"series_ticker": series, "status": "settled", "limit": 200},
                timeout=20,
            )
            resp.raise_for_status()
            data = resp.json()
            rows = data.get("markets", data) if isinstance(data, dict) else data
            all_markets.extend(rows)
        except Exception as exc:
            errors += 1
            continue

    if not all_markets and errors:
        return {"new_records": 0, "total_records": _count_total(), "errors": errors,
                "markets_checked": 0}

    records_batch: list[dict] = []

    for mkt in all_markets:
        checked += 1
        mkt_id   = str(mkt.get("ticker") or "")
        title    = str(mkt.get("title") or mkt.get("subtitle") or mkt_id)

        # Skip already collected
        if mkt_id and mkt_id in seen:
            continue

        # Resolve date from close_time or expected_expiration_time
        res_date = _resolution_date(mkt)
        if res_date and res_date < cutoff:
            continue

        outcome = _extract_outcome(mkt)
        if outcome is None:
            continue

        # City from ticker first, then title text
        from polymarket_weather.api_client import (
            extract_city_from_ticker, KALSHI_LOC_MAP
        )
        city = extract_city_from_ticker(mkt_id) or _detect_city(title)
        if not city:
            continue

        weather = _align_weather(city, res_date) if res_date else None

        # Market price at settlement (last traded price in cents → 0-1)
        last_price_cents = mkt.get("last_price") or 50
        market_price = float(last_price_cents) / 100.0

        record: dict = {
            "market_id":    mkt_id,
            "question":     title,
            "city":         city,
            "outcome":      outcome,
            "date":         res_date,
            "market_price": market_price,
            "collected_at": datetime.now(timezone.utc).isoformat(),
        }
        if weather:
            record.update(weather)

        records_batch.append(record)
        if mkt_id:
            seen.add(mkt_id)
        new_records += 1

        time.sleep(0.1)   # gentle pacing

    if records_batch:
        with RESOLVED_FILE.open("a") as fh:
            for rec in records_batch:
                fh.write(json.dumps(rec) + "\n")
        _save_seen_ids(seen)

    total = _count_total()
    return {
        "new_records":     new_records,
        "total_records":   total,
        "errors":          errors,
        "markets_checked": checked,
    }


def load_resolved_for_training() -> list[dict]:
    """
    Load all resolved records that have weather features, ready for model.fit().
    Returns list of dicts; each dict has at least: outcome, temperature_2m_max.
    """
    if not RESOLVED_FILE.exists():
        return []
    records = []
    with RESOLVED_FILE.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                # Only include records with at least one weather feature
                if rec.get("temperature_2m_max") is not None:
                    records.append(rec)
            except Exception:
                continue
    return records


def resolved_count() -> int:
    """Return the number of saved resolved records with weather data."""
    return len(load_resolved_for_training())


def _count_total() -> int:
    """Count all lines in the JSONL file (including records without weather)."""
    if not RESOLVED_FILE.exists():
        return 0
    try:
        with RESOLVED_FILE.open() as fh:
            return sum(1 for line in fh if line.strip())
    except Exception:
        return 0
