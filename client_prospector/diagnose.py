"""
Client Prospector preflight + revenue-pipeline audit.

The product the prospector sells:
  · SAAS  — Wholesale Deal Analyzer subscription ($97/$197/$397 a month)
  · OAS   — Outreach-as-a-Service retainer ($300/$500/$800 a month)

This module answers, in one read-only command:
  1. Are the channels wired?  (SMTP — needed to send pitch + follow-up)
  2. Is the prospect pool actually pitchable?  (email coverage)
  3. Where is the funnel leaking?  (new / pitched / followed_up / replied / converted / stale)
  4. What's the realistic MRR ceiling at current conversion?
"""
from __future__ import annotations

import json
import os
import smtplib
import sys
from dataclasses import dataclass
from pathlib import Path

DATA_DIR        = Path(__file__).parent.parent / "data"
PROSPECTS_FILE  = DATA_DIR / "prospects.json"
SAAS_FILE       = DATA_DIR / "saas_clients.json"
OAS_FILE        = DATA_DIR / "oas_clients.json"

# Industry-standard cold-outreach assumptions for the ceiling math.
REPLY_RATE_OPTIMISTIC = 0.05   # 5% of pitched-with-email reply
CONVERT_RATE          = 0.20   # 20% of replies convert to paid
SAAS_PRO_PRICE        = 197    # default plan owner sells most
OAS_STANDARD_PRICE    = 500    # default tier owner sells most


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
                     fix_hint="Gmail app password required to pitch and follow up with prospects")
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


# ─────────────────────────── Prospect pool ───────────────────────────

def _funnel_state(p: dict) -> str:
    if p.get("converted_client_id"):
        return "converted"
    if p.get("replied") or p.get("status") == "replied":
        return "replied"
    if p.get("status") == "stale":
        return "stale"
    if p.get("status") == "followed_up" or p.get("followup_count", 0) > 0:
        return "followed_up"
    if p.get("status") == "pitched":
        return "pitched"
    return "new"


def check_prospect_pool() -> Check:
    prospects = _load(PROSPECTS_FILE, {})
    if not isinstance(prospects, dict):
        return Check(name="prospects.json shape", severity="P0", status="fail",
                     detail=f"expected dict, got {type(prospects).__name__}")
    n = len(prospects)
    if n == 0:
        return Check(name="Prospect pool", severity="P1", status="warn",
                     detail="0 — nothing to pitch yet",
                     fix_hint="Run the prospector with default markets to seed the pool")
    with_email = sum(1 for p in prospects.values() if p.get("email"))
    bounced    = sum(1 for p in prospects.values() if p.get("email_bounced"))
    pitchable  = with_email - bounced
    return Check(name="Prospect pool", severity="info", status="info",
                 detail=(f"total={n}  with_email={with_email}  bounced={bounced}  "
                         f"pitchable={pitchable}  email_coverage={with_email*100//max(n,1)}%"))


def check_funnel() -> Check:
    prospects = _load(PROSPECTS_FILE, {})
    if not isinstance(prospects, dict) or not prospects:
        return Check(name="Funnel", severity="info", status="info", detail="(no prospects)")
    by_state = {}
    by_product = {"saas": 0, "oas": 0}
    for p in prospects.values():
        s = _funnel_state(p)
        by_state[s] = by_state.get(s, 0) + 1
        prod = p.get("product_pitched", "saas")
        if prod in by_product:
            by_product[prod] += 1
    detail = "  ".join(f"{k}={v}" for k, v in by_state.items())
    detail += f"  ·  saas_pitch={by_product['saas']}  oas_pitch={by_product['oas']}"
    return Check(name="Funnel state", severity="info", status="info", detail=detail)


def check_owner_queue() -> Check:
    """Replied prospects without a converted_client_id need owner action."""
    prospects = _load(PROSPECTS_FILE, {})
    if not isinstance(prospects, dict) or not prospects:
        return Check(name="Owner action queue", severity="info", status="info", detail="(no prospects)")
    awaiting = [p for p in prospects.values()
                if p.get("replied") and not p.get("converted_client_id")]
    if not awaiting:
        return Check(name="Owner action queue (replied, awaiting onboarding)",
                     severity="info", status="info", detail="empty — no replies to process")
    ids = ", ".join(p.get("prospect_id", "?") for p in awaiting[:5])
    extra = f" (+{len(awaiting) - 5} more)" if len(awaiting) > 5 else ""
    return Check(
        name="Owner action queue (replied, awaiting onboarding)",
        severity="P1", status="warn",
        detail=f"{len(awaiting)} prospect(s) replied: {ids}{extra}",
        fix_hint="Run `python3 onboard_client.py` for each — converts the reply into a paying client",
    )


