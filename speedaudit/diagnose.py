"""
SpeedAudit preflight.

SpeedAudit makes outbound HTTP fetches against arbitrary customer URLs. The
silent failure modes that the existing cycle doesn't surface:

  1. Outbound HTTP egress broken → every audit returns "error" + score n/a.
     A report is still written ("Could not audit"), no obvious owner signal.
  2. sa_inputs/ empty → build_queue produces 0 audits, looks identical to
     "all caught up" in the daily count.
  3. sa_leads.json empty → no preview audits go out; pipeline runs but no
     pitch reaches anyone.
  4. sa_subscribers.json was consume-only — there was no CLI to flip a paying
     prospect to an active subscriber.
  5. Many target sites block our UA — score=error for those without the
     owner noticing the unauditable concentration.

This module surfaces all five plus subscribers + MRR.
"""
from __future__ import annotations

import json
import os
import smtplib
import sys
import urllib.request
import urllib.error
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

DATA_DIR     = Path(__file__).parent.parent / "data"
INPUTS_DIR   = DATA_DIR / "sa_inputs"
OUTPUTS_DIR  = DATA_DIR / "sa_outputs"
LEADS_FILE   = DATA_DIR / "sa_leads.json"
SUBS_FILE    = DATA_DIR / "sa_subscribers.json"
DELIVERY_LOG = DATA_DIR / "sa_delivery_log.json"

PLAN_PRICES_MO = {"audit_77": 0, "monthly_37": 37, "retainer_297": 99}


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
                     fix_hint="Required for lead-preview outreach + monthly subscriber delivery")
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


