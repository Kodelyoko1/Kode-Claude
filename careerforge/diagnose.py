"""
CareerForge preflight + revenue-pipeline audit.

The product: $29/tailoring, $49/mo unlimited (~20/mo per CLAUDE.md),
$147 career package. The cycle scores leads from cf_leads.json and
fulfills paid orders from cf_orders.json by reading
cf_profiles/<user_id>.json + the order's jd_text and writing
cf_resumes/<user_id>/<slug>_resume.md + _cover.md + _ats_match.md.

Silent failure modes — none of these alert today:
  · A paid order's profile file is missing → fulfill_orders silently
    `continue`s; customer paid and never gets their resume
  · A paid order has neither jd_text nor jd_file → same outcome
  · The jd_file reference doesn't resolve under cf_jobs/ → caught by
    the self-healing wrapper but re-tried forever on the next cron
  · monthly_49 advertises "unlimited" but the actual cap is ~20/mo per
    CLAUDE.md — fulfill_orders has no enforcement; a single subscriber
    can ship 200 resumes/mo for $49
  · cf_clients.json was consumed but never written

This module answers, in one read-only command:
  1. Channels: SMTP creds + login
  2. Inputs: profiles + queued orders + jobs + leads triangulation
     (P0 if there are paid orders with no matching profile —
     fulfill_orders skips them silently)
  3. Stuck leads: count of cf_leads.json entries with neither
     profile_data nor jd_text (they can never be scored)
  4. Per-order outcome distribution (P1 if no_profile or no_jd
     dominate vs. success)
  5. ATS score distribution from delivered orders (info)
  6. Monthly usage vs. CF_MONTHLY_CAP (P1 if any monthly_49 user is
     ≥ CF_OVER_CAP_WARN)
  7. Clients + MRR + one-time + by-plan
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
from careerforge.health import (
    probe_inputs,
    order_outcome_summary,
    stuck_mail_failed,
    score_summary,
    monthly_usage_per_user,
    users_over_threshold,
    MONTHLY_CAP,
    OVER_CAP_WARN,
)
from careerforge.clients import listing as client_listing

DATA_DIR    = Path(__file__).parent.parent / "data"
LEADS_FILE  = DATA_DIR / "cf_leads.json"


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
                     fix_hint="Required for ATS-score outreach + resume delivery")
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


# ─────────────────────────── Inputs ───────────────────────────

def check_inputs() -> Check:
    p = probe_inputs()
    if not p.get("ok"):
        return Check(name="Inputs", severity="P0", status="fail",
                     detail="cf_profiles/ empty AND cf_orders.json empty",
                     fix_hint="Add a profile (data/cf_profiles/<user_id>.json) "
                              "and queue an order in data/cf_orders.json.")
    return Check(name="Inputs", severity="info", status="info",
                 detail=(f"profiles={p['profiles']}  jobs={p['jobs_files']}  "
                         f"orders_total={p['orders_total']}  "
                         f"paid_pending={p['orders_paid_pending']}  "
                         f"delivered={p['orders_delivered']}  "
                         f"leads={p['leads_total']} (ready={p['leads_ready']})"))


def check_orders_vs_profiles() -> Check:
    """A paid order with no profile silently never delivers. Surface those."""
    p = probe_inputs()
    missing = p.get("orders_missing_profile", [])
    if not missing:
        return Check(name="Orders vs profiles", severity="info", status="info",
                     detail=("all paid_pending orders have a profile"
                             if p["orders_paid_pending"] > 0
                             else "(no paid_pending orders)"))
    sample = ", ".join(missing[:5])
    extra = f" +{len(missing) - 5}" if len(missing) > 5 else ""
    return Check(name="Orders vs profiles", severity="P0", status="fail",
                 detail=f"{len(missing)} paid order(s) reference a missing profile: {sample}{extra}",
                 fix_hint=("Drop the matching cf_profiles/<user_id>.json or those "
                           "customers paid and will never receive their resume."))


def check_leads_stuck() -> Check:
    leads = _load(LEADS_FILE, [])
    if not isinstance(leads, list) or not leads:
        return Check(name="Lead pipeline", severity="info", status="info",
                     detail="0 leads")
    stuck = sum(1 for l in leads
                if not (l.get("profile_data") and l.get("jd_text")))
    scored = sum(1 for l in leads if l.get("status") == "scored")
    if stuck:
        return Check(name="Lead pipeline", severity="P1", status="warn",
                     detail=(f"total={len(leads)}  scored={scored}  "
                             f"stuck (no profile_data/jd_text)={stuck}"),
                     fix_hint=("Stuck leads can never be scored — they need "
                               "profile_data + jd_text added or they should be cleared."))
    return Check(name="Lead pipeline", severity="info", status="info",
                 detail=f"total={len(leads)}  scored={scored}  all leads have data")


# ─────────────────────────── Per-order outcomes ───────────────────────────

def check_order_outcomes() -> Check:
    s = order_outcome_summary()
    if s["total"] == 0:
        return Check(name="Order outcomes", severity="info", status="info",
                     detail="(no orders logged yet)")
    detail = (f"log={s['total']}  success={s['success']}  "
              f"no_profile={s['no_profile']}  no_jd={s['no_jd']}  "
              f"no_email={s['no_email']}  mail_failed={s['mail_failed']}")
    fail = s["no_profile"] + s["no_jd"] + s["no_email"] + s["mail_failed"]
    if fail > s["success"] and s["total"] >= 5:
        return Check(name="Order outcomes", severity="P1", status="warn",
                     detail=detail,
                     fix_hint=("Skip outcomes outnumber successes — customers are "
                               "paying and not getting their deliverable. Triage the "
                               "skip reasons above."))
    return Check(name="Order outcomes", severity="info", status="info",
                 detail=detail)


def check_stuck_mail() -> Check:
    """Orders that built the files but the mailer keeps rejecting — those
    sit at status=paid forever and re-attempt mail every cycle without
    anyone noticing until this check fires."""
    stuck = stuck_mail_failed(min_attempts=3)
    if not stuck:
        return Check(name="Stuck mail_failed", severity="info", status="info",
                     detail="(no orders with ≥3 mail_failed attempts)")
    sample = ", ".join(f"{s['order_id']}({s['attempts']}× → {s['user_id']})"
                       for s in stuck[:4])
    extra = f" +{len(stuck) - 4}" if len(stuck) > 4 else ""
    return Check(name="Stuck mail_failed", severity="P1", status="warn",
                 detail=f"{len(stuck)} order(s) stuck after ≥3 mail_failed attempts: {sample}{extra}",
                 fix_hint=("Resumes were built but the mailer keeps rejecting. "
                           "Common causes: recipient hit Gmail bounce list, "
                           "example.com sandbox, or the address is malformed. "
                           "Fix the address in cf_orders.json or mark the order failed."))


# ─────────────────────────── ATS score distribution ───────────────────────────

def check_score_distribution() -> Check:
    s = score_summary()
    if s["total"] == 0:
        return Check(name="ATS scores", severity="info", status="info",
                     detail="(no scores logged yet)")
    return Check(name="ATS scores", severity="info", status="info",
                 detail=f"total={s['total']}  avg={s['avg']}  dist " +
                        " ".join(f"{k}={v}" for k, v in s["dist"].items()))


# ─────────────────────────── Subscription usage cap ───────────────────────────

def check_usage_cap() -> Check:
    over = users_over_threshold()
    if not over:
        return Check(name="Monthly usage", severity="info", status="info",
                     detail=f"no user is at ≥{OVER_CAP_WARN}/{MONTHLY_CAP} this month")
    over_cap   = [r for r in over if r["over_cap"]]
    near_cap   = [r for r in over if not r["over_cap"]]
    pieces = []
    if over_cap:
        pieces.append(f"OVER cap (>{MONTHLY_CAP}): "
                      + ", ".join(f"{r['user_id']}({r['count']})" for r in over_cap[:4]))
    if near_cap:
        pieces.append(f"near cap (≥{OVER_CAP_WARN}): "
                      + ", ".join(f"{r['user_id']}({r['count']})" for r in near_cap[:4]))
    return Check(name="Monthly usage", severity="P1", status="warn",
                 detail=" · ".join(pieces),
                 fix_hint=("monthly_49 advertises 'unlimited' but CLAUDE.md cap is "
                           f"~{MONTHLY_CAP}/mo — fulfill_orders has no enforcement. "
                           "Either tighten the docs to 'fair use' or add a cap "
                           "check in fulfill_orders for active monthly_49 clients."))


# ─────────────────────────── Clients + revenue ───────────────────────────

def check_clients() -> Check:
    out = client_listing()
    if out["total"] == 0:
        return Check(name="Clients", severity="info", status="info",
                     detail="0 — owner-only mode")
    by_plan = " ".join(f"{p}={n}" for p, n in out["by_plan"].items())
    return Check(name="Clients", severity="info", status="info",
                 detail=(f"active={out['active']}  pending={out['pending']}  "
                         f"fulfilled={out['fulfilled']}  churned={out['churned']}  "
                         f"MRR≈${out['mrr']:.0f}/mo  "
                         f"one-time=${out['one_time_collected']}  · {by_plan}"))


# ─────────────────────────── Aggregate ───────────────────────────

def run_diagnostics() -> dict:
    checks = [
        check_smtp(),
        check_inputs(),
        check_orders_vs_profiles(),
        check_leads_stuck(),
        check_order_outcomes(),
        check_stuck_mail(),
        check_score_distribution(),
        check_usage_cap(),
        check_clients(),
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
        print("  ✓ Ready to run. See `--orders N` and `--scores`/`--usage` "
              "for outcome detail.")
    else:
        print("  ✗ Fix P0 items above first — paid customers go unfulfilled.")


def main() -> int:
    print("CareerForge preflight\n")
    report = run_diagnostics()
    print_report(report)
    return 0 if report["summary"]["ready_to_run"] else 1


if __name__ == "__main__":
    sys.exit(main())
