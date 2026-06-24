"""
Resolved market data collector.

Fetches closed PolyMarket weather markets, extracts YES/NO outcomes,
aligns with Open-Meteo historical weather for the resolution date,
and saves labeled training records to data/pw_resolved/resolved.jsonl.

These records are the ground truth the XGBoost models need to learn
real mispricings instead of relying on synthetic outcomes.

Run automatically each cycle via tools.py, or manually:
    python3 run_polymarket_weather_auto.py --collect
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

ROOT         = Path(__file__).parent.parent
RESOLVED_DIR = ROOT / "data" / "pw_resolved"
RESOLVED_DIR.mkdir(parents=True, exist_ok=True)
RESOLVED_FILE = RESOLVED_DIR / "resolved.jsonl"
SEEN_FILE     = RESOLVED_DIR / "seen_ids.json"

GAMMA_API = "https://gamma-api.polymarket.com"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_seen() -> set[str]:
    if SEEN_FILE.exists():
        try:
            return set(json.loads(SEEN_FILE.read_text()))
        except Exception:
            pass
    return set()


def _save_seen(seen: set[str]):
    SEEN_FILE.write_text(json.dumps(sorted(seen)))


def _load_resolved() -> list[dict]:
    if not RESOLVED_FILE.exists():
        return []
    records = []
    for line in RESOLVED_FILE.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return records


def _append_resolved(records: list[dict]):
    with RESOLVED_FILE.open("a") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


# ---------------------------------------------------------------------------
# Outcome extraction
# ---------------------------------------------------------------------------

def _extract_outcome(market: dict) -> Optional[int]:
    """
    Return 1 (YES won), 0 (NO won), or None (unresolvable).

    PolyMarket signals resolution via token final prices:
      YES token price → 1.0 means YES won
      NO  token price → 1.0 means NO  won
    """
    tokens = market.get("tokens") or market.get("outcomes") or []

    for token in tokens:
        outcome_label = (token.get("outcome") or "").upper()
        price = float(token.get("price") or token.get("finalPrice") or 0)
        if outcome_label == "YES" and price >= 0.95:
            return 1
        if outcome_label == "NO" and price >= 0.95:
            return 0

    # Fallback: check outcomePrices field (some API versions)
    outcome_prices = market.get("outcomePrices")
    if outcome_prices:
        try:
            prices = [float(p) for p in outcome_prices]
            if prices[0] >= 0.95:   # index 0 = YES
                return 1
            if len(prices) > 1 and prices[1] >= 0.95:
                return 0
        except (ValueError, IndexError):
            pass

    return None


def _is_weather_market(question: str) -> bool:
    q = question.lower()
    weather_terms = [
        "temperature", "rain", "wind", "precipitation",
        "weather", "forecast", "humid", "snow", "storm",
        "above", "below", "exceed", "freeze", "frost",
    ]
    return any(t in q for t in weather_terms)


# ---------------------------------------------------------------------------
# Gamma API fetch
# ---------------------------------------------------------------------------

def _fetch_resolved_markets(days_back: int = 60, limit: int = 200) -> list[dict]:
    """
    Query Gamma API for recently closed weather markets.
    Returns raw market dicts or [] on network error.
    """
    import requests

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days_back)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    params = {
        "closed":       "true",
        "limit":        limit,
        "end_date_min": cutoff,
    }
    try:
        resp = requests.get(
            f"{GAMMA_API}/markets",
            params=params,
            timeout=15,
            headers={"User-Agent": "WholesaleOmniverse-PolyWeather/1.0"},
        )
        resp.raise_for_status()
        data = resp.json()
        markets = data if isinstance(data, list) else data.get("markets", [])
        return [m for m in markets if _is_weather_market(m.get("question", ""))]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Weather alignment
# ---------------------------------------------------------------------------

def _align_weather(market: dict) -> Optional[dict]:
    """
    Fetch historical Open-Meteo data for the market's resolution date and city.
    Returns a feature dict or None if city/date can't be extracted.
    """
    from polymarket_weather.agent import _extract_city, _extract_event_type
    from polymarket_weather.data_pipeline import (
        fetch_historical_weather,
        build_daily_weather_df,
        CITY_COORDS,
    )
    from polymarket_weather.model import engineer_features

    question = market.get("question", "")
    city_key = _extract_city(question)
    if city_key is None:
        return None

    event_type = _extract_event_type(question)

    # Resolution date
    end_date = (market.get("endDate") or market.get("end_date") or "")[:10]
    if not end_date:
        return None

    try:
        start = (datetime.strptime(end_date, "%Y-%m-%d") - timedelta(days=30)).strftime(
            "%Y-%m-%d"
        )
        lat, lon, _ = CITY_COORDS[city_key]
        raw  = fetch_historical_weather(lat, lon, start, end_date)
        recs = build_daily_weather_df(raw)
        eng  = engineer_features(recs)

        # Find the record matching the resolution date
        matching = [r for r in eng if r.get("date") == end_date]
        if not matching:
            matching = [eng[-1]] if eng else []
        if not matching:
            return None

        record = dict(matching[0])
        record["city"]         = city_key
        record["event_type"]   = event_type
        record["market_id"]    = market.get("conditionId") or market.get("condition_id", "")
        record["question"]     = question
        record["resolution_date"] = end_date
        record["collected_at"] = datetime.now(timezone.utc).isoformat()
        return record

    except Exception:
        return None


# ---------------------------------------------------------------------------
# Main collection function
# ---------------------------------------------------------------------------

def collect_new_resolutions(days_back: int = 60) -> dict:
    """
    Fetch newly resolved weather markets, align with weather data,
    and save labeled records for model retraining.

    Returns: {"new_records": int, "total_records": int, "errors": int}
    """
    seen    = _load_seen()
    markets = _fetch_resolved_markets(days_back=days_back)

    new_records: list[dict] = []
    errors = 0

    for market in markets:
        condition_id = market.get("conditionId") or market.get("condition_id", "")
        if not condition_id or condition_id in seen:
            continue

        outcome = _extract_outcome(market)
        if outcome is None:
            continue

        aligned = _align_weather(market)
        if aligned is None:
            errors += 1
            seen.add(condition_id)
            continue

        aligned["outcome"] = outcome
        new_records.append(aligned)
        seen.add(condition_id)
        time.sleep(0.2)  # gentle rate limiting

    if new_records:
        _append_resolved(new_records)

    _save_seen(seen)

    total = len(_load_resolved())
    return {
        "new_records":   len(new_records),
        "total_records": total,
        "errors":        errors,
        "markets_checked": len(markets),
    }


def load_resolved_for_training() -> list[dict]:
    """
    Load all resolved records and return them ready for model.fit().
    Each record has weather features + outcome (0/1) + event_type + market_price.
    """
    records = _load_resolved()
    # Ensure market_price exists (use 0.5 if not saved — model uses outcome as truth)
    for r in records:
        r.setdefault("market_price", 0.5)
    return records


def resolved_count() -> int:
    return len(_load_resolved())
