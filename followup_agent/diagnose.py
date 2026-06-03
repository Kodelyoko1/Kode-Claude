"""
Followup agent preflight + state audit.

Tells the owner three things they currently can't see from the dashboard:
  1. Are the channels actually configured?  (SMTP, optional Twilio)
  2. Is the lead pool actually reachable?   (phone vs email vs nothing)
  3. Where is the silent waste?            (distress-tag leads stuck at stage 0,
                                              dead emails, idle high-motivation buckets)

Read-only — no sends, no mutations.
"""
from __future__ import annotations

import json
import os
import smtplib
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

DATA_DIR   = Path(__file__).parent.parent / "data"
LEADS_FILE = DATA_DIR / "leads.json"
EMAIL_LOG  = DATA_DIR / "email_log.json"
SMS_LOG    = DATA_DIR / "sms_log.json"

# Tags that mean "real motivation — don't drip-email, escalate."
HOT_MOTIVATIONS = {
    "foreclosure", "pre_foreclosure", "pre-foreclosure",
    "tax_delinquent", "tax-delinquent",
    "code_violations", "code-violations",
    "vacant", "vacant_abandoned",
    "probate", "inherited", "estate",
    "divorce", "bankruptcy",
}


@dataclass
class Check:
    name: str
    severity: str   # "P0" | "P1" | "info"
    status: str     # "pass" | "fail" | "warn" | "info"
    detail: str = ""
    fix_hint: str = ""


# ─────────────────────────── SMTP probe ───────────────────────────

def check_smtp() -> Check:
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER", "")
    pwd  = os.environ.get("SMTP_PASS", "")

    if not user:
        return Check(name="SMTP_USER", severity="P0", status="fail",
                      detail="not set",
                      fix_hint="Set SMTP_USER=<gmail address> in .env")
    if not pwd:
        return Check(name="SMTP_PASS", severity="P0", status="fail",
                      detail="not set",
                      fix_hint="Generate a Gmail app password at myaccount.google.com/apppasswords")

    try:
        with smtplib.SMTP(host, port, timeout=10) as srv:
            srv.starttls()
            srv.login(user, pwd)
        return Check(name="SMTP auth", severity="P0", status="pass",
                      detail=f"{host}:{port} as {user}")
    except smtplib.SMTPAuthenticationError as e:
        return Check(name="SMTP auth", severity="P0", status="fail",
                      detail=f"Gmail rejected: {str(e)[:120]}",
                      fix_hint="App password is wrong or revoked — re-generate one")
    except Exception as e:
        return Check(name="SMTP connection", severity="P0", status="fail",
                      detail=f"{type(e).__name__}: {str(e)[:120]}",
                      fix_hint=f"Verify SMTP_HOST={host} SMTP_PORT={port}")


# ─────────────────────────── Twilio (optional but unlocks SMS) ───────────────────────────

def check_twilio() -> Check:
    sid = os.environ.get("TWILIO_ACCOUNT_SID", "")
    tok = os.environ.get("TWILIO_AUTH_TOKEN", "")
    sms_from = os.environ.get("TWILIO_SMS_FROM", "")
    if not (sid and tok):
        return Check(
            name="Twilio creds", severity="P1", status="warn",
            detail="TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN not set — SMS channel disabled",
            fix_hint="Set both to enable SMS followup (the 370 phone-only leads are unreachable otherwise)",
        )
    if not sms_from:
        return Check(
            name="Twilio SMS sender", severity="P1", status="warn",
            detail="TWILIO_SMS_FROM not set",
            fix_hint='Set TWILIO_SMS_FROM="+1XXXXXXXXXX" — a Twilio number capable of sending US SMS',
        )
    # Cheap probe — fetch the account to confirm creds work
    try:
        import requests
        r = requests.get(
            f"https://api.twilio.com/2010-04-01/Accounts/{sid}.json",
            auth=(sid, tok), timeout=8,
        )
        if r.status_code == 200:
            return Check(name="Twilio creds", severity="P1", status="pass",
                          detail=f"sid={sid[:6]}… from={sms_from}")
        return Check(name="Twilio creds", severity="P1", status="fail",
                      detail=f"Twilio rejected: HTTP {r.status_code} {r.text[:120]}",
                      fix_hint="Re-verify the SID/token pair in the Twilio console")
    except Exception as e:
        return Check(name="Twilio probe", severity="P1", status="fail",
                      detail=str(e)[:160])


# ─────────────────────────── SMS safety flag ───────────────────────────

def check_sms_live() -> Check:
    live = os.environ.get("FOLLOWUP_SMS_LIVE", "").strip().lower() in ("1", "true", "yes")
    return Check(
        name="FOLLOWUP_SMS_LIVE",
        severity="info",
        status="info",
        detail="ENABLED — SMS will actually send" if live else
               "DISABLED — SMS sequence is dry-run only",
        fix_hint="" if live else
                 "Set FOLLOWUP_SMS_LIVE=1 to start sending real SMS (after Twilio + STOP wiring verified)",
    )


# ─────────────────────────── Lead pool reachability ───────────────────────────

