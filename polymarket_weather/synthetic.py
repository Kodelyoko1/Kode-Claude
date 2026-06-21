"""
Synthetic data generator for offline testing and demo mode.

Generates climatologically realistic weather data using seasonal sinusoids
plus realistic random variance — no internet required.

Also generates mock PolyMarket weather markets so the full pipeline
(data → model → backtest → trading agent) can be exercised completely offline.
"""
from __future__ import annotations

import json
import math
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

ROOT     = Path(__file__).parent.parent
DATA_DIR = ROOT / "data" / "pw_historical"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# City climatology: (mean_annual_temp_c, temp_amplitude_c, mean_annual_precip_mm/day)
CITY_CLIMATE = {
    "new_york":     (12.0, 13.0, 3.2),
    "los_angeles":  (17.5,  5.0, 1.2),
    "chicago":      ( 9.5, 16.0, 2.8),
    "houston":      (20.0, 10.0, 3.8),
    "phoenix":      (23.0, 14.0, 0.7),
    "philadelphia": (12.5, 13.5, 3.1),
    "san_antonio":  (20.5, 11.0, 2.4),
    "dallas":       (18.5, 12.5, 2.9),
    "miami":        (24.5,  4.0, 4.5),
    "atlanta":      (16.0, 11.0, 3.4),
}


def generate_synthetic_weather(
    city: str,
    start_date: str,   # "YYYY-MM-DD"
    end_date:   str,   # "YYYY-MM-DD"
    seed: int = 42,
) -> list[dict]:
    """
    Generate daily synthetic weather records for a city.
    Uses a seasonal sinusoid + AR(1) noise to mimic real weather variability.
    All values in metric (°C, mm, km/h).
    """
    rng = random.Random(seed + hash(city) % 10000)
    mean_t, amp_t, mean_precip = CITY_CLIMATE.get(city, (15.0, 10.0, 2.5))

    start = datetime.strptime(start_date, "%Y-%m-%d")
    end   = datetime.strptime(end_date,   "%Y-%m-%d")
    n_days = (end - start).days + 1

    records = []
    temp_noise = 0.0  # AR(1) state

    for i in range(n_days):
        dt  = start + timedelta(days=i)
        doy = dt.timetuple().tm_yday

        # Seasonal mean: peak in July (~doy 196), trough in January (~doy 15)
        seasonal = mean_t + amp_t * math.sin(2 * math.pi * (doy - 80) / 365)

        # AR(1) noise (persistence ~0.7)
        temp_noise = 0.7 * temp_noise + rng.gauss(0, 2.0)
        tmax = seasonal + temp_noise + rng.gauss(4, 1.5)
        tmin = seasonal + temp_noise + rng.gauss(-4, 1.5)
        if tmin > tmax:
            tmin, tmax = tmax, tmin

        # Precipitation: exponential distribution, occasional rain days
        rain_day = rng.random() < 0.35
        precip   = rng.expovariate(1.0 / mean_precip) if rain_day else 0.0
        precip   = round(min(precip, 120.0), 1)

        wind = abs(rng.gauss(20, 8))
        solar = max(0, rng.gauss(180 + 80 * math.sin(2 * math.pi * (doy - 80) / 365), 40))

        records.append({
            "date":                       dt.strftime("%Y-%m-%d"),
            "temperature_2m_max":         round(tmax, 1),
            "temperature_2m_min":         round(tmin, 1),
            "precipitation_sum":          precip,
            "wind_speed_10m_max":         round(wind, 1),
            "shortwave_radiation_sum":    round(solar, 1),
            "et0_fao_evapotranspiration": round(max(0, solar / 80 + rng.gauss(1, 0.3)), 2),
        })

    return records


