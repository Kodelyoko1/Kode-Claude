"""
TownCrier preflight + revenue-pipeline audit.

The product: a free hyper-local weekly newsletter (revenue from sponsors).
The cycle reads HTML snapshots dropped into data/tc_snapshots/, extracts
events with a date+length heuristic, groups by category, and emails a
digest to subscribers in that city. Sponsors get a slot at the top.

Silent failure modes — none of these are loud today:
  · Owner stops adding snapshots for a city → digest skipped, no alert
  · BeautifulSoup not installed → parse_event_snapshot returns []
  · SMTP creds rotated → subscribers + sponsor pitches all silently fail
  · Paid sponsor has 0 sends_remaining → revenue stranded
  · Active subscribers in a city with no snapshots → audience dark

This module answers, in one read-only command:
  1. Channels: SMTP creds + login
  2. Parser: BeautifulSoup importable
  3. Input inventory: snapshots present, per-city count, newest mtime
  4. Per-city extraction health (from towncrier.health)
  5. Digest cadence: newest output age
  6. Subscriber ledger: total + per-city + cities with no snapshots
  7. Sponsor pipeline: committed revenue, slots remaining, pending unpaid
"""
from __future__ import annotations

import json
import os
import smtplib
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from towncrier.health import (
    summary as health_summary,
    unhealthy_cities,
    probe_snapshots,
    ALERT_AFTER_SKIPS,
)
from towncrier.subscribers import listing as sub_listing
from towncrier.sponsors    import listing as spon_listing, PLANS as SPON_PLANS

DATA_DIR     = Path(__file__).parent.parent / "data"
SNAPSHOT_DIR = DATA_DIR / "tc_snapshots"
DIGEST_DIR   = DATA_DIR / "tc_digests"


@dataclass
class Check:
    name: str
    severity: str   # "P0" | "P1" | "info"
    status: str     # "pass" | "fail" | "warn" | "info"
    detail: str = ""
    fix_hint: str = ""


# ─────────────────────────── Channels ───────────────────────────

def check_smtp() -> Check:
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER", "")
    pwd  = os.environ.get("SMTP_PASS", "")
    if not (user and pwd):
        return Check(name="SMTP creds", severity="P0", status="fail",
                     detail="SMTP_USER / SMTP_PASS not set",
                     fix_hint="Required for subscriber digests + sponsor pitches")
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


def check_parser() -> Check:
    """BeautifulSoup is a hard dependency — every snapshot needs it."""
    try:
        import bs4  # noqa: F401
        return Check(name="HTML parser", severity="P0", status="pass",
                     detail=f"beautifulsoup4 {bs4.__version__}")
    except ImportError:
        return Check(name="HTML parser", severity="P0", status="fail",
                     detail="beautifulsoup4 not importable",
                     fix_hint="pip install beautifulsoup4 — every snapshot silently yields 0 events without it")


# ─────────────────────────── Inputs ───────────────────────────

def check_snapshot_inventory() -> Check:
    """tc_snapshots/ is the owner-fed input. Empty → entire cycle no-ops."""
    p = probe_snapshots()
    if not p.get("ok"):
        return Check(name="Snapshot inputs", severity="P0", status="fail",
                     detail=p.get("error") or f"0 snapshots in {SNAPSHOT_DIR}",
                     fix_hint="Drop event-page HTML into data/tc_snapshots/ "
                              "named <city-slug>_<source>.html")
    by_city = p["by_city"]
    cities  = len(by_city)
    top = sorted(by_city.items(), key=lambda kv: -kv[1])[:4]
    top_str = ", ".join(f"{c}={n}" for c, n in top)
    extra = f" +{cities - 4} more" if cities > 4 else ""
    # Newest mtime — surfaces "queue is stale" even when count looks healthy
    files = list(SNAPSHOT_DIR.glob("*.html"))
    newest = max((f.stat().st_mtime for f in files), default=0)
    age = (datetime.now() - datetime.fromtimestamp(newest)).days if newest else None
    age_str = f" · newest {age}d old" if age is not None else ""
    severity, status = ("info", "info")
    fix = ""
    if age is not None and age > 14:
        severity, status = ("P1", "warn")
        fix = ("Inputs are stale — owner hasn't refreshed snapshots in over two "
               "weeks. Newsletter will keep recycling the same events.")
    return Check(name="Snapshot inputs", severity=severity, status=status,
                 detail=f"{p['total']} snapshot(s) across {cities} city(ies) — {top_str}{extra}{age_str}",
                 fix_hint=fix)


# ─────────────────────────── Per-city extraction health ───────────────────────────

def check_city_health() -> Check:
    s = health_summary()
    if s["cities"] == 0:
        return Check(name="Per-city extraction", severity="P1", status="warn",
                     detail="no cities tracked yet — run a cycle first",
                     fix_hint="Run `python3 run_towncrier_auto.py` once to populate tc_city_health.json")
    bad = unhealthy_cities()
    if bad:
        names = ", ".join(f"{c['city']}(-{c['consecutive_skips']})" for c in bad[:5])
        extra = f" +{len(bad) - 5}" if len(bad) > 5 else ""
        return Check(name="Per-city extraction", severity="P1", status="warn",
                     detail=(f"{s['healthy']}/{s['cities']} healthy · "
                             f"{s['warning']} city(ies) with ≥{ALERT_AFTER_SKIPS} skips: {names}{extra}"),
                     fix_hint=("Either snapshots stopped arriving for these cities or the "
                               "page layout drifted. Spot-check parse_event_snapshot() in tools.py."))
    return Check(name="Per-city extraction", severity="info", status="info",
                 detail=f"{s['healthy']}/{s['cities']} healthy · "
                        f"all-time events: {s['total_events_all_time']} · "
                        f"sent: {s['total_sent_all_time']}")


