"""
polymarket_weather/resolver.py
Collects real resolved PolyMarket weather market data to improve model accuracy.

Flow:
  1. Fetch closed/resolved weather markets from Gamma API
  2. Extract YES/NO outcome from final token prices
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

GAMMA_API     = "https://gamma-api.polymarket.com"
OPEN_METEO    = "https://archive-api.open-meteo.com/v1/archive"

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
    Checks token final prices (≥ 0.95 = winner).
    """
    tokens = market.get("tokens") or []
    for tok in tokens:
        outcome_str = str(tok.get("outcome", "")).upper()
        price = float(tok.get("price", 0) or 0)
        if price >= 0.95:
            return 1 if outcome_str == "YES" else 0

    # Fallback: outcomePrices list [yes_price, no_price]
    prices_raw = market.get("outcomePrices") or []
    try:
        prices = [float(p) for p in prices_raw]
        if len(prices) >= 2:
            if prices[0] >= 0.95:
                return 1
            if prices[1] >= 0.95:
                return 0
    except (ValueError, TypeError):
        pass

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
    """Extract YYYY-MM-DD from endDate / resolutionTime fields."""
    for field in ("endDate", "resolutionTime", "end_date_iso"):
        val = market.get(field)
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
    Fetch recently closed weather markets, extract outcomes, align weather, save.

    Returns:
        {"new_records": int, "total_records": int, "errors": int, "markets_checked": int}
    """
    seen = _load_seen_ids()
    new_records = 0
    errors      = 0
    checked     = 0

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days_back)).strftime("%Y-%m-%d")

    # Fetch closed markets from Gamma API
    try:
        resp = requests.get(
            f"{GAMMA_API}/markets",
            params={
                "closed":    "true",
                "limit":     200,
                "active":    "false",
                "order":     "endDate",
                "ascending": "false",
            },
            timeout=20,
        )
        resp.raise_for_status()
        markets = resp.json()
        if isinstance(markets, dict):
            markets = markets.get("markets") or markets.get("data") or []
    except Exception as exc:
        return {"new_records": 0, "total_records": _count_total(), "errors": 1,
                "markets_checked": 0, "error": str(exc)}

    records_batch: list[dict] = []

    for mkt in markets:
        checked += 1
        mkt_id   = str(mkt.get("id") or mkt.get("conditionId") or "")
        question = str(mkt.get("question") or "")

        # Skip non-weather markets
        if not any(kw in question.lower() for kw in WEATHER_KEYWORDS):
            continue

        # Skip already collected
        if mkt_id and mkt_id in seen:
            continue

        # Skip markets that resolved before the cutoff
        res_date = _resolution_date(mkt)
        if res_date and res_date < cutoff:
            continue

        outcome = _extract_outcome(mkt)
        if outcome is None:
            continue

        city = _detect_city(question)
        if not city:
            continue

        weather = _align_weather(city, res_date) if res_date else None

        record: dict = {
            "market_id":   mkt_id,
            "question":    question,
            "city":        city,
            "outcome":     outcome,
            "date":        res_date,
            "market_price": float(mkt.get("bestBid") or mkt.get("lastTradedPrice") or 0.5),
            "collected_at": datetime.now(timezone.utc).isoformat(),
        }
        if weather:
            record.update(weather)

        records_batch.append(record)
        if mkt_id:
            seen.add(mkt_id)
        new_records += 1

        # Gentle rate limiting
        time.sleep(0.2)

    # Append to JSONL file
    if records_batch:
        with RESOLVED_FILE.open("a") as fh:
            for rec in records_batch:
                fh.write(json.dumps(rec) + "\n")
        _save_seen_ids(seen)

    total = _count_total()
    return {
        "new_records":    new_records,
        "total_records":  total,
        "errors":         errors,
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
