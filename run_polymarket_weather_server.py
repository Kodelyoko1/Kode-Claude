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

from flask import Flask, jsonify

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
    risk = _state.get("last_result", {}).get("risk", {})
    return (
        f"<h2>PolyMarket Weather Agent</h2>"
        f"<p>Cycles run: {_state['cycles']}</p>"
        f"<p>Last cycle: {_state['last_cycle']}</p>"
        f"<p>Bankroll: ${risk.get('bankroll', 0):.2f}</p>"
        f"<p>Total P&L: ${risk.get('total_pnl', 0):+.2f}</p>"
        f"<p>Halted: {risk.get('halted', False)}</p>"
        f"<p>Live trading: {_state.get('last_result', {}).get('live_trading', False)}</p>"
    )


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
