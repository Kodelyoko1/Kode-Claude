"""
DropshipScout preflight + revenue-pipeline audit.

The product: $47/mo digest of trending TikTok-shop hashtags + Amazon Best Sellers,
plus a public landing page (website/dropship_scout_trends.html) that drives
signups by showing the top 3 of each section as a free preview.

Three silent degradation modes the existing cycle hides:
  1. TikTok live feed dark → evergreen fallback kicks in. Digest still has data
     but the public page loses its rank labels and the "live snapshot" promise
     in the marketing copy gets stale.
  2. One Amazon category returns []. Aggregate still looks fine.
  3. Cron stopped firing entirely. The public page's "Updated" date in the
     eyebrow goes stale — bad lead-magnet experience but no agent-level alarm.

This module answers, in one read-only command:
  1. Channels: SMTP (needed for subscriber delivery)
  2. Per-source health: 1 TikTok + 5 Amazon categories
  3. TikTok mode: live vs evergreen fallback ratio
  4. Public page freshness: mtime vs. a 24h staleness budget
  5. Subscribers + MRR
"""
from __future__ import annotations

import json
import os
import smtplib
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from dropship_scout.health import summary as health_summary, unhealthy_sources, ALERT_AFTER_ZEROS

DATA_DIR     = Path(__file__).parent.parent / "data"
WEBSITE_OUT  = Path(__file__).parent.parent / "website" / "dropship_scout_trends.html"
LATEST_FILE  = DATA_DIR / "ds_latest_trends.json"
SUBS_FILE    = DATA_DIR / "ds_subscribers.json"
DIGESTS_DIR  = DATA_DIR / "ds_digests"

# Plan price for MRR math — single plan today ($47/mo).
PLAN_PRICE = 47

# How stale the public page can be before we flag it. Cron is hourly, so 24h
# means more than 24 missed runs without a refresh.
PAGE_STALENESS_HOURS = int(os.environ.get("DS_PAGE_STALENESS_HOURS", "24"))


@dataclass
class Check:
    name: str
    severity: str   # "P0" | "P1" | "info"
    status: str     # "pass" | "fail" | "warn" | "info"
    detail: str = ""
    fix_hint: str = ""


