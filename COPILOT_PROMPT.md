# GitHub Copilot Workspace Prompt — PolyMarket Weather Trading Agent

> Paste this entire file into GitHub Copilot Workspace (or any AI coding assistant)
> to scaffold the complete project from scratch in a new repository.

---

## Project Overview

Build an **autonomous PolyMarket weather market trading agent** that:

1. Fetches weather data from the free Open-Meteo API (no key required)
2. Trains XGBoost models to predict the probability of binary weather outcomes
3. Scans live PolyMarket prediction markets for pricing inefficiencies
4. Places limit orders when model edge exceeds a configurable threshold
5. Runs 24/7 on Render free tier as a Flask web service (no cron needed)
6. Rotates credentials via GitHub Actions (no secrets in code ever)

The entire system is self-contained in one repo. It works in offline/dry-run mode
with synthetic data so no API keys are required to develop and test locally.

---

## Repository Structure

```
polymarket-weather-agent/
├── polymarket_weather/
│   ├── __init__.py
│   ├── api_client.py        # PolyMarket Gamma + CLOB API wrapper
│   ├── data_pipeline.py     # Open-Meteo fetching + historical storage
│   ├── model.py             # XGBoost probability forecaster
│   ├── backtest.py          # Historical simulation engine
│   ├── risk.py              # RiskManager with kill switch
│   ├── agent.py             # WeatherTradingAgent (scan + trade)
│   ├── synthetic.py         # Offline fallback data generator
│   └── tools.py             # run_full_cycle() orchestrator
├── run_polymarket_weather_auto.py   # CLI entry point
├── run_polymarket_weather_server.py # Flask server for Render
├── run_polymarket_weather_worker.py # Simple worker loop (optional)
├── requirements-pw.txt
├── render.yaml
└── .github/workflows/update-render-env.yml
```

---

## Component 1 — `polymarket_weather/api_client.py`

### Purpose
Wraps PolyMarket's public **Gamma API** (market discovery) and **CLOB API**
(order books, authenticated order placement).

### Key dataclasses

```python
@dataclass
class Market:
    condition_id: str
    question: str
    slug: str
    end_date: str
    tokens: list[dict]      # [{"token_id": "...", "outcome": "YES"}, ...]
    volume: float = 0.0
    liquidity: float = 0.0
    closed: bool = False
    tags: list[str] = field(default_factory=list)

    def yes_token_id(self) -> Optional[str]: ...
    def no_token_id(self) -> Optional[str]: ...

@dataclass
class OrderBook:
    bids: list[dict]   # [{"price": float, "size": float}]
    asks: list[dict]
    def mid_price(self) -> float: ...   # (best_bid + best_ask) / 2
```

### Public functions

```python
def get_weather_markets(limit: int = 100, closed: bool = False) -> list[Market]:
    """
    Query Gamma API for weather prediction markets.
    Filter: tags containing "weather" OR question text containing
    ["temperature","rain","wind","precipitation","weather","forecast"].
    Endpoint: GET https://gamma-api.polymarket.com/markets
    params: limit=limit, closed=closed, tag_slug_contains=weather
    Falls back to _load_cached_markets() if network blocked.
    Save raw response to data/pw_cache/markets.json on success.
    """

def get_order_book(token_id: str) -> OrderBook:
    """
    GET https://clob.polymarket.com/book?token_id={token_id}
    Returns bids/asks. Falls back to synthetic book (mid = 0.50 ± random) if blocked.
    """

def get_midpoint_price(token_id: str) -> float:
    """Convenience wrapper around get_order_book().mid_price()."""
```

### PolyMarketTrader class

```python
class PolyMarketTrader:
    """
    Authenticated order placement via py-clob-client.

    Auth strategy (try in order):
      1. L2 auth: PW_API_KEY + PW_API_SECRET (passphrase = "")
      2. L1 auth: PW_PRIVATE_KEY only (derives API creds on-chain)
    
    If PW_LIVE_TRADING != "1": dry-run mode — log the order but never send.
    
    Private key format: 64-char hex WITHOUT "0x" prefix.
    Chain ID: 137 (Polygon mainnet).
    """

    def place_limit_order(
        self,
        token_id: str,
        side: str,      # "BUY" or "SELL"
        price: float,   # 0.01–0.99 USDC per share
        size: float,    # USDC amount
    ) -> dict:
        """
        Returns {"status": "live"|"dry_run"|"error", "order_id": str, ...}
        In dry-run mode, returns status="dry_run" with the order details logged.
        """
```