def generate_synthetic_markets(
    n_markets: int = 30,
    seed: int = 42,
) -> list[dict]:
    """
    Generate mock PolyMarket weather markets for offline testing.
    Each market is a binary question with a synthetic price history.
    """
    rng = random.Random(seed)
    cities = list(CITY_CLIMATE.keys())
    templates = [
        ("Will the high temperature in {city_nice} exceed 90°F on {date}?",  "temp_above_90f"),
        ("Will it rain in {city_nice} on {date}?",                            "precip_any"),
        ("Will {city_nice} see more than 1 inch of rain on {date}?",          "precip_1in"),
        ("Will {city_nice} temperatures drop below freezing on {date}?",       "temp_above_32f"),
        ("Will wind gusts exceed 25 mph in {city_nice} on {date}?",           "wind_above_25mph"),
    ]

    today  = datetime.now(timezone.utc)
    markets = []
    for i in range(n_markets):
        city        = rng.choice(cities)
        city_nice   = city.replace("_", " ").title()
        template, _ = rng.choice(templates)
        delta_days  = rng.randint(1, 14)
        end_dt      = today + timedelta(days=delta_days)
        end_date    = end_dt.strftime("%Y-%m-%d")
        question    = template.format(city_nice=city_nice, date=end_date)

        # Synthetic price (market-implied probability)
        mid_price = round(rng.uniform(0.15, 0.85), 3)

        cid = f"SYNTHETIC_{i:04d}_{city[:3].upper()}"
        yes_token = f"YES_{cid}"
        no_token  = f"NO_{cid}"

        # Generate a short price history (last 14 days, random walk toward mid_price)
        price_history = []
        p = rng.uniform(0.2, 0.8)
        ts_base = int((today - timedelta(days=14)).timestamp())
        for h in range(14 * 24):   # hourly
            p = max(0.05, min(0.95, p + rng.gauss(0, 0.01)))
            price_history.append({"timestamp": ts_base + h * 3600, "price": round(p, 4)})

        markets.append({
            "condition_id": cid,
            "question":     question,
            "slug":         cid.lower(),
            "end_date":     end_date,
            "tokens":       [
                {"token_id": yes_token, "outcome": "YES"},
                {"token_id": no_token,  "outcome": "NO"},
            ],
            "volume":       round(rng.uniform(500, 50000), 2),
            "liquidity":    round(rng.uniform(200, 10000), 2),
            "closed":       False,
            "tags":         [{"slug": "weather"}],
            "_synthetic":   True,
            "_mid_price":   mid_price,
            "_price_history": price_history,
        })

    return markets


def save_synthetic_markets(markets: list[dict]) -> Path:
    path = DATA_DIR / "markets_cache.json"
    # Strip the embedded price history before caching (stored separately)
    slim = [
        {k: v for k, v in m.items() if k != "_price_history"}
        for m in markets
    ]
    path.write_text(json.dumps(slim, indent=2))

    # Save per-market price histories
    for m in markets:
        cid = m["condition_id"]
        hist = m.get("_price_history", [])
        if hist:
            hist_path = DATA_DIR / f"mkt_{cid[:20]}_prices.json"
            hist_path.write_text(json.dumps(hist, indent=2))

    return path


def generate_full_demo_dataset(
    cities: list[str] | None = None,
    lookback_years: int = 3,
) -> dict:
    """
    Generate and save synthetic weather + market data for all cities.
    Returns summary dict matching run_data_pipeline()'s return shape.
    """
    cities = cities or list(CITY_CLIMATE.keys())
    today      = datetime.now(timezone.utc)
    start_date = (today - timedelta(days=365 * lookback_years)).strftime("%Y-%m-%d")
    end_date   = today.strftime("%Y-%m-%d")

    total_records = 0
    for i, city in enumerate(cities):
        records = generate_synthetic_weather(city, start_date, end_date, seed=i * 100)
        path    = DATA_DIR / f"{city}_weather.json"
        path.write_text(json.dumps(records, indent=2))
        total_records += len(records)

    markets = generate_synthetic_markets(n_markets=40)
    save_synthetic_markets(markets)

    return {
        "weather_cities":  len(cities),
        "weather_records": total_records,
        "markets_found":   len(markets),
        "price_histories": len(markets),
        "source":          "synthetic",
    }