# ─────────────────────────── Output cadence ───────────────────────────

def check_digests() -> Check:
    if not DIGEST_DIR.exists():
        return Check(name="Digest output", severity="info", status="info",
                     detail="tc_digests/ does not exist (no cycles run yet)")
    files = sorted(DIGEST_DIR.glob("*.md"))
    if not files:
        return Check(name="Digest output", severity="info", status="info", detail="(empty)")
    last = files[-1]
    age = (datetime.now() - datetime.fromtimestamp(last.stat().st_mtime)).days
    # Newsletter cadence is weekly. >10d is suspicious; >21d is firmly broken.
    if age > 21:
        return Check(name="Digest output", severity="P1", status="warn",
                     detail=f"{len(files)} digest(s), newest {age}d old ({last.name})",
                     fix_hint="No digest in 3+ weeks — cron broken or all cities skipped. "
                              "See Per-city extraction.")
    if age > 10:
        return Check(name="Digest output", severity="P1", status="warn",
                     detail=f"{len(files)} digest(s), newest {age}d old ({last.name})",
                     fix_hint="Weekly cadence slipping — check cron + Per-city extraction.")
    return Check(name="Digest output", severity="info", status="info",
                 detail=f"{len(files)} digest(s), newest {age}d old")


# ─────────────────────────── Audience ───────────────────────────

def check_subscribers() -> Check:
    out = sub_listing()
    n_active = out["active"]
    if out["total"] == 0:
        return Check(name="Subscribers", severity="info", status="info",
                     detail="0 — owner-only mode")
    # Cross-check audience vs. inventory: any active-subscriber city with 0 snapshots?
    snaps = probe_snapshots().get("by_city", {})
    sub_cities = set(out["by_city"].keys())
    dark = sorted(c for c in sub_cities if c not in snaps)
    by_city_str = ", ".join(f"{c}={n}" for c, n in
                            sorted(out["by_city"].items(), key=lambda kv: -kv[1])[:4])
    extra = f" +{len(out['by_city']) - 4}" if len(out["by_city"]) > 4 else ""
    detail = (f"active={n_active}  total={out['total']}  churned={out['churned']}  "
              f"cities: {by_city_str}{extra}")
    if dark:
        return Check(name="Subscribers", severity="P1", status="warn",
                     detail=detail + f" · dark (no snapshots): {', '.join(dark[:4])}",
                     fix_hint=("Subscribers in these cities will get nothing — either drop "
                               "snapshots for them or cancel those subs."))
    return Check(name="Subscribers", severity="info", status="info", detail=detail)


# ─────────────────────────── Revenue (sponsors) ───────────────────────────

def check_sponsors() -> Check:
    out = spon_listing()
    if out["total"] == 0:
        return Check(name="Sponsor pipeline", severity="info", status="info",
                     detail="0 — no sponsors on file")
    detail = (f"committed=${out['committed_revenue']}  "
              f"slots_remaining={out['slots_remaining']}  "
              f"pending={out['pending']}  paid={out['paid']}  "
              f"cancelled={out['cancelled']}  fulfilled={out['fulfilled']}")
    # Paid sponsor with 0 sends_remaining = revenue collected but obligation done.
    # Pending unpaid for >14d = stale quote.
    stranded = []
    stale_quotes = []
    now = datetime.now()
    for s in out["sponsors"]:
        if s.get("status") == "paid" and s.get("sends_remaining", 0) == 0:
            stranded.append(s.get("name", "?"))
        if s.get("status") == "pending":
            try:
                added = datetime.fromisoformat(s.get("added_at", "").split("+")[0])
                if (now - added).days > 14:
                    stale_quotes.append(s.get("name", "?"))
            except (ValueError, AttributeError):
                pass
    notes = []
    if stranded:
        notes.append(f"fulfilled-but-not-marked: {', '.join(stranded[:3])}")
    if stale_quotes:
        notes.append(f"stale_quotes(>14d): {', '.join(stale_quotes[:3])}")
    if notes:
        return Check(name="Sponsor pipeline", severity="P1", status="warn",
                     detail=detail + " · " + " · ".join(notes),
                     fix_hint=("Mark stranded sponsors fulfilled (manually edit JSON status) "
                               "and follow up on stale quotes."))
    return Check(name="Sponsor pipeline", severity="info", status="info", detail=detail)


# ─────────────────────────── Aggregate ───────────────────────────

def run_diagnostics() -> dict:
    checks = [
        check_smtp(),
        check_parser(),
        check_snapshot_inventory(),
        check_city_health(),
        check_digests(),
        check_subscribers(),
        check_sponsors(),
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
        print(f"  [{icon[c['status']]}] [{c['severity']:>4s}] {c['name']:25s} {c['detail']}")
        if c["fix_hint"] and c["status"] in ("fail", "warn"):
            print(f"        ↳ {c['fix_hint']}")
    s = report["summary"]
    print()
    print(f"  Result: {s['passed']}/{s['total']} passed · P0 fails={s['P0_fail']} · "
          f"P1 warns={s['P1_warn']}")
    if s["ready_to_run"]:
        print("  ✓ Ready to run. See `--health-report` for per-city detail.")
    else:
        print("  ✗ Fix P0 items above first — cycle would produce no digest.")


def main() -> int:
    print("TownCrier preflight\n")
    report = run_diagnostics()
    print_report(report)
    return 0 if report["summary"]["ready_to_run"] else 1


if __name__ == "__main__":
    sys.exit(main())