---

## Component 2 — `polymarket_weather/data_pipeline.py`

### Purpose
Fetch historical and forecast weather from the **Open-Meteo API** (100% free,
no API key, no rate limit for reasonable use). Store history as flat JSON files.

### City coordinates dict

```python
CITY_COORDS: dict[str, tuple[float, float, int]] = {
    # city_key: (latitude, longitude, utc_offset_hours)
    "new_york":    (40.7128, -74.0060, -5),
    "los_angeles": (34.0522, -118.2437, -8),
    "chicago":     (41.8781, -87.6298, -6),
    "houston":     (29.7604, -95.3698, -6),
    "phoenix":     (33.4484, -112.0740, -7),
    "philadelphia":(39.9526, -75.1652, -5),
    "san_antonio": (29.4241, -98.4936, -6),
    "dallas":      (32.7767, -96.7970, -6),
    "miami":       (25.7617, -80.1918, -5),
    "atlanta":     (33.7490, -84.3880, -5),
}
```

### Key functions

```python
def fetch_historical_weather(
    lat: float, lon: float,
    start_date: str,   # "YYYY-MM-DD"
    end_date: str,
) -> dict:
    """
    GET https://archive-api.open-meteo.com/v1/archive
    params: latitude, longitude, start_date, end_date,
            daily=["temperature_2m_max","temperature_2m_min",
                   "precipitation_sum","wind_speed_10m_max",
                   "shortwave_radiation_sum","et0_fao_evapotranspiration"],
            timezone="auto"
    Returns raw JSON response dict.
    """

def fetch_forecast_weather(lat: float, lon: float, days: int = 14) -> dict:
    """
    GET https://api.open-meteo.com/v1/forecast
    Same daily variables as above. Returns raw JSON.
    """

def build_daily_weather_df(raw: dict) -> list[dict]:
    """
    Unpack the Open-Meteo response (column-oriented) into a list of daily
    records: [{"date": "2024-07-01", "temperature_2m_max": 95.2, ...}, ...]
    """

def run_data_pipeline(
    cities: list[str],
    lookback_years: int = 3,
) -> dict:
    """
    For each city: fetch historical weather from (today - lookback_years) to today.
    Save to data/pw_historical/{city}.json (append new records, dedupe by date).
    Also fetch synthetic market prices from PolyMarket CLOB for each city
    and save to data/pw_historical/{city}_prices.json.
    
    IMPORTANT: If Open-Meteo is unreachable, call generate_full_demo_dataset()
    from synthetic.py as a fallback so the system works fully offline.
    
    Returns {"cities_updated": [...], "records_added": int, ...}
    """

def load_historical_weather(city: str) -> list[dict]:
    """Load data/pw_historical/{city}.json, return [] if missing."""
```

---

## Component 3 — `polymarket_weather/model.py`

### Purpose
XGBoost binary classifier that outputs **P(event = YES)** for a given
weather threshold question on a target date.

### Feature columns

```python
FEATURE_COLS = [
    "temperature_2m_max",
    "temperature_2m_min",
    "precipitation_sum",
    "wind_speed_10m_max",
    "shortwave_radiation_sum",
    "et0_fao_evapotranspiration",
    # engineered below
    "temp_range",       # max - min
    "temp_anomaly_7d",  # deviation from 7-day rolling average of max temp
    "precip_7d_sum",    # rolling 7-day precipitation sum
    "month_sin",        # sin(2π * month / 12)
    "month_cos",        # cos(2π * month / 12)
    "doy_sin",          # sin(2π * day_of_year / 365)
    "doy_cos",          # cos(2π * day_of_year / 365)
]
```

### engineer_features function

```python
def engineer_features(records: list[dict]) -> list[dict]:
    """
    Input: list of daily weather dicts (must include date and raw columns).
    Adds: temp_range, temp_anomaly_7d, precip_7d_sum, month_sin/cos, doy_sin/cos.
    Returns new list with all FEATURE_COLS present (None → 0.0 for missing values).
    """
```

### WeatherForecastModel class