def check_followup_queue() -> Check:
    """Prospects pitched but sitting without a 2nd touch."""
    from datetime import datetime, timedelta
    days = int(os.environ.get("PROSPECTOR_FOLLOWUP_DAYS", "5"))
    cutoff = datetime.now() - timedelta(days=days)
    prospects = _load(PROSPECTS_FILE, {})
    if not isinstance(prospects, dict) or not prospects:
        return Check(name="Follow-up queue", severity="info", status="info", detail="(no prospects)")
    queue = []
    for p in prospects.values():
        if p.get("replied") or p.get("converted_client_id"):
            continue
        if p.get("followup_count", 0) > 0:
            continue
        if p.get("status") != "pitched":
            continue
        sent = p.get("pitched_at", "")
        if not sent:
            continue
        try:
            ts = datetime.fromisoformat(sent.replace("Z", "+00:00").split("+")[0])
        except ValueError:
            continue
        if ts < cutoff:
            queue.append(p)
    return Check(
        name=f"Follow-up queue (pitched >{days}d ago, no reply)",
        severity="info", status="info",
        detail=(f"{len(queue)} prospect(s) ready for 2nd touch  "
                f"← run --followup to send"),
    )


def check_revenue() -> Check:
    """Current MRR from real clients + realistic ceiling from the prospect pool."""
    saas    = _load(SAAS_FILE, {})
    oas     = _load(OAS_FILE,  {})
    prospects = _load(PROSPECTS_FILE, {})
    mrr_actual = 0.0
    if isinstance(saas, dict):
        for c in saas.values():
            if c.get("status") == "active" and c.get("payment_verified"):
                mrr_actual += float(c.get("monthly_fee", 0))
    if isinstance(oas, dict):
        for c in oas.values():
            if c.get("status") == "active" and c.get("payment_verified"):
                mrr_actual += float(c.get("monthly_fee", 0))

    pitchable = 0
    saas_pool = 0
    oas_pool  = 0
    if isinstance(prospects, dict):
        for p in prospects.values():
            if not p.get("email") or p.get("email_bounced"):
                continue
            pitchable += 1
            if p.get("product_pitched") == "oas":
                oas_pool += 1
            else:
                saas_pool += 1

    saas_ceiling = int(saas_pool * REPLY_RATE_OPTIMISTIC * CONVERT_RATE * SAAS_PRO_PRICE)
    oas_ceiling  = int(oas_pool  * REPLY_RATE_OPTIMISTIC * CONVERT_RATE * OAS_STANDARD_PRICE)
    return Check(
        name="Revenue (MRR)",
        severity="info", status="info",
        detail=(f"actual=${mrr_actual:.0f}  pitchable={pitchable}  "
                f"ceiling: saas=${saas_ceiling} oas=${oas_ceiling}  "
                f"({int(REPLY_RATE_OPTIMISTIC*100)}% reply × {int(CONVERT_RATE*100)}% convert)"),
    )


# ─────────────────────────── Aggregate ───────────────────────────

def run_diagnostics() -> dict:
    checks = [
        check_smtp(),
        check_prospect_pool(),
        check_funnel(),
        check_owner_queue(),
        check_followup_queue(),
        check_revenue(),
    ]
    summary = {
        "P0_fail": sum(1 for c in checks if c.severity == "P0" and c.status == "fail"),
        "P1_warn": sum(1 for c in checks if c.severity == "P1" and c.status == "warn"),
        "passed":  sum(1 for c in checks if c.status == "pass"),
        "total":   len(checks),
    }
    summary["ready_to_pitch"] = summary["P0_fail"] == 0
    return {"checks": [c.__dict__ for c in checks], "summary": summary}


def print_report(report: dict) -> None:
    icon = {"pass": "✓", "fail": "✗", "warn": "!", "info": "·"}
    for c in report["checks"]:
        print(f"  [{icon[c['status']]}] [{c['severity']:>4s}] {c['name']:46s} {c['detail']}")
        if c["fix_hint"] and c["status"] in ("fail", "warn"):
            print(f"        ↳ {c['fix_hint']}")
    s = report["summary"]
    print()
    print(f"  Result: {s['passed']}/{s['total']} passed · P0 fails={s['P0_fail']} · "
          f"P1 warns={s['P1_warn']}")
    if s["ready_to_pitch"]:
        print("  ✓ Ready to pitch / follow up. Replies in the owner queue need manual "
              "onboarding via onboard_client.py.")
    else:
        print("  ✗ Fix P0 items above first — pitches won't go out.")


def main() -> int:
    print("Client Prospector preflight\n")
    report = run_diagnostics()
    print_report(report)
    return 0 if report["summary"]["ready_to_pitch"] else 1


if __name__ == "__main__":
    sys.exit(main())