def _load(path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return default


# ─────────────────────────── Channels ───────────────────────────

def check_smtp() -> Check:
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER", "")
    pwd  = os.environ.get("SMTP_PASS", "")
    if not (user and pwd):
        return Check(name="SMTP creds", severity="P0", status="fail",
                     detail="SMTP_USER / SMTP_PASS not set",
                     fix_hint="Required to deliver weekly digests to paying subscribers")
    try:
        with smtplib.SMTP(host, port, timeout=10) as srv:
            srv.starttls()
            srv.login(user, pwd)
        return Check(name="SMTP auth", severity="P0", status="pass",
                     detail=f"{host}:{port} as {user}")
    except smtplib.SMTPAuthenticationError as e:
        return Check(name="SMTP auth", severity="P0", status="fail",
                     detail=f"Gmail rejected: {str(e)[:120]}",
                     fix_hint="Re-generate the Gmail app password")
    except Exception as e:
        return Check(name="SMTP connection", severity="P0", status="fail",
                     detail=f"{type(e).__name__}: {str(e)[:120]}")


# ─────────────────────────── Per-source health ───────────────────────────

def check_source_health() -> Check:
    s = health_summary()
    if s["sources"] == 0:
        return Check(
            name="Per-source health",
            severity="P1", status="warn",
            detail="no sources tracked yet — run a cycle first",
            fix_hint="Run `python3 run_dropship_scout_auto.py` once to populate ds_source_health.json",
        )
    bad = unhealthy_sources()
    if bad:
        names = ", ".join(f"{c['source']}(-{c['consecutive_zeros']})" for c in bad[:5])
        return Check(
            name="Per-source health",
            severity="P1", status="warn",
            detail=(f"{s['healthy']}/{s['sources']} healthy  ·  "
                    f"{s['warning']} source(s) with ≥{ALERT_AFTER_ZEROS} zeros: {names}"),
            fix_hint=("Source layout likely changed. For TikTok: inspect Creative Center's "
                      "current JSON shape and update the regex in scrape_tiktok_trends(). "
                      "For Amazon: re-check the gridItemRoot selector + anti-bot UA."),
        )
    return Check(name="Per-source health", severity="info", status="info",
                 detail=f"{s['healthy']}/{s['sources']} healthy  ·  "
                        f"total found all-time: {s['total_found_all_time']}")


# ─────────────────────────── TikTok mode ───────────────────────────

def check_tiktok_mode() -> Check:
    """Are we still getting live Creative Center data, or running on evergreen fallback?"""
    trends = _load(LATEST_FILE, {})
    if not isinstance(trends, dict):
        return Check(name="TikTok mode", severity="info", status="info",
                     detail="(no trend snapshot yet)")
    tt = trends.get("tiktok_hashtags", [])
    if not tt:
        return Check(name="TikTok mode", severity="P1", status="warn",
                     detail="no hashtags in latest snapshot",
                     fix_hint="Either evergreen list is empty or snapshot file is stale — rerun a cycle")
    live = sum(1 for t in tt if t.get("source") == "tiktok_creative_center")
    evergreen = sum(1 for t in tt if t.get("source") == "evergreen_curated")
    if live == 0 and evergreen > 0:
        return Check(
            name="TikTok mode",
            severity="P1", status="warn",
            detail=f"live=0  evergreen_fallback={evergreen}",
            fix_hint=("Public page loses 'live snapshot' credibility when only fallback shows. "
                      "Patch scrape_tiktok_trends() regex/selectors against the current "
                      "Creative Center HTML."),
        )
    return Check(name="TikTok mode", severity="info", status="info",
                 detail=f"live={live}  evergreen_fallback={evergreen}")


# ─────────────────────────── Public page freshness ───────────────────────────

def check_public_page() -> Check:
    if not WEBSITE_OUT.exists():
        return Check(
            name="Public page",
            severity="P1", status="warn",
            detail=f"{WEBSITE_OUT.name} does not exist",
            fix_hint="Run a cycle to generate it — the lead-magnet flow depends on this file",
        )
    mtime = datetime.fromtimestamp(WEBSITE_OUT.stat().st_mtime)
    age_h = (datetime.now() - mtime).total_seconds() / 3600
    if age_h > PAGE_STALENESS_HOURS:
        return Check(
            name="Public page",
            severity="P1", status="warn",
            detail=f"{WEBSITE_OUT.name} is {age_h:.1f}h old (budget: {PAGE_STALENESS_HOURS}h)",
            fix_hint=("Hourly cron probably stopped firing. Check run_dropship_scout_cron.sh "
                      "+ system cron, or run a manual cycle."),
        )
    return Check(name="Public page", severity="info", status="info",
                 detail=f"refreshed {age_h:.1f}h ago  ({mtime:%Y-%m-%d %H:%M})")


# ─────────────────────────── Subscribers + MRR ───────────────────────────

def check_subscribers() -> Check:
    subs = _load(SUBS_FILE, [])
    if not isinstance(subs, list):
        return Check(name="Subscribers", severity="P1", status="warn",
                     detail="ds_subscribers.json wrong shape (expected list)")
    if not subs:
        return Check(name="Subscribers", severity="info", status="info",
                     detail="0 — owner-only mode")
    active  = [s for s in subs if s.get("status") == "active"]
    pending = [s for s in subs if s.get("status") == "pending"]
    mrr = len(active) * PLAN_PRICE
    return Check(name="Subscribers", severity="info", status="info",
                 detail=f"total={len(subs)}  active={len(active)}  "
                        f"pending={len(pending)}  MRR=${mrr}/mo")


# ─────────────────────────── Delivery cadence ───────────────────────────

def check_delivery_cadence() -> Check:
    """Mondays only by design. Surface that so the owner isn't confused on Tuesday."""
    today = datetime.now().weekday()
    days = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]
    next_mon = (7 - today) % 7 or 7
    if today == 0:
        return Check(name="Delivery cadence", severity="info", status="info",
                     detail="today is Monday — digests will be delivered this cycle")
    return Check(name="Delivery cadence", severity="info", status="info",
                 detail=f"today={days[today]} — next delivery in {next_mon}d (Monday)  "
                        f"·  override with --force-deliver")


# ─────────────────────────── Aggregate ───────────────────────────

def run_diagnostics() -> dict:
    checks = [
        check_smtp(),
        check_source_health(),
        check_tiktok_mode(),
        check_public_page(),
        check_subscribers(),
        check_delivery_cadence(),
    ]
    summary = {
        "P0_fail": sum(1 for c in checks if c.severity == "P0" and c.status == "fail"),
        "P1_warn": sum(1 for c in checks if c.severity == "P1" and c.status == "warn"),
        "passed":  sum(1 for c in checks if c.status == "pass"),
        "total":   len(checks),
    }
    summary["ready_to_run"] = summary["P0_fail"] == 0
    return {"checks": [c.__dict__ for c in checks], "summary": summary}


def print_report(report: dict) -> None:
    icon = {"pass": "✓", "fail": "✗", "warn": "!", "info": "·"}
    for c in report["checks"]:
        print(f"  [{icon[c['status']]}] [{c['severity']:>4s}] {c['name']:24s} {c['detail']}")
        if c["fix_hint"] and c["status"] in ("fail", "warn"):
            print(f"        ↳ {c['fix_hint']}")
    s = report["summary"]
    print()
    print(f"  Result: {s['passed']}/{s['total']} passed · P0 fails={s['P0_fail']} · "
          f"P1 warns={s['P1_warn']}")
    if s["ready_to_run"]:
        print("  ✓ Ready to run cycles. See `--health-report` for per-source detail.")
    else:
        print("  ✗ Fix P0 items above first.")


def main() -> int:
    print("DropshipScout preflight\n")
    report = run_diagnostics()
    print_report(report)
    return 0 if report["summary"]["ready_to_run"] else 1


if __name__ == "__main__":
    sys.exit(main())