```python
class WeatherForecastModel:
    """
    Wraps xgboost.train() with a binary:logistic objective.
    
    XGBoost native API params (NOT sklearn wrapper):
      {"objective": "binary:logistic",
       "eval_metric": "logloss",
       "max_depth": 4,
       "learning_rate": 0.05,
       "subsample": 0.8,
       "colsample_bytree": 0.8,
       "min_child_weight": 5,
       "seed": 42}
    num_boost_round = 200 (pass as arg to xgb.train, NOT in params dict)
    
    Serialization: save/load as .ubj (binary JSON — xgb native format).
    File path: data/pw_models/{event_type}.ubj
    
    Metrics stored on model: accuracy, brier_score, brier_skill, auc, logloss, n
    """

    def fit(self, records: list[dict], event_type: str) -> dict:
        """
        Build labels based on event_type:
          "temp_above_90f"  → label = 1 if temperature_2m_max > 32.2°C (90°F)
          "temp_above_32f"  → label = 1 if temperature_2m_max > 0°C (32°F)  
          "precip_any"      → label = 1 if precipitation_sum > 0.1mm
          "precip_1in"      → label = 1 if precipitation_sum > 25.4mm
          "wind_above_25mph"→ label = 1 if wind_speed_10m_max > 40.2 kph
        
        Compute metrics on a 20% held-out test set.
        Save model and metrics to disk.
        Returns metrics dict.
        """

    def predict_proba(self, records: list[dict]) -> list[float]:
        """Run inference. Returns list of float probabilities."""

    def predict_single(self, record: dict) -> float:
        """Convenience wrapper for a single record."""

    @classmethod
    def load(cls, event_type: str) -> "WeatherForecastModel":
        """Load from data/pw_models/{event_type}.ubj. Returns untrained instance if missing."""
```

### train_all_models function

```python
EVENT_TYPES = [
    "temp_above_90f",
    "temp_above_32f",
    "precip_any",
    "precip_1in",
    "wind_above_25mph",
]

def train_all_models(weather_by_city: dict[str, list[dict]]) -> dict[str, dict]:
    """
    For each event type: pool records from all cities, call model.fit().
    Returns {event_type: metrics_dict} for all 5 models.
    """
```

---

## Component 4 — `polymarket_weather/backtest.py`

### Purpose
Simulate historical trading to validate the edge before going live.

### Key functions

```python
def kelly_size(
    model_prob: float,
    market_price: float,
    side: str,           # "YES" or "NO"
    bankroll: float,
    max_position_pct: float = 0.05,   # max 5% of bankroll per trade
    kelly_fraction: float = 0.25,     # fractional Kelly (conservative)
) -> float:
    """
    Kelly criterion:
      For YES: f = (model_prob * (1/market_price - 1) - (1-model_prob)) / (1/market_price - 1)
      For NO:  use (1-model_prob) and (1-market_price) in same formula.
    Apply kelly_fraction multiplier.
    Cap at max_position_pct * bankroll.
    Minimum trade size: $1.00. Return 0.0 if Kelly is negative.
    """

def threshold_signal(
    model_prob: float,
    market_price: float,
    min_edge: float = 0.07,
) -> Optional[str]:
    """
    Returns "YES" if model_prob - market_price > min_edge,
            "NO"  if market_price - model_prob > min_edge (model thinks NO is underpriced),
            None  if no edge.
    """
```

### BacktestEngine class

```python
class BacktestEngine:
    """
    Simulates trading on historical records with known outcomes.
    
    Each record must have: model features + "market_price" + "outcome" (0/1) + "market_id"
    
    Tracks: bankroll, trade-by-trade P&L, drawdown, Sharpe ratio.
    """

    def run(self, records: list[dict]) -> dict:
        """
        Returns: {"trades": int, "win_rate": float, "roi_pct": float,
                  "sharpe": float, "max_drawdown_pct": float,
                  "brier_score": float, "final_bankroll": float}
        """

    def generate_report(self, name: str) -> Path:
        """Write markdown report to data/pw_reports/{name}.md"""
```

---

## Component 5 — `polymarket_weather/risk.py`

### Purpose
Per-trade risk checks and portfolio kill switch. State persisted to disk so it
survives container restarts.

