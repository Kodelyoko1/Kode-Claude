"""
ReputationGuard preflight + revenue-pipeline audit.

The product: $79/mo per location for weekly reply-draft digests,
$497 one-time deep audit. Owner drops Google/Yelp HTML at
data/rg_snapshots/<biz_slug>.html. acquire_cycle scans for businesses
with ≥3 negative reviews (prospecting); fulfill_cycle drafts replies
for active clients and emails the digest.

Silent failure modes — none of these are loud today:
  · Owner stops refreshing a client's snapshot → same reviews recycle
    → same drafts emailed every week → client churns
  · An active client has no snapshot file at all → fulfill_cycle's
    `if not snap.exists(): continue` skips them with zero indication
  · Google / Yelp tweak their HTML → parse_snapshot returns [] for
    every snapshot at once; entire fleet silently dark
  · bs4 missing → same fleet-wide outcome
  · rg_clients.json was consumed but never written — fixed by clients.py
  · Prospects sit in "queued" state forever because owner hasn't
    filled in contact_email (acquire_cycle only sends when it's set)

This module answers, in one read-only command:
  1. Channels: SMTP creds + login
  2. Parser: bs4 importable
  3. Snapshot inventory (P0 if empty; P1 if newest > 14d old)
  4. Audience coverage — active clients vs. snapshot presence
     (P1 if any active client has no snapshot — dark business)
  5. Per-business yield streaks from rg_business_health.json
     (P1 if ≥RG_ALERT_AFTER_SKIPS consecutive skips for any biz)
  6. Replies-folder cadence (P1 if newest > 10d for a weekly cycle)
  7. Clients + MRR + one-time collected
  8. Prospect pipeline — queued / contacted / awaiting_contact_email
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
from reputation_guard.health import (
    summary as health_summary,
    unhealthy_businesses,
    probe_snapshots,
    ALERT_AFTER_SKIPS,
)
from reputation_guard.clients import listing as client_listing

DATA_DIR      = Path(__file__).parent.parent / "data"
SNAPSHOT_DIR  = DATA_DIR / "rg_snapshots"
REPLIES_DIR   = DATA_DIR / "rg_replies"
PROSPECTS     = DATA_DIR / "rg_prospects.json"


@dataclass
class Check:
    name: str
    severity: str
    status: str
    detail: str = ""
    fix_hint: str = ""


def _load(path: Path, default):
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
                     fix_hint="Required for prospect outreach + client fulfillment")
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
    try:
        import bs4
        return Check(name="HTML parser", severity="P0", status="pass",
                     detail=f"beautifulsoup4 {bs4.__version__}")
    except ImportError:
        return Check(name="HTML parser", severity="P0", status="fail",
                     detail="beautifulsoup4 not importable",
                     fix_hint="pip install beautifulsoup4 — without it every snapshot silently empties")


# ─────────────────────────── Inputs ───────────────────────────

def check_snapshot_inventory() -> Check:
    p = probe_snapshots()
    if not p.get("ok"):
        return Check(name="Snapshot inventory", severity="P0", status="fail",
                     detail=p.get("error") or "0 snapshots in rg_snapshots/",
                     fix_hint="Drop Google/Yelp HTML into data/rg_snapshots/<biz-slug>.html")
    age = p.get("newest_age_days")
    age_str = f" · newest {age}d old" if age is not None else ""
    severity, status, fix = "info", "info", ""
    if age is not None and age > 14:
        severity, status = "P1", "warn"
        fix = ("Snapshots are stale across the fleet — same reviews keep "
               "ranking. Refresh rg_snapshots/<biz>.html for active clients.")
    return Check(name="Snapshot inventory", severity=severity, status=status,
                 detail=f"{p['total']} snapshot(s){age_str}",
                 fix_hint=fix)


def check_audience_coverage() -> Check:
    """Each active client needs a snapshot named for their business_slug.
    Without one, fulfill_cycle silently skips them."""
    out = client_listing()
    if out["active"] == 0:
        return Check(name="Audience coverage", severity="info", status="info",
                     detail="(no active clients)")
    snaps = probe_snapshots().get("by_business", {})
    dark = []
    stale = []
    for c in out["clients"]:
        if c.get("status") != "active":
            continue
        slug = c.get("business_slug", "")
        if not slug:
            continue
        if slug not in snaps:
            dark.append(slug)
        else:
            age = snaps[slug].get("age_days", 0)
            if age > 14:
                stale.append(f"{slug}({age}d)")
    notes = []
    if dark:
        notes.append(f"dark (no snapshot): {', '.join(dark[:4])}"
                     + (f" +{len(dark) - 4}" if len(dark) > 4 else ""))
    if stale:
        notes.append(f"stale snapshots: {', '.join(stale[:4])}"
                     + (f" +{len(stale) - 4}" if len(stale) > 4 else ""))
    if notes:
        return Check(name="Audience coverage", severity="P1", status="warn",
                     detail=" · ".join(notes),
                     fix_hint=("Drop fresh snapshots for these business_slugs "
                               "or the client gets duplicate/no drafts."))
    return Check(name="Audience coverage", severity="info", status="info",
                 detail=f"all {out['active']} active client(s) have fresh snapshots")


# ─────────────────────────── Per-business yield ───────────────────────────

def check_business_health() -> Check:
    s = health_summary()
    if s["businesses"] == 0:
        return Check(name="Per-business yield", severity="P1", status="warn",
                     detail="no businesses tracked yet — run a cycle first",
                     fix_hint="Run `python3 run_reputation_guard_auto.py` once to populate rg_business_health.json")
    bad = unhealthy_businesses()
    if bad:
        names = ", ".join(f"{b['business_slug'][:24]}(-{b['consecutive_skips']})"
                          for b in bad[:4])
        extra = f" +{len(bad) - 4}" if len(bad) > 4 else ""
        return Check(name="Per-business yield", severity="P1", status="warn",
                     detail=(f"{s['healthy']}/{s['businesses']} healthy · "
                             f"{s['warning']} biz with ≥{ALERT_AFTER_SKIPS} skips: {names}{extra}"),
                     fix_hint="Snapshots stopped, parser drifted, or no negatives detected.")
    return Check(name="Per-business yield", severity="info", status="info",
                 detail=f"{s['healthy']}/{s['businesses']} healthy · "
                        f"all-time drafts sent: {s['total_drafts_all_time']}")


# ─────────────────────────── Output cadence ───────────────────────────

def check_cadence() -> Check:
    if not REPLIES_DIR.exists():
        return Check(name="Reply cadence", severity="info", status="info",
                     detail="rg_replies/ does not exist (no cycles run yet)")
    files = sorted(REPLIES_DIR.glob("*.txt"))
    if not files:
        return Check(name="Reply cadence", severity="info", status="info", detail="(empty)")
    last = max(files, key=lambda f: f.stat().st_mtime)
    age = (datetime.now() - datetime.fromtimestamp(last.stat().st_mtime)).days
    if age > 21:
        return Check(name="Reply cadence", severity="P1", status="warn",
                     detail=f"{len(files)} file(s), newest {age}d old ({last.name})",
                     fix_hint="No drafts in 3+ weeks — check Audience coverage + Per-business yield.")
    if age > 10:
        return Check(name="Reply cadence", severity="P1", status="warn",
                     detail=f"{len(files)} file(s), newest {age}d old ({last.name})",
                     fix_hint="Weekly cadence slipping.")
    return Check(name="Reply cadence", severity="info", status="info",
                 detail=f"{len(files)} file(s), newest {age}d old")


# ─────────────────────────── Clients + revenue ───────────────────────────

def check_clients() -> Check:
    out = client_listing()
    if out["total"] == 0:
        return Check(name="Clients", severity="info", status="info",
                     detail="0 — owner-only mode")
    return Check(name="Clients", severity="info", status="info",
                 detail=(f"active={out['active']}  pending={out['pending']}  "
                         f"fulfilled={out['fulfilled']}  churned={out['churned']}  "
                         f"MRR≈${out['mrr']:.0f}/mo  "
                         f"one-time-collected=${out['one_time_collected']}"))


# ─────────────────────────── Prospect pipeline ───────────────────────────

def check_prospects() -> Check:
    pros = _load(PROSPECTS, [])
    if not isinstance(pros, list) or not pros:
        return Check(name="Prospect pipeline", severity="info", status="info",
                     detail="0 prospects — scan_prospects() hasn't found any negative-heavy businesses yet")
    queued = sum(1 for p in pros if p.get("status") == "queued")
    contacted = sum(1 for p in pros if p.get("status") == "contacted")
    awaiting_email = sum(1 for p in pros
                         if p.get("status") == "queued" and not p.get("contact_email"))
    return Check(name="Prospect pipeline", severity="info", status="info",
                 detail=(f"total={len(pros)}  queued={queued}  "
                         f"contacted={contacted}  awaiting_contact_email={awaiting_email}"))


# ─────────────────────────── Aggregate ───────────────────────────

def run_diagnostics() -> dict:
    checks = [
        check_smtp(),
        check_parser(),
        check_snapshot_inventory(),
        check_audience_coverage(),
        check_business_health(),
        check_cadence(),
        check_clients(),
        check_prospects(),
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
        print("  ✓ Ready to run. See `--health-report` for per-business detail.")
    else:
        print("  ✗ Fix P0 items above first — cycle would draft nothing.")


def main() -> int:
    print("ReputationGuard preflight\n")
    report = run_diagnostics()
    print_report(report)
    return 0 if report["summary"]["ready_to_run"] else 1


if __name__ == "__main__":
    sys.exit(main())
