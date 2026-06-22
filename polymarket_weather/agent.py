"""
Trading agent — scans live PolyMarket weather markets, compares model probabilities
to market prices, and places trades when edge exceeds the configured threshold.

Cycle:
  1. Fetch all open PolyMarket weather markets
  2. For each market, determine the relevant city/event from the question text
  3. Fetch live weather forecast for that location
  4. Run the appropriate model to get P(event=YES)
  5. Compare to the current market mid-price
  6. If edge > threshold AND risk checks pass → place order
  7. Log everything to data/pw_trades/trade_log.jsonl

Set PW_LIVE_TRADING=1 to place real orders. Default is dry-run.
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from polymarket_weather.api_client import (
    get_weather_markets,
    get_midpoint_price,
    get_order_book,
    PolyMarketTrader,
    Market,
)
from polymarket_weather.data_pipeline import (
    fetch_forecast_weather,
    build_daily_weather_df,
    CITY_COORDS,
)
from polymarket_weather.model import engineer_features
from polymarket_weather.model import WeatherForecastModel
from polymarket_weather.risk import RiskManager
from polymarket_weather.backtest import kelly_size, threshold_signal

ROOT       = Path(__file__).parent.parent
TRADES_DIR = ROOT / "data" / "pw_trades"
TRADES_DIR.mkdir(parents=True, exist_ok=True)

TRADE_LOG = TRADES_DIR / "trade_log.jsonl"

# ---------------------------------------------------------------------------
# City / event extraction from question text
# ---------------------------------------------------------------------------

# Map question keywords → city key in CITY_COORDS
CITY_KEYWORDS: dict[str, str] = {
    "new york":    "new_york",
    "nyc":         "new_york",
    "manhattan":   "new_york",
    "los angeles": "los_angeles",
    "la":          "los_angeles",
    "chicago":     "chicago",
    "houston":     "houston",
    "phoenix":     "phoenix",
    "philadelphia":"philadelphia",
    "philly":      "philadelphia",
    "san antonio": "san_antonio",
    "dallas":      "dallas",
    "miami":       "miami",
    "atlanta":     "atlanta",
}

# Map question keywords → event_type for the ML model
EVENT_KEYWORDS: dict[str, str] = {
    "above 90":          "temp_above_90f",
    "exceed 90":         "temp_above_90f",
    "over 90":           "temp_above_90f",
    "above 32":          "temp_above_32f",
    "freeze":            "temp_above_32f",
    "frost":             "temp_above_32f",
    "rain":              "precip_any",
    "precipitation":     "precip_any",
    "wet":               "precip_any",
    "1 inch":            "precip_1in",
    "one inch":          "precip_1in",
    "25 mph":            "wind_above_25mph",
    "25mph":             "wind_above_25mph",
    "wind":              "wind_above_25mph",
}


def _extract_city(question: str) -> Optional[str]:
    q = question.lower()
    # Use word-boundary matching so "la" doesn't hit "dallas"/"atlanta"/"philadelphia"
    import re
    for keyword, city_key in CITY_KEYWORDS.items():
        pattern = r'\b' + re.escape(keyword) + r'\b'
        if re.search(pattern, q):
            return city_key
    return None


def _extract_event_type(question: str) -> str:
    q = question.lower()
    for keyword, event_type in EVENT_KEYWORDS.items():
        if keyword in q:
            return event_type
    return "temp_above_90f"  # default to most common market type


# ---------------------------------------------------------------------------
# Opportunity dataclass
# ---------------------------------------------------------------------------

class Opportunity:
    def __init__(
        self,
        market:       Market,
        city:         str,
        event_type:   str,
        model_prob:   float,
        market_price: float,
        side:         str,       # "YES" or "NO"
        edge:         float,
        proposed_size:float,
    ):
        self.market       = market
        self.city         = city
        self.event_type   = event_type
        self.model_prob   = model_prob
        self.market_price = market_price
        self.side         = side
        self.edge         = edge
        self.proposed_size= proposed_size

    def ev(self) -> float:
        """Expected value per USDC staked."""
        if self.side == "YES":
            return self.model_prob * (1 - self.market_price) - (1 - self.model_prob) * self.market_price
        return (1 - self.model_prob) * self.market_price - self.model_prob * (1 - self.market_price)

    def to_dict(self) -> dict:
        return {
            "question":     self.market.question,
            "city":         self.city,
            "event_type":   self.event_type,
            "model_prob":   round(self.model_prob, 4),
            "market_price": round(self.market_price, 4),
            "side":         self.side,
            "edge":         round(self.edge, 4),
            "ev":           round(self.ev(), 4),
            "proposed_size":self.proposed_size,
        }


# ---------------------------------------------------------------------------
# Main trading agent
# ---------------------------------------------------------------------------

class WeatherTradingAgent:
    def __init__(
        self,
        min_edge:      float = 0.07,
        min_liquidity: float = 500.0,   # ignore markets with thin liquidity
        bankroll:      float = 1000.0,
        kelly_fraction:float = 0.25,
        max_position_pct:float = 0.05,
    ):
        self.min_edge        = min_edge
        self.min_liquidity   = min_liquidity
        self.kelly_fraction  = kelly_fraction
        self.max_position_pct= max_position_pct
        self.risk            = RiskManager(
            starting_bankroll=bankroll,
            min_edge=min_edge,
            max_position_pct=max_position_pct,
        )
        self.trader          = PolyMarketTrader()
        self._model_cache:   dict[str, WeatherForecastModel] = {}
        self._forecast_cache:dict[str, list[dict]] = {}

    # -----------------------------------------------------------------------
    # Model access
    # -----------------------------------------------------------------------

    def _get_model(self, event_type: str) -> WeatherForecastModel:
        if event_type not in self._model_cache:
            self._model_cache[event_type] = WeatherForecastModel.load(event_type)
        return self._model_cache[event_type]

    def _get_forecast(self, city: str) -> list[dict]:
        if city in self._forecast_cache:
            return self._forecast_cache[city]
        lat, lon, _ = CITY_COORDS.get(city, (40.71, -74.0, 10))
        feats: list[dict] = []
        try:
            raw   = fetch_forecast_weather(lat, lon, days=14)
            recs  = build_daily_weather_df(raw)
            feats = engineer_features(recs)
        except Exception:
            # Fall back to synthetic 14-day forecast for this city
            feats = self._synthetic_forecast(city)
        self._forecast_cache[city] = feats
        return feats

    def _synthetic_forecast(self, city: str, days: int = 14) -> list[dict]:
        """Generate a realistic 14-day synthetic forecast when live API is offline."""
        from polymarket_weather.synthetic import generate_synthetic_weather
        from datetime import datetime, timedelta, timezone
        today      = datetime.now(timezone.utc)
        start_date = today.strftime("%Y-%m-%d")
        end_date   = (today + timedelta(days=days)).strftime("%Y-%m-%d")
        import hashlib
        seed = int(hashlib.md5(f"{city}{today.strftime('%Y%m%d')}".encode()).hexdigest(), 16) % 100000
        recs = generate_synthetic_weather(city, start_date, end_date, seed=seed)
        return engineer_features(recs)

    def _get_model_prob(self, city: str, event_type: str, target_date: str) -> float:
        """Return model probability for a specific date."""
        forecasts = self._get_forecast(city)
        if not forecasts:
            return 0.5

        # Find the closest forecast date
        matching = [f for f in forecasts if f.get("date") == target_date]
        if not matching:
            # Use the last available forecast day
            matching = [forecasts[-1]] if forecasts else []
        if not matching:
            return 0.5

        model = self._get_model(event_type)
        if not model._trained:
            return 0.5
        return model.predict_single(matching[0])

    # -----------------------------------------------------------------------
    # Market scanning
    # -----------------------------------------------------------------------

    def scan_opportunities(self) -> list[Opportunity]:
        """Fetch live markets and identify trades with positive edge."""
        markets = get_weather_markets(limit=100, closed=False)
        opportunities: list[Opportunity] = []

        for market in markets:
            if market.closed:
                continue
            if market.liquidity < self.min_liquidity:
                continue

            city = _extract_city(market.question)
            if city is None:
                continue

            event_type = _extract_event_type(market.question)

            yes_token = market.yes_token_id()
            if not yes_token:
                continue

            try:
                book         = get_order_book(yes_token)
                market_price = book.mid_price()
                if market_price <= 0.01 or market_price >= 0.99:
                    continue   # Skip resolved / near-resolved markets
            except Exception:
                continue

            # Extract target date from end_date
            target_date = (market.end_date or "")[:10]

            model_prob = self._get_model_prob(city, event_type, target_date)
            side       = threshold_signal(model_prob, market_price, self.min_edge)
            if side is None:
                continue

            edge = abs(model_prob - market_price)
            token_id = yes_token if side == "YES" else market.no_token_id() or yes_token
            size = kelly_size(
                model_prob,
                market_price,
                side,
                self.risk.bankroll,
                self.max_position_pct,
                self.kelly_fraction,
            )

            opportunities.append(Opportunity(
                market       = market,
                city         = city,
                event_type   = event_type,
                model_prob   = model_prob,
                market_price = market_price,
                side         = side,
                edge         = edge,
                proposed_size= size,
            ))

        # Sort by edge (highest first)
        opportunities.sort(key=lambda o: -o.edge)
        return opportunities

    # -----------------------------------------------------------------------
    # Trade execution
    # -----------------------------------------------------------------------

    def execute_opportunity(self, opp: Opportunity) -> dict:
        """Run risk checks and place an order for one opportunity."""
        approved, reason = self.risk.check_trade(
            opp.model_prob,
            opp.market_price,
            opp.side,
            opp.proposed_size,
        )

        result: dict = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "question":  opp.market.question,
            "city":      opp.city,
            "event":     opp.event_type,
            "side":      opp.side,
            "edge":      opp.edge,
            "model_prob":opp.model_prob,
            "market_price": opp.market_price,
            "size":      opp.proposed_size,
            "approved":  approved,
            "reason":    reason,
        }

        if not approved:
            _log_trade(result)
            return result

        # Determine the right token to buy
        if opp.side == "YES":
            token_id    = opp.market.yes_token_id()
            entry_price = opp.market_price
        else:
            token_id    = opp.market.no_token_id() or opp.market.yes_token_id()
            entry_price = 1.0 - opp.market_price

        receipt = self.trader.place_limit_order(
            token_id    = token_id,
            side        = "BUY",
            price       = entry_price,
            size        = opp.proposed_size,
        )

        result["receipt"]  = receipt
        result["order_id"] = receipt.get("order_id", "")
        result["live"]     = self.trader.live

        if receipt.get("status") not in ("error",):
            self.risk.record_trade_open(
                order_id = receipt.get("order_id", "DRY"),
                token_id = token_id or "",
                side     = opp.side,
                size     = opp.proposed_size,
            )

        _log_trade(result)
        return result

    # -----------------------------------------------------------------------
    # Full cycle
    # -----------------------------------------------------------------------

    def run_cycle(self) -> dict:
        """One complete scan + trade cycle. Called by run_full_cycle()."""
        if self.risk.is_halted:
            return {
                "status":      "halted",
                "halt_reason": self.risk.status_dict()["halt_reason"],
                "opportunities": 0,
                "trades_placed": 0,
            }

        self._forecast_cache = {}  # clear per-cycle cache

        opportunities = self.scan_opportunities()
        trades_placed  = []

        for opp in opportunities:
            result = self.execute_opportunity(opp)
            trades_placed.append(result)
            time.sleep(0.5)  # gentle pacing

        return {
            "status":        "ok",
            "opportunities": len(opportunities),
            "trades_placed": len([t for t in trades_placed if t.get("approved")]),
            "risk":          self.risk.status_dict(),
            "live_trading":  self.trader.live,
            "top_opps":      [o.to_dict() for o in opportunities[:5]],
        }


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def _log_trade(record: dict):
    with TRADE_LOG.open("a") as f:
        f.write(json.dumps(record) + "\n")


def load_trade_log(limit: int = 100) -> list[dict]:
    if not TRADE_LOG.exists():
        return []
    lines = TRADE_LOG.read_text().strip().splitlines()
    parsed = []
    for line in lines[-limit:]:
        try:
            parsed.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return parsed