```python
class RiskManager:
    """
    State file: data/pw_trades/risk_state.json
    
    Limits enforced:
      - min_edge: reject trade if |model_prob - market_price| < threshold
      - max_position_pct: single trade ≤ 5% of bankroll
      - max_open_positions: 10 concurrent positions
      - daily_loss_limit: halt if daily P&L < -10% of starting bankroll
      - max_consecutive_losses: halt after 5 consecutive losing trades
    
    Kill switch: halt() sets is_halted=True, persists to disk.
                 resume() clears the halt.
    """

    def check_trade(
        self,
        model_prob: float,
        market_price: float,
        side: str,
        proposed_size: float,
    ) -> tuple[bool, str]:
        """Returns (approved: bool, reason: str)."""

    def record_trade_open(self, order_id: str, token_id: str, side: str, size: float):
        """Track open position in state."""

    def record_trade_close(self, order_id: str, pnl: float):
        """Update P&L, check stop-loss conditions, possibly trigger halt."""

    def status_dict(self) -> dict:
        """Returns current state as dict for logging / digest."""
```

---

## Component 6 — `polymarket_weather/agent.py`

### Purpose
The core trading agent: scans markets, extracts city/event from question text,
gets model probability, checks edge, and executes trades.

### City/event extraction

```python
CITY_KEYWORDS: dict[str, str] = {
    "new york": "new_york", "nyc": "new_york", "manhattan": "new_york",
    "los angeles": "los_angeles", "chicago": "chicago", "houston": "houston",
    "phoenix": "phoenix", "philadelphia": "philadelphia", "philly": "philadelphia",
    "san antonio": "san_antonio", "dallas": "dallas", "miami": "miami",
    "atlanta": "atlanta",
    # NOTE: "la" must use word-boundary regex r'\bla\b' to avoid matching "dallas"
}

EVENT_KEYWORDS: dict[str, str] = {
    "above 90": "temp_above_90f", "exceed 90": "temp_above_90f",
    "above 32": "temp_above_32f", "freeze": "temp_above_32f", "frost": "temp_above_32f",
    "rain": "precip_any", "precipitation": "precip_any", "wet": "precip_any",
    "1 inch": "precip_1in", "one inch": "precip_1in",
    "25 mph": "wind_above_25mph", "wind": "wind_above_25mph",
}

def _extract_city(question: str) -> Optional[str]:
    """Use r'\b' + re.escape(keyword) + r'\b' for ALL keywords to prevent substring matches."""

def _extract_event_type(question: str) -> str:
    """Returns best-matching event type; defaults to 'temp_above_90f'."""
```

### WeatherTradingAgent class

```python
class WeatherTradingAgent:
    """
    Config from env vars:
      PW_MIN_EDGE        (default 0.07)
      PW_MIN_LIQUIDITY   (default 500.0)
      PW_BANKROLL        (default 1000.0)
      PW_KELLY_FRACTION  (default 0.25)
      PW_MAX_POSITION_PCT(default 0.05)
    """

    def scan_opportunities(self) -> list[Opportunity]:
        """
        1. get_weather_markets(limit=100, closed=False)
        2. For each market: extract city, event_type
        3. get_order_book(yes_token_id) → market_price
        4. _get_model_prob(city, event_type, target_date)
        5. threshold_signal → side (YES/NO/None)
        6. kelly_size → proposed_size
        Returns Opportunity objects sorted by edge descending.
        
        _get_forecast(city):
          Try fetch_forecast_weather() → build_daily_weather_df() → engineer_features()
          On ANY exception: fall back to _synthetic_forecast(city)
        """

    def execute_opportunity(self, opp: Opportunity) -> dict:
        """
        1. risk.check_trade() → (approved, reason)
        2. If approved: trader.place_limit_order()
        3. If order placed: risk.record_trade_open()
        4. Append result to data/pw_trades/trade_log.jsonl
        Returns result dict with all details.
        """

    def run_cycle(self) -> dict:
        """scan_opportunities() + execute all → return summary dict."""
```

---

## Component 7 — `polymarket_weather/synthetic.py`

### Purpose
Generate realistic-looking weather and market data for offline dev/testing.
Called automatically when Open-Meteo or PolyMarket APIs are unreachable.

