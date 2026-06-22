"""
PolyMarket Weather Trading Agent — tools.py
Exposes run_full_cycle() called by run_polymarket_weather_auto.py.

Revenue tiers: $97/mo signal feed, $297/mo with live trading, $997/yr white-label.

Full cycle:
  1. Refresh data pipeline (weather + market prices)   [hourly rate-limited]
  2. Retrain models if stale (> 7 days)                [weekly]
  3. Run the trading agent cycle (scan + trade)
  4. Write a performance digest to pw_reports/
  5. Email digest to owner if SMTP configured
  6. Update agent_metrics.json for the ecosystem dashboard
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from autonomous import storage, mailer, metrics

from polymarket_weather.data_pipeline import (
    run_data_pipeline,
    load_historical_weather,
    CITY_COORDS,
)
from polymarket_weather.model import (
    train_all_models,
    engineer_features,
    WeatherForecastModel,
)
from polymarket_weather.agent import WeatherTradingAgent, load_trade_log
from polymarket_weather.backtest import BacktestEngine, threshold_signal
from polymarket_weather.risk import RiskManager

AGENT_KEY  = "polymarket_weather"
DATA_DIR   = ROOT / "data"
REPORTS_DIR = DATA_DIR / "pw_reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

# Config from env
MIN_EDGE         = float(os.getenv("PW_MIN_EDGE", "0.07"))
BANKROLL         = float(os.getenv("PW_BANKROLL", "1000.0"))
KELLY_FRACTION   = float(os.getenv("PW_KELLY_FRACTION", "0.25"))
MAX_POSITION_PCT = float(os.getenv("PW_MAX_POSITION_PCT", "0.05"))
MIN_LIQUIDITY    = float(os.getenv("PW_MIN_LIQUIDITY", "500.0"))
RETRAIN_DAYS     = int(os.getenv("PW_RETRAIN_DAYS", "7"))
DATA_REFRESH_HOURS = int(os.getenv("PW_DATA_REFRESH_HOURS", "6"))
CITIES           = os.getenv("PW_CITIES", "new_york,chicago,miami,atlanta,dallas").split(",")


# ---------------------------------------------------------------------------
# Data freshness check
# ---------------------------------------------------------------------------

def _data_is_stale(hours: int = DATA_REFRESH_HOURS) -> bool:
    marker = DATA_DIR / "pw_historical" / "last_refresh.txt"
    if not marker.exists():
        return True
    age = time.time() - marker.stat().st_mtime
    return age > hours * 3600


def _mark_data_fresh():
    marker = DATA_DIR / "pw_historical" / "last_refresh.txt"
    marker.write_text(datetime.now(timezone.utc).isoformat())


def _model_is_stale(days: int = RETRAIN_DAYS) -> bool:
    marker = DATA_DIR / "pw_models" / "last_trained.txt"
    if not marker.exists():
        return True
    age = time.time() - marker.stat().st_mtime
    return age > days * 86400


def _mark_model_fresh():
    marker = DATA_DIR / "pw_models" / "last_trained.txt"
    marker.write_text(datetime.now(timezone.utc).isoformat())


# ---------------------------------------------------------------------------
# Sub-tasks
# ---------------------------------------------------------------------------

def refresh_data(force: bool = False) -> dict:
    if not force and not _data_is_stale():
        return {"skipped": True, "reason": "data fresh"}
    result = run_data_pipeline(cities=CITIES, lookback_years=3)
    _mark_data_fresh()
    return result


def retrain_models(force: bool = False) -> dict:
    if not force and not _model_is_stale():
        return {"skipped": True, "reason": "models fresh"}

    weather_by_city: dict[str, list[dict]] = {}
    for city in CITIES:
        records = load_historical_weather(city)
        if records:
            weather_by_city[city] = engineer_features(records)

    if not weather_by_city:
        return {"error": "no weather data; run refresh first"}

    result = train_all_models(weather_by_city)
    _mark_model_fresh()
    return result


def run_backtest_quick(lookback_records: int = 500) -> dict:
    """
    Run a quick backtest using the last N weather records from each city
    and synthetic market prices (midpoint around true climatological prob).
    Returns metrics dict.
    """
    import random
    random.seed(42)

    all_records: list[dict] = []
    for city in CITIES:
        raw = load_historical_weather(city)[-lookback_records:]
        eng = engineer_features(raw)
        for r in eng:
            # Synthetic market price: true prob ± 10% noise (simulates market mispricing)
            true_val = 1 if float(r.get("temperature_2m_max") or 0) > 32.2 else 0
            noise    = random.uniform(-0.12, 0.12)
            r["market_price"] = max(0.05, min(0.95, true_val + noise))
            r["outcome"]      = true_val
            r["market_id"]    = f"{city}_{r.get('date','')}_temp90"
            r["question"]     = f"Will temp exceed 90°F in {city}?"
            all_records.append(r)

    if not all_records:
        return {"error": "no data for backtest"}

    event_type = "temp_above_90f"
    model      = WeatherForecastModel.load(event_type)

    def model_fn(rows):
        if model._trained:
            return model.predict_proba(rows)
        return [0.5] * len(rows)

    engine = BacktestEngine(
        model_fn        = model_fn,
        bankroll        = BANKROLL,
        min_edge        = MIN_EDGE,
        sizing          = "kelly",
        kelly_fraction  = KELLY_FRACTION,
        max_position_pct= MAX_POSITION_PCT,
    )
    metrics_result = engine.run(all_records)
    report_path    = engine.generate_report("quick_backtest")
    return {**metrics_result, "report_path": str(report_path)}


# ---------------------------------------------------------------------------
# Digest generation
# ---------------------------------------------------------------------------

def _write_digest(cycle_result: dict, backtest_result: dict | None = None) -> Path:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    risk  = cycle_result.get("risk", {})
    opps  = cycle_result.get("top_opps", [])
    lines = [
        f"# PolyMarket Weather Agent Digest — {today}",
        f"Generated: {datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S UTC}",
        "",
        "## Status",
        f"- Trading: {'LIVE' if cycle_result.get('live_trading') else 'DRY-RUN'}",
        f"- Halted: {cycle_result.get('status') == 'halted'}",
        f"- Bankroll: ${risk.get('bankroll', 0):.2f}",
        f"- Daily P&L: ${risk.get('daily_pnl', 0):+.2f}",
        f"- Total P&L: ${risk.get('total_pnl', 0):+.2f}",
        f"- Open positions: {risk.get('open_positions', 0)}",
        f"- Trades today: {cycle_result.get('trades_placed', 0)}",
        f"- Opportunities found: {cycle_result.get('opportunities', 0)}",
        "",
    ]
    if opps:
        lines += [
            "## Top Opportunities",
            "| Question | City | Side | Edge | Model P | Market P | EV |",
            "|----------|------|------|------|---------|----------|----|",
        ]
        for o in opps:
            lines.append(
                f"| {o['question'][:45]}… | {o['city']} | {o['side']} | "
                f"{o['edge']:.3f} | {o['model_prob']:.3f} | "
                f"{o['market_price']:.3f} | {o['ev']:.4f} |"
            )
        lines.append("")

    if backtest_result and "roi_pct" in backtest_result:
        lines += [
            "## Backtest (quick, synthetic markets)",
            f"- Trades: {backtest_result.get('trades', 0)}",
            f"- Win rate: {backtest_result.get('win_rate', 0):.1%}",
            f"- ROI: {backtest_result.get('roi_pct', 0):+.1f}%",
            f"- Sharpe: {backtest_result.get('sharpe', 0):.2f}",
            f"- Max drawdown: {backtest_result.get('max_drawdown_pct', 0):.1f}%",
            f"- Brier score: {backtest_result.get('brier_score', 0):.4f}",
            "",
        ]

    recent_trades = load_trade_log(10)
    if recent_trades:
        lines += ["## Recent Trades", ""]
        for t in recent_trades[-5:]:
            approved = "✓" if t.get("approved") else "✗"
            lines.append(
                f"{approved} {t.get('timestamp','')[:10]} | "
                f"{t.get('side','')} | edge={t.get('edge',0):.3f} | "
                f"{t.get('question','')[:50]}"
            )
        lines.append("")

    path = REPORTS_DIR / f"{today}.md"
    path.write_text("\n".join(lines))
    return path


# ---------------------------------------------------------------------------
# Main cycle
# ---------------------------------------------------------------------------

def run_full_cycle() -> dict:
    """
    Called by run_polymarket_weather_auto.py via run_with_healing.
    Returns a summary dict consumed by the entry point for display.
    """
    step_results: dict = {}

    # 1. Refresh data if stale
    try:
        step_results["data_refresh"] = refresh_data()
    except Exception as exc:
        step_results["data_refresh"] = {"error": str(exc)}

    # 2. Retrain if stale
    try:
        step_results["retrain"] = retrain_models()
    except Exception as exc:
        step_results["retrain"] = {"error": str(exc)}

    # 3. Trading agent cycle
    agent = WeatherTradingAgent(
        min_edge         = MIN_EDGE,
        min_liquidity    = MIN_LIQUIDITY,
        bankroll         = BANKROLL,
        kelly_fraction   = KELLY_FRACTION,
        max_position_pct = MAX_POSITION_PCT,
    )
    cycle_result = agent.run_cycle()
    step_results["cycle"] = cycle_result

    # 4. Quick backtest (weekly; skip if models weren't retrained recently)
    backtest_result = None
    if not step_results["retrain"].get("skipped"):
        try:
            backtest_result = run_backtest_quick()
            step_results["backtest"] = backtest_result
        except Exception as exc:
            step_results["backtest"] = {"error": str(exc)}

    # 5. Write digest
    digest_path = _write_digest(cycle_result, backtest_result)
    step_results["digest_path"] = str(digest_path)

    # 6. Email digest to owner
    owner_email = os.getenv("PW_OWNER_EMAIL") or os.getenv("SMTP_USER", "")
    if owner_email:
        try:
            body = digest_path.read_text()
            mailer.send(
                to      = owner_email,
                subject = f"[PolyMarket Weather] Daily Digest {datetime.now(timezone.utc):%Y-%m-%d}",
                body    = body,
            )
            step_results["email_sent"] = True
        except Exception as exc:
            step_results["email_sent"] = False
            step_results["email_error"] = str(exc)

    # 7. Agent metrics
    try:
        risk_state = agent.risk.status_dict()
        metrics.record(AGENT_KEY, {
            "bankroll":      risk_state["bankroll"],
            "daily_pnl":     risk_state["daily_pnl"],
            "total_pnl":     risk_state["total_pnl"],
            "opportunities": cycle_result.get("opportunities", 0),
            "trades_placed": cycle_result.get("trades_placed", 0),
        })
    except Exception:
        pass

    return {
        "opportunities":  cycle_result.get("opportunities", 0),
        "trades_placed":  cycle_result.get("trades_placed", 0),
        "data_refreshed": not step_results["data_refresh"].get("skipped"),
        "models_retrained": not step_results["retrain"].get("skipped"),
        "digest_path":    str(digest_path),
        "live_trading":   agent.trader.live,
        "risk":           agent.risk.status_dict(),
        "backtest":       backtest_result,
    }
