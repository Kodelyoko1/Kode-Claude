"""
Louisiana Football Quant Agent — tools.py
Exposes run_full_cycle() called by run_louisiana_football_quant_auto.py.

One cycle:
  1. Pull sportsbook consensus odds (DK/FD/MGM via The Odds API) for NFL + CFB.
  2. Run the +EV scan and the two-way cross-book arbitrage scan against those
     odds — pure price math, no invented numbers.
  3. Pull PrizePicks projections and run the heuristic demon/goblin value scan.
  4. Pull Kalshi's open macro (non-sports) markets as a separate strategy sleeve.
  5. Write a markdown digest to data/lq_reports/YYYY-MM-DD.md, log every hit to
     data/lq_predictions.json (rolling), email the owner, and record metrics.

Signal-only for sportsbooks and PrizePicks (see odds_client.py and
prizepicks_client.py for why no execution path exists there). Kalshi
submission is dry-run unless LQ_KALSHI_LIVE=1. Not financial advice —
sports wagering carries substantial risk of loss.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from autonomous import storage, mailer, metrics

from louisiana_football_quant import odds_client, prizepicks_client, kalshi_client, analysis
from louisiana_football_quant.report import format_report

AGENT_KEY = "louisiana_football_quant"
REPORTS_DIR = ROOT / "data" / "lq_reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)
MAX_LOGGED = 1000


def gather_sportsbook_odds() -> dict:
    """Fetch odds for every configured league; returns
    {league: {"ok": bool, "events": [...], "error": ...}}."""
    out = {}
    for league in odds_client.configured_sports():
        result = odds_client.fetch_game_odds(league)
        out[league] = {
            "ok": result.get("ok", False),
            "events": result.get("data", []) if result.get("ok") else [],
            "error": result.get("error", ""),
        }
    return out


def run_cycle() -> dict:
    """One full scan + report cycle. Returns the assembled result dict."""
    by_league = gather_sportsbook_odds()

    ev_bets: list[dict] = []
    arbitrage: list[dict] = []
    odds_errors: list[str] = []
    for league, result in by_league.items():
        if not result["ok"]:
            if result["error"]:
                odds_errors.append(f"{league}: {result['error']}")
            continue
        ev_bets.extend(analysis.find_ev_bets(result["events"], league))
        arbitrage.extend(analysis.find_arbitrage(result["events"], league))

    pp_result = prizepicks_client.fetch_projections()
    prizepicks_value = (
        analysis.prizepicks_value_scan(pp_result["projections"])
        if pp_result.get("ok") else []
    )

    kalshi_result = kalshi_client.fetch_macro_markets()

    cycle = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "ev_bets": ev_bets,
        "arbitrage": arbitrage,
        "prizepicks_value": prizepicks_value,
        "prizepicks_error": pp_result.get("error", ""),
        "kalshi_markets": kalshi_result.get("markets", []),
        "kalshi_error": kalshi_result.get("error", ""),
        "kalshi_live": kalshi_client.KalshiTrader().live,
        "odds_errors": odds_errors,
    }
    cycle["report"] = format_report(cycle)
    return cycle


def _log_cycle(cycle: dict) -> None:
    log = storage.load("lq_predictions.json", [])
    if not isinstance(log, list):
        log = []
    entry = {
        "generated_at": cycle["generated_at"],
        "ev_bets": cycle["ev_bets"],
        "arbitrage": cycle["arbitrage"],
        "prizepicks_value": cycle["prizepicks_value"],
    }
    log.append(entry)
    if len(log) > MAX_LOGGED:
        log = log[-MAX_LOGGED:]
    storage.save("lq_predictions.json", log)


def _write_digest(cycle: dict) -> Path:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = REPORTS_DIR / f"{today}.md"
    now = datetime.now(timezone.utc).strftime("%H:%M UTC")
    block = f"\n## Cycle @ {now}\n\n```text\n{cycle['report']}\n```\n"
    existing = path.read_text(encoding="utf-8") if path.exists() else (
        f"# Louisiana Football Quant Agent — {today}\n\n"
        "_Not financial advice. Signal-feed output for informational/"
        "educational use; sports wagering carries substantial risk of loss._\n"
    )
    path.write_text(existing + block, encoding="utf-8")
    return path


def run_full_cycle() -> dict:
    """Called by run_louisiana_football_quant_auto.py via run_with_healing."""
    cycle = run_cycle()
    _log_cycle(cycle)
    digest_path = _write_digest(cycle)

    owner_email = os.getenv("LQ_OWNER_EMAIL") or os.getenv("SMTP_USER", "")
    email_sent = False
    if owner_email:
        try:
            mailer.send(
                agent_key=AGENT_KEY,
                to_email=owner_email,
                subject=f"[LA Football Quant] {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC Cycle",
                body=cycle["report"],
                purpose="notification",
            )
            email_sent = True
        except Exception:
            email_sent = False

    metrics.record(
        AGENT_KEY,
        ev_bets_found=len(cycle["ev_bets"]),
        arbitrage_found=len(cycle["arbitrage"]),
        prizepicks_flags=len(cycle["prizepicks_value"]),
        kalshi_markets=len(cycle["kalshi_markets"]),
    )

    cycle["digest_path"] = str(digest_path)
    cycle["email_sent"] = email_sent
    return cycle
