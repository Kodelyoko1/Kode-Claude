#!/usr/bin/env python3
"""
Minimal Flask web server for Render free-tier deployment.
Runs the trading loop in a background thread; exposes /health and /status
so Render's health checks keep the dyno alive.

Use UptimeRobot (free) to ping /health every 5 min to prevent sleep.
"""
import os
import sys
import threading
import time
import logging
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
# Health / status endpoints
# ---------------------------------------------------------------------------

@app.route("/health")
def health():
    return jsonify({"status": "ok", "cycles": _state["cycles"]}), 200


@app.route("/status")
def status():
    return jsonify(_state), 200


@app.route("/")
def index():
    from polymarket_weather.resolver import resolved_count
    risk  = _state.get("last_result", {}).get("risk", {})
    pnl   = risk.get("total_pnl", 0)
    res   = _state.get("last_result", {}).get("total_resolutions", resolved_count())
    pnl_color = "#2ecc71" if pnl >= 0 else "#e74c3c"
    model_quality = "improving" if res >= 50 else f"collecting ({res}/50 resolved markets)"
    return f"""<!doctype html><html><head>
<title>PolyMarket Weather Agent</title>
<meta http-equiv="refresh" content="60">
<style>
  body{{font-family:monospace;background:#111;color:#eee;padding:2rem;}}
  h2{{color:#00bcd4;}} .card{{background:#1a1a1a;border-radius:8px;padding:1rem;margin:.5rem 0;}}
  .green{{color:#2ecc71;}} .red{{color:#e74c3c;}} a{{color:#00bcd4;}}
</style></head><body>
<h2>PolyMarket Weather Agent</h2>
<div class="card">
  <b>Cycles run:</b> {_state['cycles']}<br>
  <b>Last cycle:</b> {_state['last_cycle'] or 'pending…'}<br>
  <b>Bankroll:</b> ${risk.get('bankroll', 0):.2f}<br>
  <b>Total P&amp;L:</b> <span style="color:{pnl_color}">${pnl:+.2f}</span><br>
  <b>Open positions:</b> {risk.get('open_positions', 0)}<br>
  <b>Halted:</b> {risk.get('halted', False)}<br>
  <b>Live trading:</b> {_state.get('last_result', {}).get('live_trading', False)}<br>
  <b>Model quality:</b> {model_quality}
</div>
<div class="card">
  <a href="/status">JSON status</a> &nbsp;|&nbsp;
  <a href="/backtest">Run backtest</a> &nbsp;|&nbsp;
  <a href="/report">Latest report</a> &nbsp;|&nbsp;
  <a href="/collect">Collect resolutions</a>
</div>
</body></html>"""


@app.route("/backtest")
def backtest():
    if not _state["boot_done"]:
        return Response("Boot training not complete yet — try again in a minute.", 503)
    try:
        from polymarket_weather.tools import run_backtest_quick
        r = run_backtest_quick()
    except Exception as exc:
        return Response(f"Backtest failed: {exc}", 500)

    if "error" in r:
        return Response(f"Backtest error: {r['error']}", 500)

    rows = "".join(
        f"<tr><td>{k}</td><td><b>{v}</b></td></tr>"
        for k, v in r.items() if k != "report_path"
    )
    report_link = (
        f'<p><a href="/report">View full report</a></p>'
        if r.get("report_path") else ""
    )
    return f"""<!doctype html><html><head><title>Backtest Results</title>
<style>body{{font-family:monospace;background:#111;color:#eee;padding:2rem;}}
h2{{color:#00bcd4;}} table{{border-collapse:collapse;width:400px;}}
td{{padding:.4rem .8rem;border-bottom:1px solid #333;}} a{{color:#00bcd4;}}
</style></head><body>
<h2>Backtest Results</h2>
<table>{rows}</table>
{report_link}
<p><a href="/">← Back</a></p>
</body></html>"""


@app.route("/collect")
def collect():
    try:
        from polymarket_weather.tools import collect_resolutions
        r = collect_resolutions()
    except Exception as exc:
        return Response(f"Collection failed: {exc}", 500)
    rows = "".join(
        f"<tr><td>{k}</td><td><b>{v}</b></td></tr>" for k, v in r.items()
    )
    return f"""<!doctype html><html><head><title>Resolution Collector</title>
<style>body{{font-family:monospace;background:#111;color:#eee;padding:2rem;}}
h2{{color:#00bcd4;}} table{{border-collapse:collapse;width:400px;}}
td{{padding:.4rem .8rem;border-bottom:1px solid #333;}} a{{color:#00bcd4;}}
</style></head><body>
<h2>Resolved Market Collection</h2>
<table>{rows}</table>
<p style="color:#aaa;font-size:.85rem">
  New records are saved to data/pw_resolved/resolved.jsonl and blended
  into model retraining. Run this every few days to improve accuracy.
</p>
<p><a href="/">← Back</a></p>
</body></html>"""


@app.route("/report")
def report():
    from pathlib import Path
    reports = sorted((ROOT / "data" / "pw_reports").glob("*.md"), reverse=True)
    if not reports:
        return Response("No reports yet.", 404)
    content = reports[0].read_text().replace("\n", "<br>").replace("|", "&#124;")
    return f"""<!doctype html><html><head><title>Report</title>
<style>body{{font-family:monospace;background:#111;color:#eee;padding:2rem;font-size:.85rem;}}
a{{color:#00bcd4;}}</style></head><body>
<p><a href="/">← Back</a></p>
<pre style="white-space:pre-wrap">{reports[0].read_text()}</pre>
</body></html>"""


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
                "Cycle done | opps=%d trades=%d bankroll=$%.2f pnl=$%+.2f",
                result.get("opportunities", 0),
                result.get("trades_placed", 0),
                result.get("risk", {}).get("bankroll", 0),
                result.get("risk", {}).get("total_pnl", 0),
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
