#!/usr/bin/env python3
"""
Render worker loop for the PolyMarket Weather Trading Agent.

Runs one full cycle every PW_CYCLE_MINUTES (default 60), then sleeps.
Render keeps this process alive as a background worker — no cron needed.

Startup sequence:
  1. Force-refresh weather data + train models on first boot
  2. Then enter the hourly scan → trade → sleep loop
"""
import os
import sys
import time
import signal
import logging
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("pw-worker")

CYCLE_MINUTES = int(os.getenv("PW_CYCLE_MINUTES", "60"))
_shutdown = False


def _handle_sigterm(sig, frame):
    global _shutdown
    log.info("SIGTERM received — finishing current cycle then exiting")
    _shutdown = True


signal.signal(signal.SIGTERM, _handle_sigterm)


def boot_train():
    """On first start, refresh data and train all models."""
    log.info("Boot: refreshing weather data and training models…")
    try:
        from polymarket_weather.tools import refresh_data, retrain_models
        r = refresh_data(force=True)
        log.info("Data refresh: %s", r)
        t = retrain_models(force=True)
        for event, m in t.items():
            if "error" not in m:
                log.info("Model %s — accuracy=%.4f brier_skill=%.4f n=%d",
                         event, m.get("accuracy", 0), m.get("brier_skill", 0), m.get("n", 0))
    except Exception as exc:
        log.warning("Boot training failed (non-fatal): %s", exc)


def run_cycle():
    from polymarket_weather.tools import run_full_cycle
    result = run_full_cycle()
    log.info(
        "Cycle done | opps=%d trades=%d live=%s bankroll=$%.2f pnl=$%+.2f halted=%s",
        result.get("opportunities", 0),
        result.get("trades_placed", 0),
        result.get("live_trading", False),
        result.get("risk", {}).get("bankroll", 0),
        result.get("risk", {}).get("total_pnl", 0),
        result.get("risk", {}).get("halted", False),
    )
    if result.get("risk", {}).get("halted"):
        log.warning("KILL SWITCH ACTIVE: %s", result["risk"].get("halt_reason", ""))
    return result


def main():
    log.info("PolyMarket Weather Worker starting (cycle=%dm, live=%s)",
             CYCLE_MINUTES, os.getenv("PW_LIVE_TRADING") == "1")

    boot_train()

    cycle_num = 0
    while not _shutdown:
        cycle_num += 1
        log.info("─── Cycle #%d ───", cycle_num)
        try:
            run_cycle()
        except Exception as exc:
            log.error("Cycle #%d failed: %s", cycle_num, exc, exc_info=True)

        if _shutdown:
            break

        next_run = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        log.info("Sleeping %d min until next cycle…", CYCLE_MINUTES)
        time.sleep(CYCLE_MINUTES * 60)

    log.info("Worker shut down cleanly after %d cycles.", cycle_num)


if __name__ == "__main__":
    main()