def check_lead_reachability() -> Check:
    try:
        leads = json.loads(LEADS_FILE.read_text())
    except Exception as e:
        return Check(name="leads.json", severity="P0", status="fail",
                      detail=f"unreadable: {str(e)[:120]}",
                      fix_hint="batman --live should have already recovered this; check data/.healing_quarantine/")

    if not isinstance(leads, dict):
        return Check(name="leads.json shape", severity="P0", status="fail",
                      detail=f"expected dict, got {type(leads).__name__}")

    n = len(leads)
    if n == 0:
        return Check(name="leads pool", severity="info", status="info",
                      detail="0 leads — nothing to follow up on yet")

    with_email = sum(1 for l in leads.values() if l.get("seller_email"))
    with_phone = sum(1 for l in leads.values() if l.get("seller_phone"))
    with_both  = sum(1 for l in leads.values() if l.get("seller_email") and l.get("seller_phone"))
    unreachable = sum(1 for l in leads.values() if not l.get("seller_email") and not l.get("seller_phone"))

    detail = (f"total={n}  email={with_email}  phone={with_phone}  "
              f"both={with_both}  unreachable={unreachable} ({unreachable*100//n}%)")

    if unreachable > n * 0.5:
        return Check(
            name="Lead reachability",
            severity="P1", status="warn", detail=detail,
            fix_hint=(f"{unreachable} leads have neither email nor phone. They're dead weight unless "
                      "you run skip-tracing. Consider exporting to a paid skip-trace service."),
        )
    return Check(name="Lead reachability", severity="info", status="info",
                  detail=detail)


# ─────────────────────────── Distress-tag stuck leads ───────────────────────────

def check_distress_escalation() -> Check:
    try:
        leads = json.loads(LEADS_FILE.read_text())
    except Exception:
        return Check(name="Distress-tag escalation", severity="info", status="info",
                      detail="leads.json unreadable")

    hot_stage_zero = []
    hot_seen = Counter()
    for l in leads.values():
        m = (l.get("motivation") or "").strip().lower()
        if any(tag in m for tag in HOT_MOTIVATIONS):
            hot_seen[next((t for t in HOT_MOTIVATIONS if t in m), m)] += 1
            if l.get("followup_stage", 0) == 0 and not l.get("seller_responded"):
                hot_stage_zero.append(l.get("lead_id"))

    total_hot = sum(hot_seen.values())
    if total_hot == 0:
        return Check(name="Distress-tag escalation", severity="info", status="info",
                      detail="0 distress-tagged leads")

    if hot_stage_zero:
        # Top 3 most common distress tags
        sample = ", ".join(f"{t}:{n}" for t, n in hot_seen.most_common(3))
        return Check(
            name="Distress-tag escalation",
            severity="P1", status="warn",
            detail=f"{len(hot_stage_zero)} hot leads stuck at stage 0 (tags: {sample})",
            fix_hint=("These deserve immediate owner attention, not a 6-day email drip. "
                      "--escalate sends an owner digest of distress-tagged leads."),
        )
    return Check(name="Distress-tag escalation", severity="info", status="pass",
                  detail=f"{total_hot} distress-tagged leads, all in motion")


# ─────────────────────────── Aggregate ───────────────────────────

def run_diagnostics() -> dict:
    checks: list[Check] = [
        check_smtp(),
        check_twilio(),
        check_sms_live(),
        check_lead_reachability(),
        check_distress_escalation(),
    ]
    summary = {
        "P0_fail": sum(1 for c in checks if c.severity == "P0" and c.status == "fail"),
        "P1_fail": sum(1 for c in checks if c.severity == "P1" and c.status == "fail"),
        "P1_warn": sum(1 for c in checks if c.severity == "P1" and c.status == "warn"),
        "passed":  sum(1 for c in checks if c.status == "pass"),
        "total":   len(checks),
    }
    summary["ready_to_send"] = summary["P0_fail"] == 0
    return {"checks": [c.__dict__ for c in checks], "summary": summary}


def print_report(report: dict) -> None:
    icon = {"pass": "✓", "fail": "✗", "warn": "!", "info": "·"}
    for c in report["checks"]:
        print(f"  [{icon[c['status']]}] [{c['severity']:>4s}] {c['name']:32s} {c['detail']}")
        if c["fix_hint"] and c["status"] in ("fail", "warn"):
            print(f"        ↳ {c['fix_hint']}")
    s = report["summary"]
    print()
    print(f"  Result: {s['passed']}/{s['total']} passed · P0 fails={s['P0_fail']} · "
          f"P1 warns={s['P1_warn']}")
    if s["ready_to_send"]:
        print("  ✓ Email channel is wired. SMS channel depends on Twilio + FOLLOWUP_SMS_LIVE flags above.")
    else:
        print("  ✗ Fix P0 items above before running the agent (sends will silently fail).")


def main() -> int:
    print("Followup agent preflight\n")
    report = run_diagnostics()
    print_report(report)
    return 0 if report["summary"]["ready_to_send"] else 1


if __name__ == "__main__":
    sys.exit(main())