def check_http_egress() -> Check:
    """Probe outbound HTTPS against a stable, lightweight endpoint."""
    try:
        req = urllib.request.Request(
            "https://httpbin.org/status/200",
            headers={"User-Agent": "SpeedAudit-Diagnose/1.0"},
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            if r.status == 200:
                return Check(name="Outbound HTTP", severity="P0", status="pass",
                             detail="httpbin reached ok")
            return Check(name="Outbound HTTP", severity="P1", status="warn",
                         detail=f"httpbin returned {r.status}")
    except Exception as e:
        return Check(name="Outbound HTTP", severity="P0", status="fail",
                     detail=f"{type(e).__name__}: {str(e)[:120]}",
                     fix_hint="Container has no internet egress — every audit will error out")


# ─────────────────────────── Queues ───────────────────────────

def check_inputs_queue() -> Check:
    if not INPUTS_DIR.exists():
        return Check(name="sa_inputs queue", severity="info", status="info",
                     detail="sa_inputs/ does not exist yet")
    specs = list(INPUTS_DIR.glob("*.json"))
    if not specs:
        return Check(name="sa_inputs queue", severity="info", status="info",
                     detail="empty — drop {slug}.json files here to enqueue ad-hoc audits")
    audited = sum(1 for s in specs if (OUTPUTS_DIR / f"{s.stem}.md").exists())
    return Check(name="sa_inputs queue", severity="info", status="info",
                 detail=f"{len(specs)} spec(s), {audited} already audited, "
                        f"{len(specs) - audited} pending")


def check_leads_queue() -> Check:
    leads = _load(LEADS_FILE, [])
    if not isinstance(leads, list):
        return Check(name="sa_leads.json shape", severity="P1", status="warn",
                     detail=f"expected list, got {type(leads).__name__}")
    if not leads:
        return Check(name="Lead queue", severity="info", status="info",
                     detail="0 — no inbound prospects yet")
    pitched = sum(1 for l in leads if l.get("trial_sent"))
    return Check(name="Lead queue", severity="info", status="info",
                 detail=f"total={len(leads)}  trial_sent={pitched}  un-pitched={len(leads) - pitched}")


# ─────────────────────────── Audit-history quality ───────────────────────────

def check_audit_yield() -> Check:
    """Sample the most recent reports and report the success vs error ratio."""
    if not OUTPUTS_DIR.exists():
        return Check(name="Recent audit yield", severity="info", status="info",
                     detail="sa_outputs/ does not exist yet")
    reports = sorted(OUTPUTS_DIR.glob("*.md"),
                     key=lambda p: p.stat().st_mtime, reverse=True)[:20]
    if not reports:
        return Check(name="Recent audit yield", severity="info", status="info",
                     detail="0 reports generated yet")
    failed = 0
    for r in reports:
        try:
            head = r.read_text(errors="ignore")[:400]
            if "Could not audit" in head:
                failed += 1
        except OSError:
            continue
    total = len(reports)
    fail_pct = failed * 100 // total
    detail = f"last {total} audit(s)  ·  errors={failed}/{total} ({fail_pct}%)"
    if fail_pct >= 50:
        return Check(
            name="Recent audit yield",
            severity="P1", status="warn",
            detail=detail,
            fix_hint=("More than half of recent audits failed. Likely an outbound-HTTP issue, "
                      "or target sites are blocking the User-Agent. Try changing UA in "
                      "speedaudit/tools.py."),
        )
    return Check(name="Recent audit yield", severity="info", status="info", detail=detail)


# ─────────────────────────── Subscribers + delivery cadence ───────────────────────────

def check_subscribers() -> Check:
    subs = _load(SUBS_FILE, [])
    if not isinstance(subs, list):
        return Check(name="Subscribers", severity="P1", status="warn",
                     detail=f"sa_subscribers.json wrong shape: {type(subs).__name__}")
    if not subs:
        return Check(name="Subscribers", severity="info", status="info",
                     detail="0 — owner-only mode")
    active = [s for s in subs if s.get("status") == "active"]
    mrr = sum(PLAN_PRICES_MO.get(s.get("plan", ""), 0) for s in active)
    return Check(name="Subscribers", severity="info", status="info",
                 detail=f"total={len(subs)}  active={len(active)}  MRR=${mrr}/mo")


def check_delivery_cadence() -> Check:
    log = _load(DELIVERY_LOG, {})
    if not isinstance(log, dict) or not log:
        return Check(name="Delivery cadence", severity="info", status="info",
                     detail="(no monthly deliveries yet)")
    now = datetime.now()
    stale = []
    for email, info in log.items():
        last = info.get("last_audit_at", "")
        if not last:
            continue
        try:
            ts = datetime.fromisoformat(last.split("+")[0])
            age_days = (now - ts).days
        except ValueError:
            continue
        # fulfill_cycle re-audits every 28 days; 35d means a cycle was skipped
        if age_days > 35:
            stale.append({"email": email, "days_since": age_days})
    if stale:
        return Check(
            name="Delivery cadence",
            severity="P1", status="warn",
            detail=f"{len(stale)} subscriber(s) overdue (>35d): "
                   + ", ".join(s["email"] for s in stale[:3]),
            fix_hint="Daily cron probably skipped — check cron tail in run_all_autonomous_agents.sh",
        )
    return Check(name="Delivery cadence", severity="info", status="info",
                 detail=f"{len(log)} subscriber(s) tracked, all within the 35d window")


# ─────────────────────────── Aggregate ───────────────────────────

def run_diagnostics() -> dict:
    checks = [
        check_smtp(),
        check_http_egress(),
        check_inputs_queue(),
        check_leads_queue(),
        check_audit_yield(),
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
        print(f"  [{icon[c['status']]}] [{c['severity']:>4s}] {c['name']:30s} {c['detail']}")
        if c["fix_hint"] and c["status"] in ("fail", "warn"):
            print(f"        ↳ {c['fix_hint']}")
    s = report["summary"]
    print()
    print(f"  Result: {s['passed']}/{s['total']} passed · P0 fails={s['P0_fail']} · "
          f"P1 warns={s['P1_warn']}")
    if s["ready_to_run"]:
        print("  ✓ Ready to audit. Use --audit-now URL for one-off, --health-report for aggregates.")
    else:
        print("  ✗ Fix P0 items above first — audits will error.")


def main() -> int:
    print("SpeedAudit preflight\n")
    report = run_diagnostics()
    print_report(report)
    return 0 if report["summary"]["ready_to_run"] else 1


if __name__ == "__main__":
    sys.exit(main())