```python
def generate_synthetic_weather(
    city: str,
    start_date: str,
    end_date: str,
    seed: int = 42,
) -> list[dict]:
    """
    Generate daily weather records using:
      - Base temperature from CITY_COORDS latitude (warmer = lower lat)
      - Seasonal sinusoid: +/- 15°C amplitude over the year
      - AR(1) day-to-day noise: x[t] = 0.7 * x[t-1] + N(0, 3)
      - Precipitation: Poisson(0.3) events with Gamma-distributed intensity
      - Wind: log-normal distribution, clipped at 100 kph
    Returns list of dicts matching Open-Meteo daily format.
    """

def generate_synthetic_markets(
    cities: list[str] | None = None,
    n_per_city: int = 4,
) -> list[dict]:
    """
    Generate 40 mock PolyMarket markets (4 event types × 10 cities).
    Each market has: condition_id, question, slug, end_date, tokens, volume, liquidity.
    Prices are random but plausible (0.2–0.8 range).
    """

def generate_full_demo_dataset():
    """
    Call both functions above and save to:
      data/pw_historical/{city}.json  (3 years of synthetic weather)
      data/pw_cache/markets.json      (40 synthetic markets)
    Used as the offline fallback in run_data_pipeline().
    """
```

---

## Component 8 — `polymarket_weather/tools.py`

### Purpose
Single entry point `run_full_cycle()` that orchestrates everything.
Called by both the CLI script and the Flask server's background thread.

```python
def run_full_cycle() -> dict:
    """
    1. refresh_data()    — if data older than PW_DATA_REFRESH_HOURS (default 6h)
    2. retrain_models()  — if models older than PW_RETRAIN_DAYS (default 7d)
    3. WeatherTradingAgent().run_cycle()
    4. run_backtest_quick() — only when models were just retrained
    5. _write_digest() → data/pw_reports/YYYY-MM-DD.md
    6. mailer.send() digest to PW_OWNER_EMAIL if SMTP configured
    7. metrics.record(AGENT_KEY, {...}) for ecosystem dashboard
    Returns dict: {opportunities, trades_placed, live_trading, risk, ...}
    """
```

---

## Component 9 — `run_polymarket_weather_server.py`

### Purpose
Flask web service for Render free tier. Runs `_trading_loop()` in a daemon
thread. Render's health checker hits `/health` to keep the dyno alive.

```python
app = Flask(__name__)

@app.route("/health")
def health():
    return jsonify({"status": "ok", "cycles": _state["cycles"]}), 200

@app.route("/status")
def status():
    return jsonify(_state), 200

@app.route("/")
def index():
    """HTML page showing bankroll, P&L, last cycle, live status."""

def _trading_loop():
    """
    On boot: refresh_data(force=True) + retrain_models(force=True)
    Then loop: run_full_cycle() every PW_CYCLE_MINUTES (default 60)
    Update _state dict after each cycle for /status endpoint.
    """

def main():
    t = threading.Thread(target=_trading_loop, daemon=True)
    t.start()
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
```

---

## Component 10 — `run_polymarket_weather_auto.py`

CLI entry point for local runs and testing.

```
Usage:
  python3 run_polymarket_weather_auto.py             # one full cycle
  python3 run_polymarket_weather_auto.py --train     # force retrain
  python3 run_polymarket_weather_auto.py --backtest  # run backtest
  python3 run_polymarket_weather_auto.py --opportunities  # show opps only
  python3 run_polymarket_weather_auto.py --status    # show risk state
  python3 run_polymarket_weather_auto.py --refresh   # force data refresh
  python3 run_polymarket_weather_auto.py --live      # enable live trading
```

Wraps main logic with `run_with_healing("polymarket_weather", cycle_fn)` from
`autonomous/self_healing.py` for crash recovery.

---

## requirements-pw.txt

```
flask>=3.0.0
requests>=2.31.0
rich>=13.0.0
python-dotenv>=1.0.0
numpy>=1.26.0
xgboost>=2.0.0
py-clob-client>=0.18.0
```

---

## render.yaml (add to existing file or create new)

