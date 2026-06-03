"""
PropScout preflight + pipeline audit.

The product: free PropStream-style motivated-seller prospect engine. Owner
relies on it to feed leads.json daily. Silent degradation modes:
  · One Socrata cell breaks → consecutive zeros for that (city, record_type)
  · SMTP misconfigured → owner digest never lands, owner doesn't notice the
    cell failures because they were on the digest itself
  · Drafts pile up in data/ps_drafts/ unsent — owner can't tell because the
    "drafts written" counter just goes up daily

This module surfaces, in one read-only command:
  1. Channels: SMTP + owner-digest target
  2. Grid health: per-cell consecutive-zero count
  3. Inventory: ps_leads snapshot size, draft count + oldest unmailed
  4. Pipeline attribution: how many leads in shared leads.json are tagged
     lead_source=PropScout, and how many are propscout-attributable by
     motivation (the gap = un-tagged backfill candidates)
  5. Subscribers + MRR (placeholder until the subscription module exists)
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
from propscout.health import summary as health_summary, unhealthy_cells, ALERT_AFTER_ZEROS

DATA_DIR     = Path(__file__).parent.parent / "data"
PS_LEADS     = DATA_DIR / "ps_leads.json"
PS_DRAFTS    = DATA_DIR / "ps_drafts"
LEADS_FILE   = DATA_DIR / "leads.json"
PS_SUBS      = DATA_DIR / "ps_subscribers.json"

# Motivation tags that PropScout produces — used to size the attribution gap
PS_MOTIVATIONS = {"tax_delinquent", "code_violations", "vacant", "foreclosure", "probate"}


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


# ─────────────────────────── Channel probes ───────────────────────────

def check_smtp() -> Check:
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER", "")
    pwd  = os.environ.get("SMTP_PASS", "")
    if not (user and pwd):
        return Check(name="SMTP creds", severity="P0", status="fail",
                     detail="SMTP_USER / SMTP_PASS not set",
                     fix_hint="Required to mail owner digest + auto-outreach to sellers")
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


def check_owner_target() -> Check:
    target = os.environ.get("PS_OWNER_EMAIL") or os.environ.get("SMTP_USER", "")
    if not target:
        return Check(name="Owner digest target", severity="P1", status="warn",
                     detail="neither PS_OWNER_EMAIL nor SMTP_USER set")
    return Check(name="Owner digest target", severity="info", status="info",
                 detail=target)


# ─────────────────────────── Grid health ───────────────────────────

def check_grid_health() -> Check:
    s = health_summary()
    if s["cells"] == 0:
        return Check(name="Grid health", severity="P1", status="warn",
                     detail="no cells tracked yet — run a cycle first",
                     fix_hint="Run `python3 run_propscout_auto.py` once to populate ps_cell_health.json")
    unhealthy = unhealthy_cells()
    if unhealthy:
        names = ", ".join(f"{c['city']}/{c['record_type']}(-{c['consecutive_zeros']})"
                          for c in unhealthy[:3])
        extra = f" +{len(unhealthy) - 3}" if len(unhealthy) > 3 else ""
        return Check(
            name="Grid health",
            severity="P1", status="warn",
            detail=(f"{s['healthy']}/{s['cells']} healthy  ·  "
                    f"{s['warning']} cell(s) with ≥{ALERT_AFTER_ZEROS} zeros: {names}{extra}"),
            fix_hint=("Open-data contract likely changed for these cells. "
                      "Patch tools.SOCRATA_DATASETS / CARTO_DATASETS or remove the cell from PROSPECT_GRID."),
        )
    return Check(name="Grid health", severity="info", status="info",
                 detail=f"{s['healthy']}/{s['cells']} healthy  ·  "
                        f"total prospects all-time: {s['total_found_all_time']}")


# ─────────────────────────── Inventory ───────────────────────────

def check_inventory() -> Check:
    prospects = _load(PS_LEADS, [])
    if not isinstance(prospects, list):
        return Check(name="ps_leads.json shape", severity="P0", status="fail",
                     detail=f"expected list, got {type(prospects).__name__}")
    n = len(prospects)
    with_email = sum(1 for p in prospects if p.get("email"))
    with_phone = sum(1 for p in prospects if p.get("phone"))
    return Check(name="Prospect snapshot (ps_leads.json)", severity="info", status="info",
                 detail=f"total={n}  with_email={with_email}  with_phone={with_phone}")


def check_drafts() -> Check:
    if not PS_DRAFTS.exists():
        return Check(name="Cold-email drafts", severity="info", status="info",
                     detail="ps_drafts/ does not exist (no cycles run yet)")
    drafts = list(PS_DRAFTS.glob("*.txt"))
    n = len(drafts)
    if n == 0:
        return Check(name="Cold-email drafts", severity="info", status="info",
                     detail="(empty)")
    drafts.sort(key=lambda p: p.stat().st_mtime)
    oldest_age_days = int((datetime.now() - datetime.fromtimestamp(drafts[0].stat().st_mtime)).days)
    if n > 50 and oldest_age_days > 14:
        return Check(
            name="Cold-email drafts",
            severity="P1", status="warn",
            detail=f"{n} drafts queued, oldest {oldest_age_days}d — backlog growing",
            fix_hint="Enable PS_AUTO_EMAIL=1 or batch-send manually; drafts older than 30d are stale",
        )
    return Check(name="Cold-email drafts", severity="info", status="info",
                 detail=f"{n} drafts queued, oldest {oldest_age_days}d")


# ─────────────────────────── Pipeline attribution ───────────────────────────

def check_pipeline_attribution() -> Check:
    """How many leads in the shared pool are tagged lead_source=PropScout vs.
    motivation-eligible-but-untagged?"""
    leads = _load(LEADS_FILE, {})
    if not isinstance(leads, dict):
        return Check(name="Pipeline attribution", severity="P1", status="warn",
                     detail="leads.json shape")
    if not leads:
        return Check(name="Pipeline attribution", severity="info", status="info",
                     detail="(no leads in shared pool yet)")
    tagged    = sum(1 for l in leads.values() if l.get("lead_source") == "PropScout")
    eligible  = sum(1 for l in leads.values() if l.get("motivation") in PS_MOTIVATIONS)
    untagged  = eligible - tagged
    if untagged > 100 and tagged == 0:
        return Check(
            name="Pipeline attribution",
            severity="P1", status="warn",
            detail=f"{tagged} tagged · {eligible} eligible by motivation · {untagged} untagged",
            fix_hint=("Existing PropScout-motivation leads aren't tagged lead_source=PropScout, so "
                      "deal_analyzer can't credit them. Wave-2 hook in acquire_cycle will start "
                      "tagging new leads. Backfill: run `python3 -m propscout.attribution backfill`."),
        )
    return Check(name="Pipeline attribution", severity="info", status="info",
                 detail=f"{tagged} tagged · {eligible} eligible by motivation · {untagged} untagged")


def check_subscribers() -> Check:
    subs = _load(PS_SUBS, [])
    if not isinstance(subs, list):
        return Check(name="Subscribers", severity="info", status="info",
                     detail="ps_subscribers.json shape")
    n = len(subs)
    active = sum(1 for s in subs if s.get("status") == "active")
    if n == 0:
        return Check(name="Subscribers", severity="info", status="info",
                     detail="0 — owner-only mode")
    return Check(name="Subscribers", severity="info", status="info",
                 detail=f"total={n}  active={active}")


# ─────────────────────────── Aggregate ───────────────────────────

def run_diagnostics() -> dict:
    checks = [
        check_smtp(),
        check_owner_target(),
        check_grid_health(),
        check_inventory(),
        check_drafts(),
        check_pipeline_attribution(),
        check_subscribers(),
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
        print(f"  [{icon[c['status']]}] [{c['severity']:>4s}] {c['name']:42s} {c['detail']}")
        if c["fix_hint"] and c["status"] in ("fail", "warn"):
            print(f"        ↳ {c['fix_hint']}")
    s = report["summary"]
    print()
    print(f"  Result: {s['passed']}/{s['total']} passed · P0 fails={s['P0_fail']} · "
          f"P1 warns={s['P1_warn']}")
    if s["ready_to_run"]:
        print("  ✓ Ready to run cycles. See `--health-report` for per-cell detail.")
    else:
        print("  ✗ Fix P0 items above first.")


def main() -> int:
    print("PropScout preflight\n")
    report = run_diagnostics()
    print_report(report)
    return 0 if report["summary"]["ready_to_run"] else 1


if __name__ == "__main__":
    sys.exit(main())
