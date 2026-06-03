"""
Buyer Finder preflight + revenue-pipeline audit.

The product: a weekly digest of motivated-seller leads sold to cash buyers
on a $47/mo subscription with a free 1-week sample as the lead-in.

This module answers, in one read-only command:
  1. Are the channels wired? (SMTP, PayPal subscribe link)
  2. Is the buyer pool actually pitchable? (email coverage)
  3. Where is the funnel leaking? (pitched / replied / trial / paid / churned)
  4. What's the MRR ceiling at current conversion?
"""
from __future__ import annotations

import json
import os
import smtplib
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

DATA_DIR    = Path(__file__).parent.parent / "data"
BUYERS_FILE = DATA_DIR / "cash_buyers.json"
LEADS_FILE  = DATA_DIR / "leads.json"

SUBSCRIPTION_PRICE_USD = float(os.environ.get("BF_SUBSCRIPTION_PRICE", "47"))


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
                      fix_hint="Gmail app password required to pitch buyers")
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


def check_subscribe_link() -> Check:
    """The pitch needs a place to send buyers when they say 'I want in.'"""
    explicit = os.environ.get("BF_SUBSCRIBE_URL", "")
    if explicit:
        return Check(name="BF_SUBSCRIBE_URL", severity="P0", status="pass",
                      detail=explicit)
    pp_user = os.environ.get("PAYPAL_ME_USERNAME", "")
    if pp_user:
        return Check(
            name="Subscribe link",
            severity="P1", status="warn",
            detail=f"will fall back to paypal.me/{pp_user}/{int(SUBSCRIPTION_PRICE_USD)} "
                   "(one-time payment URL, not recurring)",
            fix_hint=("For real recurring revenue, set BF_SUBSCRIBE_URL to a PayPal "
                      "Subscriptions plan URL (https://www.paypal.com/webapps/billing/plans/...)."),
        )
    return Check(name="Subscribe link", severity="P0", status="fail",
                  detail="neither BF_SUBSCRIBE_URL nor PAYPAL_ME_USERNAME set",
                  fix_hint="Buyers have nowhere to pay; pitch will lead nowhere")


# ─────────────────────────── Buyer pool audit ───────────────────────────

def check_buyer_pool() -> Check:
    buyers = _load(BUYERS_FILE, {})
    if not isinstance(buyers, dict):
        return Check(name="cash_buyers.json shape", severity="P0", status="fail",
                      detail=f"expected dict, got {type(buyers).__name__}")
    n = len(buyers)
    if n == 0:
        return Check(name="Buyer pool", severity="P1", status="warn",
                      detail="0 — nothing to pitch yet",
                      fix_hint="Run buyer-finder cycles or import a buyer list first")
    with_email = sum(1 for b in buyers.values() if b.get("email"))
    return Check(name="Buyer pool", severity="info", status="info",
                  detail=f"total={n}  with_email={with_email}  email_coverage={with_email*100//max(n,1)}%")


def _funnel_state(b: dict) -> str:
    """Walk a buyer record's history and return their current funnel stage."""
    if b.get("subscription_status") == "active":
        return "active_paid"
    if b.get("subscription_status") == "churned":
        return "churned"
    if b.get("trial_started_at") and not b.get("trial_converted_at"):
        return "trial"
    if b.get("replied"):
        return "replied"
    if b.get("intro_email_sent"):
        return "pitched"
    return "prospect"


def check_funnel() -> Check:
    buyers = _load(BUYERS_FILE, {})
    if not isinstance(buyers, dict) or not buyers:
        return Check(name="Funnel", severity="info", status="info", detail="(no buyers)")
    stages = {}
    for b in buyers.values():
        s = _funnel_state(b)
        stages[s] = stages.get(s, 0) + 1
    detail = "  ".join(f"{k}={v}" for k, v in stages.items())
    return Check(name="Funnel state", severity="info", status="info",
                  detail=detail)


def check_revenue() -> Check:
    buyers = _load(BUYERS_FILE, {})
    if not isinstance(buyers, dict):
        return Check(name="MRR", severity="info", status="info", detail="(no buyers)")
    active = [b for b in buyers.values() if b.get("subscription_status") == "active"]
    mrr_actual = sum(float(b.get("subscription_price_usd", SUBSCRIPTION_PRICE_USD))
                      for b in active)
    n_pitchable = sum(1 for b in buyers.values() if b.get("email"))
    # Realistic conversion ceiling: ~5% of pitched-with-email convert to paid
    realistic_ceiling = int(n_pitchable * 0.05 * SUBSCRIPTION_PRICE_USD)
    return Check(
        name="Revenue (MRR)",
        severity="info", status="info",
        detail=(f"actual=${mrr_actual:.0f}  pitchable_pool={n_pitchable}  "
                f"@5%_conv_ceiling=${realistic_ceiling}/mo"),
    )


def check_lead_supply() -> Check:
    """If we have no leads, we have nothing to deliver in the digest."""
    leads = _load(LEADS_FILE, {})
    if not isinstance(leads, dict):
        return Check(name="Lead supply", severity="P1", status="warn",
                      detail="leads.json unreadable")
    n = len(leads)
    if n == 0:
        return Check(name="Lead supply", severity="P0", status="fail",
                      detail="0 leads — digest would be empty",
                      fix_hint="Run propscout/hudscout/coldcaller first to build the lead pool")
    fresh = sum(1 for l in leads.values()
                if l.get("created_at", "")[:7] >= "2026-05")
    return Check(name="Lead supply", severity="info", status="info",
                  detail=f"total={n}  fresh_last_60d≈{fresh}")


# ─────────────────────────── Aggregate ───────────────────────────

def run_diagnostics() -> dict:
    checks: list[Check] = [
        check_smtp(),
        check_subscribe_link(),
        check_buyer_pool(),
        check_funnel(),
        check_lead_supply(),
        check_revenue(),
    ]
    summary = {
        "P0_fail": sum(1 for c in checks if c.severity == "P0" and c.status == "fail"),
        "P1_warn": sum(1 for c in checks if c.severity == "P1" and c.status == "warn"),
        "passed":  sum(1 for c in checks if c.status == "pass"),
        "total":   len(checks),
    }
    summary["ready_to_pitch"]  = summary["P0_fail"] == 0
    summary["subscription_price_usd"] = SUBSCRIPTION_PRICE_USD
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
          f"P1 warns={s['P1_warn']} · price=${s['subscription_price_usd']:.0f}/mo")
    if s["ready_to_pitch"]:
        print("  ✓ Ready to pitch the existing buyer pool.")
    else:
        print("  ✗ Fix P0 items above before pitching — pitches would lead nowhere.")


def main() -> int:
    print("Buyer Finder preflight\n")
    report = run_diagnostics()
    print_report(report)
    return 0 if report["summary"]["ready_to_pitch"] else 1


if __name__ == "__main__":
    sys.exit(main())
