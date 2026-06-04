"""
PantryChef preflight + revenue-pipeline audit.

The product: $14/mo basic, $29/mo full + family, $79 one-time 30-day
deep package. Each active subscriber's pantry profile drives a weekly
meal plan; fulfill_cycle reads pc_subscribers.json, runs build_plan()
per active row, and emails the bundle.

Silent failure modes — none of these alert today:
  · Subscriber's user_id has no profile → build_plan errors
    no_user_profile and fulfill_cycle silently continues; paid
    customer never gets a plan
  · Pantry has < PC_PANTRY_MIN items → pantry_too_small skip
  · Mailer rejects (bounce, malformed) → plan files written but order
    never marked delivered; retries forever with no log
  · Allergies/dislikes filter every recipe → low recipes_count;
    customer gets a near-empty calendar but no alert
  · pc_subscribers.json was consumed but never written

This module answers, in one read-only command:
  1. SMTP creds + login
  2. Inputs triangulation (profiles + subscribers + thin-pantry detection)
  3. Subscribers-vs-profiles cross-check (P0)
  4. Thin pantries — active subs whose profile has < PC_PANTRY_MIN items
  5. Plan outcome distribution (P1 if skips dominate)
  6. Stuck mail_failed (P1 — same fix as careerforge)
  7. Recipe yield: any active user whose recent plans are < PC_MIN_RECIPES
  8. Cadence: newest pc_plans/<user>/*/plan.md mtime per active user
  9. Subscribers + MRR + one-time + by-plan
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
from pantrychef.health import (
    probe_inputs,
    plan_outcome_summary,
    stuck_mail_failed,
    yield_summary,
    users_with_thin_plans,
    MIN_RECIPES,
    PANTRY_MIN,
)
from pantrychef.subscribers import listing as sub_listing

DATA_DIR  = Path(__file__).parent.parent / "data"
USERS_DIR = DATA_DIR / "pc_users"
PLANS_DIR = DATA_DIR / "pc_plans"


@dataclass
class Check:
    name: str
    severity: str
    status: str
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
                     fix_hint="Required for trial outreach + weekly plan delivery")
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
                     detail="pc_users/ empty AND pc_subscribers.json empty",
                     fix_hint="Drop a profile at data/pc_users/<user_id>.json "
                              "and add a subscriber via subscribers.py")
    return Check(name="Inputs", severity="info", status="info",
                 detail=(f"profiles={p['profiles']}  subs_total={p['subscribers_total']}  "
                         f"subs_active={p['subscribers_active']}"))


def check_subs_vs_profiles() -> Check:
    p = probe_inputs()
    missing = p.get("subs_missing_profile", [])
    if not missing:
        return Check(name="Subs vs profiles", severity="info", status="info",
                     detail=("all active subscribers have a profile"
                             if p["subscribers_active"] > 0 else "(no active subscribers)"))
    sample = ", ".join(missing[:5])
    extra = f" +{len(missing) - 5}" if len(missing) > 5 else ""
    return Check(name="Subs vs profiles", severity="P0", status="fail",
                 detail=f"{len(missing)} active subscriber(s) have no profile: {sample}{extra}",
                 fix_hint=("Drop data/pc_users/<user_id>.json for each "
                           "or those subscribers paid and won't receive plans."))


def check_thin_pantries() -> Check:
    p = probe_inputs()
    thin = p.get("thin_pantries", [])
    if not thin:
        return Check(name="Thin pantries", severity="info", status="info",
                     detail=(f"all active profiles have ≥{PANTRY_MIN} items"
                             if p["subscribers_active"] > 0 else "(no active subscribers)"))
    sample = ", ".join(f"{t['user_id']}({t['items']})" for t in thin[:4])
    extra = f" +{len(thin) - 4}" if len(thin) > 4 else ""
    return Check(name="Thin pantries", severity="P1", status="warn",
                 detail=f"{len(thin)} active subscriber(s) have pantries < {PANTRY_MIN}: {sample}{extra}",
                 fix_hint=("build_plan returns pantry_too_small for these — "
                           "no plan delivered. Owner needs to flesh out the "
                           "pantry section in pc_users/<user_id>.json."))


# ─────────────────────────── Plan outcomes ───────────────────────────

def check_plan_outcomes() -> Check:
    s = plan_outcome_summary()
    if s["total"] == 0:
        return Check(name="Plan outcomes", severity="info", status="info",
                     detail="(no plans logged yet)")
    detail = (f"log={s['total']}  success={s['success']}  "
              f"no_user_profile={s['no_user_profile']}  "
              f"pantry_too_small={s['pantry_too_small']}  "
              f"mail_failed={s['mail_failed']}")
    fail = s["no_user_profile"] + s["pantry_too_small"] + s["no_email"] + s["mail_failed"]
    if fail > s["success"] and s["total"] >= 5:
        return Check(name="Plan outcomes", severity="P1", status="warn",
                     detail=detail,
                     fix_hint="Skip outcomes outnumber successes — triage above.")
    return Check(name="Plan outcomes", severity="info", status="info", detail=detail)


def check_stuck_mail() -> Check:
    stuck = stuck_mail_failed(min_attempts=3)
    if not stuck:
        return Check(name="Stuck mail_failed", severity="info", status="info",
                     detail="(no users with ≥3 mail_failed attempts)")
    sample = ", ".join(f"{s['user_id']}({s['attempts']}×)" for s in stuck[:4])
    extra = f" +{len(stuck) - 4}" if len(stuck) > 4 else ""
    return Check(name="Stuck mail_failed", severity="P1", status="warn",
                 detail=f"{len(stuck)} subscriber(s) stuck after ≥3 mail_failed attempts: {sample}{extra}",
                 fix_hint=("Plans were built but mailer keeps rejecting. "
                           "Check the email address in pc_subscribers.json."))


def check_yield() -> Check:
    s = yield_summary()
    if s["total"] == 0:
        return Check(name="Recipe yield", severity="info", status="info",
                     detail="(no yields logged yet)")
    thin_users = users_with_thin_plans()
    if thin_users:
        sample = ", ".join(f"{u['user_id']}({u['thin_in_window']}/{u['window']})"
                           for u in thin_users[:4])
        extra = f" +{len(thin_users) - 4}" if len(thin_users) > 4 else ""
        return Check(name="Recipe yield", severity="P1", status="warn",
                     detail=(f"avg_recipes={s['avg_recipes']}  thin_plans(<{MIN_RECIPES})={s['thin_plans']} · "
                             f"users with thin recent plans: {sample}{extra}"),
                     fix_hint=("Allergies/dislikes are filtering most recipes, or "
                               "pantry is too narrow. Owner should expand pantry or "
                               "loosen prefs in pc_users/<user_id>.json."))
    return Check(name="Recipe yield", severity="info", status="info",
                 detail=(f"plans={s['total']}  avg_recipes={s['avg_recipes']}  "
                         f"avg_shopping={s['avg_shopping']}  thin_plans={s['thin_plans']}"))


# ─────────────────────────── Cadence ───────────────────────────

def check_cadence() -> Check:
    if not PLANS_DIR.exists():
        return Check(name="Plan cadence", severity="info", status="info",
                     detail="pc_plans/ does not exist (no cycles run yet)")
    files = list(PLANS_DIR.rglob("plan.md"))
    if not files:
        return Check(name="Plan cadence", severity="info", status="info", detail="(empty)")
    last = max(files, key=lambda f: f.stat().st_mtime)
    age = (datetime.now() - datetime.fromtimestamp(last.stat().st_mtime)).days
    if age > 14:
        return Check(name="Plan cadence", severity="P1", status="warn",
                     detail=f"{len(files)} plan(s), newest {age}d old ({last.parent.name}/{last.name})",
                     fix_hint="Weekly cadence missed 2+ weeks — check cron + Plan outcomes.")
    if age > 8:
        return Check(name="Plan cadence", severity="P1", status="warn",
                     detail=f"{len(files)} plan(s), newest {age}d old",
                     fix_hint="Weekly cadence slipping.")
    return Check(name="Plan cadence", severity="info", status="info",
                 detail=f"{len(files)} plan(s), newest {age}d old")


# ─────────────────────────── Subscribers + revenue ───────────────────────────

def check_subscribers() -> Check:
    out = sub_listing()
    if out["total"] == 0:
        return Check(name="Subscribers", severity="info", status="info",
                     detail="0 — owner-only mode")
    by_plan = " ".join(f"{p}={n}" for p, n in out["by_plan"].items())
    return Check(name="Subscribers", severity="info", status="info",
                 detail=(f"active={out['active']}  pending={out['pending']}  "
                         f"fulfilled={out['fulfilled']}  churned={out['churned']}  "
                         f"MRR≈${out['mrr']:.0f}/mo  "
                         f"one-time=${out['one_time_collected']}  · {by_plan}"))


# ─────────────────────────── Aggregate ───────────────────────────

def run_diagnostics() -> dict:
    checks = [
        check_smtp(),
        check_inputs(),
        check_subs_vs_profiles(),
        check_thin_pantries(),
        check_plan_outcomes(),
        check_stuck_mail(),
        check_yield(),
        check_cadence(),
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
        print(f"  [{icon[c['status']]}] [{c['severity']:>4s}] {c['name']:24s} {c['detail']}")
        if c["fix_hint"] and c["status"] in ("fail", "warn"):
            print(f"        ↳ {c['fix_hint']}")
    s = report["summary"]
    print()
    print(f"  Result: {s['passed']}/{s['total']} passed · P0 fails={s['P0_fail']} · "
          f"P1 warns={s['P1_warn']}")
    if s["ready_to_run"]:
        print("  ✓ Ready to run. See `--plans N` / `--yield` / `--usage` for detail.")
    else:
        print("  ✗ Fix P0 items above first — paid subscribers go unfulfilled.")


def main() -> int:
    print("PantryChef preflight\n")
    report = run_diagnostics()
    print_report(report)
    return 0 if report["summary"]["ready_to_run"] else 1


if __name__ == "__main__":
    sys.exit(main())
