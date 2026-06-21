"""
Data pipeline — fetches and aligns weather + PolyMarket data.

Weather source: Open-Meteo (https://open-meteo.com) — completely free, no API key.
  Historical: archive-api.open-meteo.com/v1/archive
  Forecast:   api.open-meteo.com/v1/forecast

Market data: PolyMarket Gamma API + CLOB API (public, no key).

Stores everything as JSON in data/pw_historical/.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import requests

from polymarket_weather.api_client import (
    get_weather_markets,
    get_price_history,
    get_midpoint_price,
    Market,
)

ROOT     = Path(__file__).parent.parent
DATA_DIR = ROOT / "data" / "pw_historical"
DATA_DIR.mkdir(parents=True, exist_ok=True)

OPEN_METEO_ARCHIVE  = "https://archive-api.open-meteo.com/v1/archive"
OPEN_METEO_FORECAST = "https://api.open-meteo.com/v1/forecast"

# Weather variables we always request
HOURLY_VARS = [
    "temperature_2m",
    "precipitation",
    "relative_humidity_2m",
    "wind_speed_10m",
    "cloud_cover",
    "pressure_msl",
    "dew_point_2m",
    "apparent_temperature",
]

DAILY_VARS = [
    "temperature_2m_max",
    "temperature_2m_min",
    "precipitation_sum",
    "wind_speed_10m_max",
    "shortwave_radiation_sum",
    "et0_fao_evapotranspiration",
]

# Geocodes for major US cities (lat, lon, elevation_m)
CITY_COORDS: dict[str, tuple[float, float, int]] = {
    "new_york":    (40.7128, -74.0060,  10),
    "los_angeles": (34.0522, -118.2437,  71),
    "chicago":     (41.8781, -87.6298, 181),
    "houston":     (29.7604, -95.3698,  15),
    "phoenix":     (33.4484, -112.0740, 331),
    "philadelphia":(39.9526, -75.1652,  12),
    "san_antonio": (29.4241, -98.4936, 198),
    "dallas":      (32.7767, -96.7970, 139),
    "miami":       (25.7617, -80.1918,   2),
    "atlanta":     (33.7490, -84.3880, 320),
}


# ---------------------------------------------------------------------------
# Weather fetching
# ---------------------------------------------------------------------------

def fetch_historical_weather(
    lat: float,
    lon: float,
    start_date: str,   # "YYYY-MM-DD"
    end_date: str,     # "YYYY-MM-DD"
    hourly: bool = True,
) -> dict:
    """
    Pull historical weather from Open-Meteo archive.
    Returns raw API response dict with 'hourly' and/or 'daily' keys.
    """
    params: dict = {
        "latitude":   lat,
        "longitude":  lon,
        "start_date": start_date,
        "end_date":   end_date,
        "timezone":   "UTC",
    }
    if hourly:
        params["hourly"] = ",".join(HOURLY_VARS)
    params["daily"] = ",".join(DAILY_VARS)

    resp = requests.get(OPEN_METEO_ARCHIVE, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def fetch_forecast_weather(lat: float, lon: float, days: int = 16) -> dict:
    """Pull up to 16-day forecast from Open-Meteo."""
    params = {
        "latitude":   lat,
        "longitude":  lon,
        "hourly":     ",".join(HOURLY_VARS),
        "daily":      ",".join(DAILY_VARS),
        "timezone":   "UTC",
        "forecast_days": min(days, 16),
    }
    resp = requests.get(OPEN_METEO_FORECAST, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def build_daily_weather_df(raw: dict) -> list[dict]:
    """
    Convert Open-Meteo daily response → list of daily dicts suitable for
    feature engineering and model input.
    """
    daily = raw.get("daily", {})
    dates = daily.get("time", [])
    records = []
    for i, date in enumerate(dates):
        row = {"date": date}
        for var in DAILY_VARS:
            vals = daily.get(var, [])
            row[var] = vals[i] if i < len(vals) else None
        records.append(row)
    return records


def save_historical_weather(city: str, records: list[dict]) -> Path:
    path = DATA_DIR / f"{city}_weather.json"
    existing = _load_json(path, default=[])
    existing_dates = {r["date"] for r in existing}
    merged = existing + [r for r in records if r["date"] not in existing_dates]
    merged.sort(key=lambda x: x["date"])
    path.write_text(json.dumps(merged, indent=2))
    return path


def load_historical_weather(city: str) -> list[dict]:
    return _load_json(DATA_DIR / f"{city}_weather.json", default=[])


# ---------------------------------------------------------------------------
# PolyMarket market data
# ---------------------------------------------------------------------------

def fetch_and_cache_weather_markets(force: bool = False) -> list[dict]:
    """
    Fetch all PolyMarket weather markets and cache to disk.
    Returns list of serializable dicts.
    """
    cache_path = DATA_DIR / "markets_cache.json"
    if not force and cache_path.exists():
        age = time.time() - cache_path.stat().st_mtime
        if age < 3600:  # 1-hour cache
            return _load_json(cache_path, default=[])

    markets = get_weather_markets(closed=False)
    closed  = get_weather_markets(closed=True)
    all_markets = markets + closed

    serialized = [
        {
            "condition_id": m.condition_id,
            "question":     m.question,
            "slug":         m.slug,
            "end_date":     m.end_date,
            "tokens":       m.tokens,
            "volume":       m.volume,
            "liquidity":    m.liquidity,
            "closed":       m.closed,
            "tags":         m.tags,
        }
        for m in all_markets
    ]
    cache_path.write_text(json.dumps(serialized, indent=2))
    return serialized


def fetch_market_price_history(
    condition_id: str,
    lookback_days: int = 30,
) -> list[dict]:
    """
    Pull hourly price history for a market.
    Returns [{timestamp, price}] list.
    """
    end_ts   = int(time.time())
    start_ts = end_ts - lookback_days * 86400
    points   = get_price_history(condition_id, start_ts, end_ts, fidelity=60)
    return [{"timestamp": p.timestamp, "price": p.price} for p in points]


def save_market_price_history(condition_id: str, records: list[dict]) -> Path:
    path = DATA_DIR / f"mkt_{condition_id[:20]}_prices.json"
    existing = _load_json(path, default=[])
    existing_ts = {r["timestamp"] for r in existing}
    merged = existing + [r for r in records if r["timestamp"] not in existing_ts]
    merged.sort(key=lambda x: x["timestamp"])
    path.write_text(json.dumps(merged, indent=2))
    return path


def load_market_price_history(condition_id: str) -> list[dict]:
    path = DATA_DIR / f"mkt_{condition_id[:20]}_prices.json"
    return _load_json(path, default=[])


# ---------------------------------------------------------------------------
# Data alignment — combine weather features with market prices
# ---------------------------------------------------------------------------

def align_weather_and_market(
    weather_records: list[dict],
    price_history: list[dict],
    market_end_date: str,
    outcome_label: Optional[int] = None,  # 1=YES resolved, 0=NO resolved, None=unknown
) -> list[dict]:
    """
    Join daily weather records with the closest prior market price.
    Returns aligned training rows.

    Each row contains weather features + market_price (implied prob) + outcome.
    """
    # Index prices by day (take the last price before midnight UTC)
    price_by_day: dict[str, float] = {}
    for p in price_history:
        day = datetime.fromtimestamp(p["timestamp"], tz=timezone.utc).strftime("%Y-%m-%d")
        price_by_day[day] = p["price"]

    aligned = []
    for rec in weather_records:
        date = rec["date"]
        if date > market_end_date:
            continue
        market_price = price_by_day.get(date)
        if market_price is None:
            # Forward-fill from the nearest prior date
            prior = [v for k, v in price_by_day.items() if k <= date]
            market_price = prior[-1] if prior else None
        if market_price is None:
            continue

        row = dict(rec)
        row["market_price"]  = market_price
        row["market_end_date"] = market_end_date
        row["outcome"]       = outcome_label
        aligned.append(row)
    return aligned


# ---------------------------------------------------------------------------
# Full pipeline run
# ---------------------------------------------------------------------------

def run_data_pipeline(
    cities: list[str] | None = None,
    lookback_years: int = 3,
    progress_cb = None,
) -> dict:
    """
    Orchestrate full data pull:
      1. Fetch historical weather for each city  (falls back to synthetic on network error)
      2. Fetch all PolyMarket weather markets    (falls back to synthetic on network error)
      3. Fetch price history for each market
      4. Return a summary dict
    """
    cities = cities or list(CITY_COORDS.keys())
    today     = datetime.now(timezone.utc)
    start_str = (today - timedelta(days=365 * lookback_years)).strftime("%Y-%m-%d")
    end_str   = today.strftime("%Y-%m-%d")

    # --- Try live weather data; fall back to synthetic ---
    weather_saved = []
    live_weather_ok = False
    for city in cities[:1]:   # probe with one city
        lat, lon, _ = CITY_COORDS.get(city, (40.71, -74.00, 10))
        try:
            fetch_historical_weather(lat, lon, start_str, end_str)
            live_weather_ok = True
        except Exception:
            break

    if not live_weather_ok:
        if progress_cb:
            progress_cb("Live weather API unavailable — using synthetic data")
        from polymarket_weather.synthetic import generate_full_demo_dataset
        return generate_full_demo_dataset(cities=cities, lookback_years=lookback_years)

    # --- Live path ---
    for city in cities:
        lat, lon, _ = CITY_COORDS.get(city, (40.71, -74.00, 10))
        if progress_cb:
            progress_cb(f"Fetching weather for {city}…")
        try:
            raw     = fetch_historical_weather(lat, lon, start_str, end_str)
            records = build_daily_weather_df(raw)
            path    = save_historical_weather(city, records)
            weather_saved.append({"city": city, "records": len(records), "path": str(path)})
        except Exception as exc:
            weather_saved.append({"city": city, "error": str(exc)})
        time.sleep(0.3)

    if progress_cb:
        progress_cb("Fetching PolyMarket weather markets…")
    markets = fetch_and_cache_weather_markets(force=True)

    price_history_saved = []
    for mkt in markets[:50]:
        cid = mkt.get("condition_id", "")
        if not cid:
            continue
        try:
            history = fetch_market_price_history(cid, lookback_days=180)
            if history:
                save_market_price_history(cid, history)
                price_history_saved.append({"condition_id": cid, "points": len(history)})
        except Exception:
            pass
        time.sleep(0.2)

    return {
        "weather_cities":  len(weather_saved),
        "weather_records": sum(w.get("records", 0) for w in weather_saved),
        "markets_found":   len(markets),
        "price_histories": len(price_history_saved),
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_json(path: Path, default=None):
    if not path.exists():
        return default if default is not None else {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return default if default is not None else {}
