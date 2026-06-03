"""
Deal Analyzer preflight + queue audit.

Tells the owner three things in one read-only command:
  1. Is the channel wired?  (ANTHROPIC_API_KEY for AI analysis, SMTP for LOI delivery)
  2. Where in the funnel can revenue actually come from right now?
     · escalated hot leads with deal math (ARV > 0): instant bulk-analyze candidates
     · escalated hot leads missing ARV: need research before bulk-analyze can work
     · leads with replied=True / in_negotiation: contract-ready, owner action queue
  3. What contracts exist already and how many got assigned?
"""
from __future__ import annotations

import json
import os
import smtplib
import sys
from dataclasses import dataclass
from pathlib import Path

DATA_DIR       = Path(__file__).parent.parent / "data"
LEADS_FILE     = DATA_DIR / "leads.json"
CONTRACTS_FILE = DATA_DIR / "contracts.json"
BUYERS_FILE    = DATA_DIR / "cash_buyers.json"

# Reuse Followup's distress tag set so "hot" means the same thing everywhere.
sys.path.insert(0, str(Path(__file__).parent.parent))
try:
    from followup_agent.escalation import ALL_DISTRESS
except Exception:
    ALL_DISTRESS = {
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


def _load(path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return default


def _is_hot(lead: dict) -> bool:
    m = (lead.get("motivation") or "").lower()
    return any(t in m for t in ALL_DISTRESS)


# ─────────────────────────── Channel probes ───────────────────────────

def check_anthropic() -> Check:
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        return Check(name="ANTHROPIC_API_KEY", severity="P1", status="warn",
                      detail="not set",
                      fix_hint=("Bulk-analyze still works (pure math), but the chat agent + "
                                "LOI narrative generation falls back to template-only."))
    return Check(name="ANTHROPIC_API_KEY", severity="P1", status="pass", detail="configured")


def check_smtp() -> Check:
    user = os.environ.get("SMTP_USER", "")
    pwd  = os.environ.get("SMTP_PASS", "")
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", "587"))
    if not (user and pwd):
        return Check(name="SMTP creds", severity="P0", status="fail",
                      detail="SMTP_USER / SMTP_PASS not set",
                      fix_hint="Needed to email LOIs to sellers and digests to owner")
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


# ─────────────────────────── Queue audits ───────────────────────────

def check_analyze_queue() -> Check:
    leads = _load(LEADS_FILE, {})
    if not isinstance(leads, dict):
        return Check(name="leads.json", severity="P0", status="fail",
                      detail="unreadable or wrong shape")
    hot         = [l for l in leads.values() if _is_hot(l)]
    with_arv    = [l for l in hot if (l.get("estimated_arv") or 0) > 0]
    missing_arv = [l for l in hot if not (l.get("estimated_arv") or 0)]
    return Check(
        name="Hot-lead deal math",
        severity="info", status="info",
        detail=(f"hot={len(hot)}  with_arv={len(with_arv)}  missing_arv={len(missing_arv)}"
                "  ← bulk-analyze runs on with_arv"),
    )


def check_owner_queue() -> Check:
    """Leads where the seller responded and need an offer / LOI."""
    leads = _load(LEADS_FILE, {})
    if not isinstance(leads, dict):
        return Check(name="Owner action queue", severity="info", status="info", detail="(no leads)")
    queue = [l for l in leads.values()
             if l.get("seller_responded") or l.get("status") == "negotiating"]
    with_arv = [l for l in queue if (l.get("estimated_arv") or 0) > 0]
    return Check(
        name="Owner action queue (responded/negotiating)",
        severity="P1" if queue and not with_arv else "info",
        status="warn" if queue and not with_arv else "info",
        detail=f"queue={len(queue)}  with_arv={len(with_arv)}  ← LOI candidates",
        fix_hint=("Responded leads without ARV can't have an LOI auto-generated. "
                  "Run --refresh-arv to backfill ARV before --loi.") if queue and not with_arv else "",
    )


def check_contracts() -> Check:
    contracts = _load(CONTRACTS_FILE, {})
    if not isinstance(contracts, (dict, list)):
        return Check(name="contracts.json", severity="P1", status="warn",
                      detail=f"wrong shape: {type(contracts).__name__}")
    items = contracts.values() if isinstance(contracts, dict) else contracts
    items = list(items)
    by_status = {}
    for c in items:
        if not isinstance(c, dict): continue
        by_status[c.get("status", "?")] = by_status.get(c.get("status", "?"), 0) + 1
    if not items:
        return Check(name="Contracts", severity="info", status="info",
                      detail="0 — no deals under contract yet")
    detail = "  ".join(f"{k}={v}" for k, v in by_status.items())
    return Check(name="Contracts", severity="info", status="info",
                  detail=f"total={len(items)}  {detail}")


def check_buyers_for_assignment() -> Check:
    """Without buyers, even great contracts can't be assigned for a fee."""
    buyers = _load(BUYERS_FILE, {})
    if not isinstance(buyers, dict):
        return Check(name="Buyers for assignment", severity="P1", status="warn",
                      detail="cash_buyers.json shape")
    n = len(buyers)
    if n < 20:
        return Check(name="Buyers for assignment", severity="P1", status="warn",
                      detail=f"{n} buyers — below 20, hard to flip contracts quickly",
                      fix_hint="Run buyer_finder --pitch to grow the list")
    return Check(name="Buyers for assignment", severity="info", status="info",
                  detail=f"{n} buyers in list")


# ─────────────────────────── Aggregate ───────────────────────────

def run_diagnostics() -> dict:
    checks = [
        check_smtp(),
        check_anthropic(),
        check_analyze_queue(),
        check_owner_queue(),
        check_contracts(),
        check_buyers_for_assignment(),
    ]
    summary = {
        "P0_fail": sum(1 for c in checks if c.severity == "P0" and c.status == "fail"),
        "P1_fail": sum(1 for c in checks if c.severity == "P1" and c.status == "fail"),
        "P1_warn": sum(1 for c in checks if c.severity == "P1" and c.status == "warn"),
        "passed":  sum(1 for c in checks if c.status == "pass"),
        "total":   len(checks),
    }
    summary["ready_to_analyze"] = summary["P0_fail"] == 0
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
    if s["ready_to_analyze"]:
        print("  ✓ Ready to --bulk-analyze and --loi. Owner-queue items in 'warn' "
              "need manual ARV before LOI works.")
    else:
        print("  ✗ Fix P0 items above first.")


def main() -> int:
    print("Deal Analyzer preflight\n")
    report = run_diagnostics()
    print_report(report)
    return 0 if report["summary"]["ready_to_analyze"] else 1


if __name__ == "__main__":
    sys.exit(main())
