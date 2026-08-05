"""
ICT Gold & Crude Prediction Agent — tools.py
Exposes run_full_cycle() called by run_ict_predictor_auto.py.

Full cycle (one pass, meant to be cron'd every 5-15 min during killzones):
  1. For each configured asset (GC, CL):
       - Step 1: is it inside a killzone that's valid for this asset?
       - If yes: fetch 15M (bias) + 5M (precision) candles, run the ICT
         Decision & Validation Matrix, format the report template.
       - If no: emit a "NO TRADE - Outside Killzone" report.
  2. Append every prediction to data/ip_predictions.json (rolling log).
  3. Append today's reports to data/ip_reports/YYYY-MM-DD.md.
  4. Email the digest to the owner if SMTP is configured.
  5. Record agent_metrics.json for the ecosystem dashboard.

Not financial advice — signal-feed output for informational/educational
submission on UpsideOnly.com. Trading futures carries substantial risk.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from autonomous import storage, mailer, metrics

from ict_predictor import killzone
from ict_predictor.data_feed import get_candles, FeedError
from ict_predictor.predictor import build_prediction
from ict_predictor.report import format_report, no_trade_report

AGENT_KEY = "ict_predictor"
REPORTS_DIR = ROOT / "data" / "ip_reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

ASSETS = [a.strip().upper() for a in os.getenv("IP_ASSETS", "GC,CL").split(",") if a.strip()]
HTF_INTERVAL = "15m"
LTF_INTERVAL = os.getenv("IP_LTF_INTERVAL", "5m")
MAX_LOGGED_PREDICTIONS = 1000


def analyze_asset(asset: str) -> dict:
    """Run the full Decision & Validation Matrix for one asset. Always
    returns a prediction dict (possibly a NO TRADE) — never raises."""
    kz = killzone.current_killzone()

    if not killzone.asset_active_in_killzone(asset, kz):
        if kz is None:
            reason = (
                f"Outside all killzones — next window opens at "
                f"{killzone.next_killzone_open()}"
            )
        else:
            reason = f"{kz} is active but not an ideal window for {asset}"
        return no_trade_report(asset, reason)

    try:
        htf = get_candles(asset, HTF_INTERVAL)
        ltf = get_candles(asset, LTF_INTERVAL)
    except FeedError as exc:
        pred = no_trade_report(asset, f"Price feed unavailable: {exc}")
        pred["killzone"] = kz
        return pred

    pred = build_prediction(asset, kz, htf, ltf, ltf_label=LTF_INTERVAL.upper())
    return pred


def _log_predictions(predictions: list[dict]) -> None:
    log = storage.load("ip_predictions.json", [])
    if not isinstance(log, list):
        log = []
    log.extend(predictions)
    if len(log) > MAX_LOGGED_PREDICTIONS:
        log = log[-MAX_LOGGED_PREDICTIONS:]
    storage.save("ip_predictions.json", log)


def _write_digest(predictions: list[dict]) -> Path:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = REPORTS_DIR / f"{today}.md"
    now = datetime.now(timezone.utc).strftime("%H:%M UTC")

    block = [f"## Cycle @ {now}", ""]
    for pred in predictions:
        block.append(f"### {pred.get('asset')} — {pred.get('direction')}")
        block.append("```text")
        block.append(pred["report"])
        block.append("```")
        block.append("")

    existing = path.read_text() if path.exists() else (
        f"# ICT Gold & Crude Prediction Agent — {today}\n\n"
        f"_Not financial advice. Signal-feed output for UpsideOnly.com "
        f"submission; trading futures carries substantial risk._\n\n"
    )
    path.write_text(existing + "\n".join(block) + "\n")
    return path


def run_full_cycle() -> dict:
    """Called by run_ict_predictor_auto.py via run_with_healing."""
    predictions = []
    for asset in ASSETS:
        pred = analyze_asset(asset)
        pred["report"] = format_report(pred)
        pred["generated_at"] = datetime.now(timezone.utc).isoformat()
        predictions.append(pred)

    _log_predictions(predictions)
    digest_path = _write_digest(predictions)

    owner_email = os.getenv("IP_OWNER_EMAIL") or os.getenv("SMTP_USER", "")
    email_sent = False
    if owner_email:
        try:
            body = "\n\n".join(p["report"] for p in predictions)
            mailer.send(
                agent_key=AGENT_KEY,
                to_email=owner_email,
                subject=f"[ICT Predictor] {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC Cycle",
                body=body,
                purpose="notification",
            )
            email_sent = True
        except Exception:
            email_sent = False

    trade_signals = sum(1 for p in predictions if p["direction"] in ("LONG", "SHORT"))
    metrics.record(
        AGENT_KEY,
        predictions_generated=len(predictions),
        trade_signals=trade_signals,
        no_trade=len(predictions) - trade_signals,
    )

    return {
        "predictions": predictions,
        "trade_signals": trade_signals,
        "killzone": killzone.current_killzone(),
        "digest_path": str(digest_path),
        "email_sent": email_sent,
    }