```yaml
services:
  - type: web
    name: polymarket-weather-agent
    runtime: python
    plan: free
    region: oregon
    healthCheckPath: /health
    buildCommand: pip install -r requirements-pw.txt
    startCommand: python3 run_polymarket_weather_server.py
    envVars:
      - key: PW_LIVE_TRADING
        value: "1"
      - key: PW_BANKROLL
        value: "1000"
      - key: PW_MIN_EDGE
        value: "0.07"
      - key: PW_MIN_LIQUIDITY
        value: "500"
      - key: AGENT_PASSWORD
        value: owner
      # Set these in Render dashboard Environment tab — never commit keys
      - key: PW_PRIVATE_KEY
        sync: false
      - key: PW_API_KEY
        sync: false
      - key: PW_API_SECRET
        sync: false
```

---

## .github/workflows/update-render-env.yml

Rotates PolyMarket credentials in Render without exposing them in plaintext.
Trigger: manual `workflow_dispatch` from GitHub Actions tab.

**Required GitHub repository secrets** (set in Settings → Secrets → Actions):
- `RENDER_API_KEY` — from Render dashboard → Account → API Keys
- `PW_PRIVATE_KEY` — PolyMarket wallet private key (64-char hex, no 0x)
- `PW_API_KEY` — PolyMarket L2 API key
- `PW_API_SECRET` — PolyMarket L2 API secret

```yaml
name: Update Render Env Vars

on:
  workflow_dispatch:

jobs:
  update-env:
    runs-on: ubuntu-latest
    steps:
      - name: Find service ID
        id: find_service
        run: |
          SERVICE_ID=$(curl -s "https://api.render.com/v1/services?limit=50" \
            -H "Authorization: Bearer ${{ secrets.RENDER_API_KEY }}" \
            -H "Accept: application/json" | \
            python3 -c "
          import json, sys
          data = json.load(sys.stdin)
          for item in data:
            svc = item.get('service', item)
            if svc.get('name') == 'polymarket-weather-agent':
              print(svc['id'])
              break
          ")
          echo "service_id=$SERVICE_ID" >> $GITHUB_OUTPUT

      - name: Get current env vars
        run: |
          curl -s "https://api.render.com/v1/services/${{ steps.find_service.outputs.service_id }}/env-vars" \
            -H "Authorization: Bearer ${{ secrets.RENDER_API_KEY }}" \
            -H "Accept: application/json" > /tmp/current_env.json

      - name: Merge and push updated env vars
        run: |
          python3 << 'EOF'
          import json, os, urllib.request

          with open('/tmp/current_env.json') as f:
              current = json.load(f)

          env_map = {}
          for item in current:
              ev = item.get('envVar', item)
              env_map[ev['key']] = ev['value']

          env_map['PW_PRIVATE_KEY'] = os.environ['PW_PRIVATE_KEY']
          env_map['PW_API_KEY']     = os.environ['PW_API_KEY']
          env_map['PW_API_SECRET']  = os.environ['PW_API_SECRET']

          payload    = [{'key': k, 'value': v} for k, v in env_map.items()]
          service_id = os.environ['SERVICE_ID']
          api_key    = os.environ['RENDER_API_KEY']
          url        = f'https://api.render.com/v1/services/{service_id}/env-vars'

          data = json.dumps(payload).encode()
          req  = urllib.request.Request(url, data=data, method='PUT')
          req.add_header('Authorization', f'Bearer {api_key}')
          req.add_header('Content-Type',  'application/json')
          req.add_header('Accept',        'application/json')

          with urllib.request.urlopen(req) as resp:
              print('Updated:', resp.status)
          EOF
        env:
          PW_PRIVATE_KEY: ${{ secrets.PW_PRIVATE_KEY }}
          PW_API_KEY:     ${{ secrets.PW_API_KEY }}
          PW_API_SECRET:  ${{ secrets.PW_API_SECRET }}
          SERVICE_ID:     ${{ steps.find_service.outputs.service_id }}
          RENDER_API_KEY: ${{ secrets.RENDER_API_KEY }}

      - name: Trigger redeploy
        run: |
          curl -s -X POST \
            "https://api.render.com/v1/services/${{ steps.find_service.outputs.service_id }}/deploys" \
            -H "Authorization: Bearer ${{ secrets.RENDER_API_KEY }}" \
            -H "Accept: application/json" \
            -H "Content-Type: application/json" \
            -d '{}'
```

---

## .env (local dev — never commit)

