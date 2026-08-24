#!/usr/bin/env python3
"""
Flask web server for Render free-tier deployment.
Runs the trading loop in a background thread; exposes /health, /status,
/backtest, /report, /collect, /trades, and /trades.json so the owner
can monitor the agent.

Use UptimeRobot (free) to ping /health every 5 min to prevent sleep.
"""
import os
import sys
import threading
import time
import logging
import json as _json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from flask import Flask, jsonify, Response

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("pw-server")

app = Flask(__name__)

CYCLE_MINUTES = int(os.getenv("PW_CYCLE_MINUTES", "60"))

_state = {
    "started":       datetime.now(timezone.utc).isoformat(),
    "cycles":        0,
    "last_cycle":    None,
    "last_result":   {},
    "running":       False,
    "boot_done":     False,
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_trades() -> list:
    # Agent writes JSONL (one record per line); try that first.
    jsonl_path = ROOT / "data" / "pw_trades" / "trade_log.jsonl"
    if jsonl_path.exists():
        try:
            trades = []
            for line in jsonl_path.read_text().strip().splitlines():
                line = line.strip()
                if line:
                    try:
                        trades.append(_json.loads(line))
                    except Exception:
                        pass
            if trades:
                return trades
        except Exception:
            pass
    # Fallback: legacy JSON array format
    json_path = ROOT / "data" / "pw_trades" / "trade_log.json"
    if json_path.exists():
        try:
            return _json.loads(json_path.read_text()) or []
        except Exception:
            pass
    return []


_DARK_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body { background: #0d0d0d; color: #e0e0e0; font-family: 'Segoe UI', sans-serif; padding: 24px; }
h1 { color: #00c8ff; font-size: 1.5rem; margin-bottom: 6px; }
h2 { color: #aaa; font-size: 1rem; margin: 20px 0 8px; text-transform: uppercase; letter-spacing: .05em; }
.badge { display: inline-block; padding: 2px 10px; border-radius: 12px; font-size: .8rem; font-weight: 600; }
.live  { background: #00c853; color: #000; }
.dry   { background: #ff6d00; color: #000; }
.halted{ background: #d32f2f; color: #fff; }
.ok    { background: #1b5e20; color: #a5d6a7; }
.nav   { margin: 16px 0; display: flex; gap: 12px; flex-wrap: wrap; }
.nav a { color: #00c8ff; text-decoration: none; padding: 6px 14px; border: 1px solid #00c8ff; border-radius: 6px; font-size: .85rem; }
.nav a:hover { background: #00c8ff22; }
table  { border-collapse: collapse; width: 100%; margin-top: 8px; }
th, td { padding: 8px 12px; text-align: left; border-bottom: 1px solid #1e1e1e; font-size: .88rem; }
th     { color: #888; font-weight: 600; }
.pos   { color: #69f0ae; }
.neg   { color: #ff5252; }
.dim   { color: #666; font-size: .8rem; }
.card  { background: #141414; border: 1px solid #222; border-radius: 10px; padding: 16px; margin-bottom: 16px; }
.grid  { display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 12px; }
.stat  { background: #181818; border: 1px solid #2a2a2a; border-radius: 8px; padding: 14px; }
.stat .val { font-size: 1.4rem; font-weight: 700; color: #00c8ff; }
.stat .lbl { font-size: .75rem; color: #666; margin-top: 4px; }
pre    { background: #111; border: 1px solid #222; border-radius: 6px; padding: 14px;
         overflow-x: auto; font-size: .82rem; line-height: 1.5; white-space: pre-wrap; }
"""


def _page(title: str, body: str, refresh: int = 0) -> str:
    refresh_tag = f'<meta http-equiv="refresh" content="{refresh}">' if refresh else ""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  {refresh_tag}
  <title>{title}</title>
  <style>{_DARK_CSS}</style>
</head>
<body>
{body}
</body>
</html>"""


# ---------------------------------------------------------------------------
# Health / status endpoints
# ---------------------------------------------------------------------------

@app.route("/health")
def health():
    return jsonify({"status": "ok", "cycles": _state["cycles"]}), 200


@app.route("/status")
def status():
    """Full status including trade log — single fetch captures everything."""
    payload = dict(_state)
    payload["trades"] = _load_trades()
    return jsonify(payload), 200


# ---------------------------------------------------------------------------
# Main dashboard
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    r    = _state.get("last_result", {})
    risk = r.get("risk", {})

    live    = r.get("live_trading", False)
    halted  = risk.get("halted", False)
    bankroll = risk.get("bankroll", 0)
    daily_pnl = risk.get("daily_pnl", 0)
    total_pnl = risk.get("total_pnl", 0)
    open_pos  = risk.get("open_positions", 0)

    total_res = r.get("total_resolutions", 0)
    model_quality = (
        f"<span class='badge ok'>improving ({total_res} resolved markets)</span>"
        if total_res >= 50
        else f"<span style='color:#aaa'>collecting ({total_res}/50 resolved markets)</span>"
    )

    mode_badge = (
        "<span class='badge halted'>HALTED</span>" if halted
        else ("<span class='badge live'>LIVE</span>" if live
              else "<span class='badge dry'>DRY-RUN</span>")
    )

    pnl_cls = "pos" if total_pnl >= 0 else "neg"
    dpnl_cls = "pos" if daily_pnl >= 0 else "neg"

    stats_html = f"""
    <div class="grid">
      <div class="stat"><div class="val">${bankroll:.2f}</div><div class="lbl">Bankroll</div></div>
      <div class="stat"><div class="val {pnl_cls}">${total_pnl:+.2f}</div><div class="lbl">Total P&L</div></div>
      <div class="stat"><div class="val {dpnl_cls}">${daily_pnl:+.2f}</div><div class="lbl">Daily P&L</div></div>
      <div class="stat"><div class="val">{open_pos}</div><div class="lbl">Open Positions</div></div>
      <div class="stat"><div class="val">{r.get('trades_placed', 0)}</div><div class="lbl">Trades (this cycle)</div></div>
      <div class="stat"><div class="val">{r.get('opportunities', 0)}</div><div class="lbl">Opportunities found</div></div>
      <div class="stat"><div class="val">{_state['cycles']}</div><div class="lbl">Total cycles</div></div>
      <div class="stat"><div class="val">{r.get('new_resolutions', 0)}</div><div class="lbl">New resolutions</div></div>
    </div>"""

    body = f"""
    <h1>&#9925; PolyMarket Weather Agent</h1>
    <p class="dim">Started: {_state['started']} &middot; Last cycle: {_state['last_cycle'] or '&mdash;'}</p>
    <p style="margin-top:8px">Mode: {mode_badge} &nbsp;|&nbsp; Model quality: {model_quality}</p>

    <nav class="nav">
      <a href="/trades">Live positions</a>
      <a href="/scan">Scan markets</a>
      <a href="/risk">Risk state</a>
      <a href="/status">JSON status</a>
      <a href="/backtest">Run backtest</a>
      <a href="/report">Latest report</a>
      <a href="/collect">Collect resolutions</a>
    </nav>

    <div class="card">
      <h2>Portfolio</h2>
      {stats_html}
    </div>

    <p class="dim" style="margin-top:16px">Auto-refreshes every 60 s</p>
    """
    return _page("PolyMarket Weather Agent", body, refresh=60)


# ---------------------------------------------------------------------------
# /backtest  — run quick backtest on-demand
# ---------------------------------------------------------------------------

@app.route("/backtest")
def backtest_route():
    from polymarket_weather.tools import run_backtest_quick
    try:
        result = run_backtest_quick()
    except Exception as exc:
        result = {"error": str(exc)}

    if "error" in result:
        rows = f"<tr><td colspan='2' style='color:#ff5252'>{result['error']}</td></tr>"
    else:
        skip = {"report_path"}
        rows = "".join(
            f"<tr><td>{k}</td><td>{v}</td></tr>"
            for k, v in result.items() if k not in skip
        )
        if result.get("report_path"):
            rows += f"<tr><td>report_path</td><td><span class='dim'>{result['report_path']}</span></td></tr>"

    body = f"""
    <h1>&#9925; Backtest Results</h1>
    <nav class="nav"><a href="/">&larr; Dashboard</a></nav>
    <div class="card">
      <table>
        <thead><tr><th>Metric</th><th>Value</th></tr></thead>
        <tbody>{rows}</tbody>
      </table>
    </div>"""
    return _page("Backtest — PolyMarket Weather", body)


# ---------------------------------------------------------------------------
# /report  — serve latest digest
# ---------------------------------------------------------------------------

@app.route("/report")
def report_route():
    reports_dir = ROOT / "data" / "pw_reports"
    reports = sorted(reports_dir.glob("*.md"), reverse=True) if reports_dir.exists() else []
    if not reports:
        body = """
        <h1>&#9925; Latest Report</h1>
        <nav class="nav"><a href="/">&larr; Dashboard</a></nav>
        <p style="color:#aaa;margin-top:12px">No reports yet — run a full cycle first.</p>"""
        return _page("Report — PolyMarket Weather", body)

    content = reports[0].read_text()
    body = f"""
    <h1>&#9925; Latest Report <span class="dim">({reports[0].name})</span></h1>
    <nav class="nav"><a href="/">&larr; Dashboard</a></nav>
    <pre>{content}</pre>"""
    return _page("Report — PolyMarket Weather", body)


# ---------------------------------------------------------------------------
# /collect  — trigger resolution collection on-demand
# ---------------------------------------------------------------------------

@app.route("/collect")
def collect_route():
    from polymarket_weather.tools import collect_resolutions
    try:
        result = collect_resolutions()
    except Exception as exc:
        result = {"error": str(exc)}

    rows = "".join(
        f"<tr><td>{k}</td><td>{v}</td></tr>"
        for k, v in result.items()
    )
    body = f"""
    <h1>&#9925; Collect Resolutions</h1>
    <nav class="nav"><a href="/">&larr; Dashboard</a></nav>
    <div class="card">
      <table>
        <thead><tr><th>Key</th><th>Value</th></tr></thead>
        <tbody>{rows}</tbody>
      </table>
    </div>"""
    return _page("Collect — PolyMarket Weather", body)


# ---------------------------------------------------------------------------
# /scan  — live opportunity scan (no trades placed)
# /risk  — risk manager state + /risk/reset to clear stale positions
# ---------------------------------------------------------------------------

@app.route("/scan")
def scan_route():
    """Run scan_opportunities() and return detailed pipeline diagnostics."""
    from polymarket_weather.api_client import get_weather_markets, get_order_book
    from polymarket_weather.agent import WeatherTradingAgent, _extract_city, _extract_event_type
    from polymarket_weather.tools import MIN_EDGE, MIN_LIQUIDITY, BANKROLL, KELLY_FRACTION, MAX_POSITION_PCT

    agent = WeatherTradingAgent(
        min_edge=MIN_EDGE, min_liquidity=MIN_LIQUIDITY,
        bankroll=BANKROLL, kelly_fraction=KELLY_FRACTION,
        max_position_pct=MAX_POSITION_PCT,
    )

    stats = {
        "total_markets": 0,
        "closed_skipped": 0,
        "low_liquidity_skipped": 0,
        "no_city_match": 0,
        "no_yes_token": 0,
        "price_at_extreme": 0,
        "below_edge_threshold": 0,
        "opportunities": 0,
        "samples": [],  # first 10 markets with diagnostics
    }

    try:
        markets = get_weather_markets(limit=200, closed=False)
        stats["total_markets"] = len(markets)
        for m in markets:
            diag = {"question": m.question[:80], "liquidity": m.liquidity, "closed": m.closed}
            if m.closed:
                stats["closed_skipped"] += 1
                continue
            if m.liquidity < MIN_LIQUIDITY:
                stats["low_liquidity_skipped"] += 1
                diag["skip"] = f"low_liquidity ({m.liquidity:.0f} < {MIN_LIQUIDITY})"
                if len(stats["samples"]) < 10:
                    stats["samples"].append(diag)
                continue
            city = _extract_city(m.question)
            if city is None:
                stats["no_city_match"] += 1
                diag["skip"] = "no_city_match"
                if len(stats["samples"]) < 10:
                    stats["samples"].append(diag)
                continue
            diag["city"] = city
            yes_token = m.yes_token_id()
            if not yes_token:
                stats["no_yes_token"] += 1
                diag["skip"] = "no_yes_token"
                if len(stats["samples"]) < 10:
                    stats["samples"].append(diag)
                continue
            try:
                book = get_order_book(yes_token)
                mp = book.mid_price()
                diag["market_price"] = round(mp, 4)
            except Exception as e:
                diag["skip"] = f"book_error: {e}"
                if len(stats["samples"]) < 10:
                    stats["samples"].append(diag)
                continue
            if mp <= 0.01 or mp >= 0.99:
                stats["price_at_extreme"] += 1
                diag["skip"] = f"price_extreme ({mp:.3f})"
                if len(stats["samples"]) < 10:
                    stats["samples"].append(diag)
                continue
            event_type = _extract_event_type(m.question)
            target_date = (m.end_date or "")[:10]
            model_prob = agent._get_model_prob(city, event_type, target_date)
            edge = abs(model_prob - mp)
            diag["event_type"] = event_type
            diag["model_prob"] = round(model_prob, 4)
            diag["edge"] = round(edge, 4)
            if edge < MIN_EDGE:
                stats["below_edge_threshold"] += 1
                diag["skip"] = f"low_edge ({edge:.3f} < {MIN_EDGE})"
            else:
                stats["opportunities"] += 1
                diag["skip"] = None
            if len(stats["samples"]) < 10:
                stats["samples"].append(diag)
    except Exception as exc:
        stats["error"] = str(exc)

    return jsonify(stats), 200


@app.route("/risk")
def risk_route():
    """Return full risk manager state."""
    from polymarket_weather.risk import RiskManager
    from polymarket_weather.tools import MIN_EDGE, BANKROLL, MAX_POSITION_PCT
    rm = RiskManager(starting_bankroll=BANKROLL, min_edge=MIN_EDGE, max_position_pct=MAX_POSITION_PCT)
    state = dict(rm._state)
    return jsonify(state), 200


@app.route("/risk/reset", methods=["GET", "POST"])
def risk_reset_route():
    """Clear stale open positions from risk state (keeps bankroll and P&L)."""
    from polymarket_weather.risk import RiskManager, STATE_FILE
    from polymarket_weather.tools import MIN_EDGE, BANKROLL, MAX_POSITION_PCT
    import json as _j
    rm = RiskManager(starting_bankroll=BANKROLL, min_edge=MIN_EDGE, max_position_pct=MAX_POSITION_PCT)
    old_count = len(rm._state.get("open_positions", []))
    rm._state["open_positions"] = []
    rm._save_state()
    return jsonify({"cleared": old_count, "message": "open_positions reset to []"}), 200


# ---------------------------------------------------------------------------
# /trades.json  — raw JSON trade log
# /trades       — HTML trade log table
# ---------------------------------------------------------------------------

@app.route("/trades.json")
def trades_json():
    return jsonify(_load_trades()), 200


@app.route("/trades")
def trades_route():
    trades = _load_trades()

    if not trades:
        body = """
        <h1>&#9925; Trade Log</h1>
        <nav class="nav"><a href="/">&larr; Dashboard</a></nav>
        <p style="color:#aaa;margin-top:12px">No trades recorded yet.</p>"""
        return _page("Trades — PolyMarket Weather", body)

    rows = ""
    for t in reversed(trades):
        approved = "&#10003;" if t.get("approved") else "&#10007;"
        pnl = t.get("pnl", None)
        pnl_str = f"${pnl:+.2f}" if pnl is not None else "&mdash;"
        pnl_cls = "pos" if (pnl or 0) >= 0 else "neg"
        side_cls = "pos" if t.get("side") == "YES" else "neg"
        rows += (
            f"<tr>"
            f"<td>{t.get('timestamp','')[:19].replace('T',' ')}</td>"
            f"<td><span class='{side_cls}'>{t.get('side','')}</span></td>"
            f"<td>{t.get('edge',0):.3f}</td>"
            f"<td>{t.get('model_prob',0):.3f}</td>"
            f"<td>{t.get('market_price',0):.3f}</td>"
            f"<td>${t.get('size',0):.2f}</td>"
            f"<td class='{pnl_cls}'>{pnl_str}</td>"
            f"<td>{approved}</td>"
            f"<td class='dim'>{t.get('question','')[:55]}</td>"
            f"</tr>"
        )

    body = f"""
    <h1>&#9925; Trade Log <span class="dim">({len(trades)} total)</span></h1>
    <nav class="nav"><a href="/">&larr; Dashboard</a></nav>
    <div class="card" style="overflow-x:auto">
      <table>
        <thead>
          <tr>
            <th>Time (UTC)</th><th>Side</th><th>Edge</th>
            <th>Model P</th><th>Mkt P</th><th>Size</th>
            <th>P&amp;L</th><th>Placed</th><th>Question</th>
          </tr>
        </thead>
        <tbody>{rows}</tbody>
      </table>
    </div>"""
    return _page("Trades — PolyMarket Weather", body)


# ---------------------------------------------------------------------------
# Trading loop (background thread)
# ---------------------------------------------------------------------------

def _boot_train():
    log.info("Boot: refreshing data and training models…")
    try:
        from polymarket_weather.tools import refresh_data, retrain_models
        refresh_data(force=True)
        retrain_models(force=True)
        log.info("Boot training complete.")
    except Exception as exc:
        log.warning("Boot training failed (non-fatal): %s", exc)
    _state["boot_done"] = True


def _trading_loop():
    _boot_train()

    from polymarket_weather.tools import run_full_cycle

    while True:
        _state["running"] = True
        try:
            log.info("─── Cycle #%d ───", _state["cycles"] + 1)
            result = run_full_cycle()
            _state["cycles"]      += 1
            _state["last_cycle"]   = datetime.now(timezone.utc).isoformat()
            _state["last_result"]  = result
            _state["running"]      = False
            log.info(
                "Cycle done | opps=%d trades=%d bankroll=$%.2f pnl=$%+.2f new_res=%d total_res=%d",
                result.get("opportunities", 0),
                result.get("trades_placed", 0),
                result.get("risk", {}).get("bankroll", 0),
                result.get("risk", {}).get("total_pnl", 0),
                result.get("new_resolutions", 0),
                result.get("total_resolutions", 0),
            )
        except Exception as exc:
            log.error("Cycle failed: %s", exc, exc_info=True)
            _state["running"] = False

        time.sleep(CYCLE_MINUTES * 60)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    t = threading.Thread(target=_trading_loop, daemon=True)
    t.start()

    port = int(os.getenv("PORT", "10000"))
    log.info("Web server starting on port %d", port)
    app.run(host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