```bash
# PolyMarket credentials (get from polymarket.com → Profile → API)
PW_PRIVATE_KEY=<64-char-hex-without-0x>
PW_API_KEY=<uuid>
PW_API_SECRET=<base64-string>

# Trading config
PW_LIVE_TRADING=0          # set to 1 only when ready to trade real money
PW_BANKROLL=1000
PW_MIN_EDGE=0.07
PW_MIN_LIQUIDITY=500
PW_CYCLE_MINUTES=60

# Optional — email digests
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your@gmail.com
SMTP_PASS=<gmail-app-password>
PW_OWNER_EMAIL=your@gmail.com
```

---

## Critical Implementation Notes

### 1. Network fallback (required for offline dev)
Every external API call must be wrapped in try/except with a synthetic fallback:
- Open-Meteo blocked → `generate_full_demo_dataset()`
- PolyMarket Gamma blocked → `_load_cached_markets()` (read from disk cache)
- PolyMarket CLOB blocked → synthetic mid-price (0.50 ± small random noise)
- py-clob-client import error → log warning and operate in full dry-run mode

### 2. XGBoost native API (not sklearn)
Use `xgb.DMatrix` + `xgb.train(params, dtrain, num_boost_round=200)`.
Do NOT put `n_estimators` in the params dict — it causes a warning.
Save with `.save_model("path.ubj")` (binary JSON, not pickle).

### 3. City keyword matching
Use `re.search(r'\b' + re.escape(keyword) + r'\b', question.lower())` for ALL
city keywords. Without word boundaries, "la" matches "dallas", "atlanta",
"philadelphia". This is the most common bug in this system.

### 4. Kelly sizing
Always apply fractional Kelly (0.25×) to the raw Kelly formula.
Cap at `max_position_pct * bankroll` (5% default).
Never trade if Kelly is negative — return 0.0.
Minimum meaningful trade is $1.00; skip if Kelly < $1.

### 5. PolyMarket auth
- Private key: 64-char hex WITHOUT "0x" prefix
- L2 passphrase: empty string `""` (not None, not "None")
- Chain ID: 137 (Polygon mainnet)
- L2 creds are optional; L1 (private key only) is sufficient for most operations

### 6. Render free tier
- Use `type: web`, NOT `type: worker` (workers require paid plan)
- Flask server must bind to `0.0.0.0:PORT` where PORT comes from `os.getenv("PORT")`
- Health check path must return 200 within 30s of startup
- Free tier sleeps after 15 min inactivity — use UptimeRobot to ping `/health` every 5 min

### 7. GitHub Actions workflow_dispatch
- The workflow YAML file must be on the **default branch** (usually `main`) for
  the "Run workflow" button to appear in the GitHub UI
- Never pass credentials as plaintext `inputs:` — they appear in logs permanently
- Always use `${{ secrets.SECRET_NAME }}` and pass via `env:` block

### 8. Data directory structure
```
data/
  pw_historical/     # {city}.json, {city}_prices.json, last_refresh.txt
  pw_models/         # {event_type}.ubj, last_trained.txt, {event_type}_metrics.json
  pw_trades/         # trade_log.jsonl, risk_state.json
  pw_reports/        # YYYY-MM-DD.md digests
  pw_cache/          # markets.json (Gamma API cache)
```

---

## Deployment Checklist

1. Push all files to `main` branch
2. Connect repo to Render (New → Blueprint → select repo)
3. Render detects `render.yaml` and creates the `polymarket-weather-agent` web service
4. In Render dashboard → `polymarket-weather-agent` → Environment tab:
   - Set `PW_PRIVATE_KEY`, `PW_API_KEY`, `PW_API_SECRET`
5. In GitHub repo → Settings → Secrets → Actions:
   - Add `RENDER_API_KEY`, `PW_PRIVATE_KEY`, `PW_API_KEY`, `PW_API_SECRET`
6. Set up UptimeRobot free monitor: ping `https://<your-app>.onrender.com/health` every 5 min
7. To rotate credentials: GitHub → Actions → `Update Render Env Vars` → Run workflow

---

## Revenue Model (optional SaaS wrapper)

If building as a paid service:
- $97/mo: signal feed only (email digest with top opportunities)
- $297/mo: live trading + full API access
- $997/yr: white-label (run for client's own PolyMarket account)

Gate with `paywall.agent_paywall.paywall_prompt` from the `paywall/` module.
Owner bypass: `AGENT_PASSWORD` env var skips paywall entirely.
