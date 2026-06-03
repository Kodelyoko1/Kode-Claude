"""
Outreach-as-a-Service preflight + retainer-pipeline audit.

The product: monthly retainer ($300/$500/$800) for ongoing motivated-seller
prospecting + outreach in the client's target markets. Owner needs to know
before kicking off a cycle:
  1. Are the channels wired?  (SMTP — needed to email report + chase sellers)
  2. Which active clients are actually serviceable this cycle?
     · payment_verified — paywall lets the campaign run
     · target_markets configured — campaign has somewhere to go
     · monthly cap not exceeded — basic=2, standard=4, premium=8
  3. Lead-source coverage per client market: structured (Socrata/Carto/county
     pages) vs. Bing-fallback. Bing-only markets produce lower-quality leads.
  4. Upcoming renewals in the next 7 days (PayPal subs auto-charge; one-time
     invoices need a manual chase via --renewal-reminders).
  5. Revenue snapshot: actual MRR + ARR.
"""
from __future__ import annotations

import json
import os
import smtplib
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

DATA_DIR    = Path(__file__).parent.parent / "data"
OAS_FILE    = DATA_DIR / "outreach_clients.json"
CAMPS_FILE  = DATA_DIR / "outreach_campaigns.json"

# Tier monthly campaign caps — kept in sync with outreach_service.tools.SERVICE_TIERS
TIER_CAPS = {"basic": 2, "standard": 4, "premium": 8}


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


def _structured_coverage_for(city: str) -> str:
    """Return which structured lead source covers a city, or '' for Bing-only."""
    sys.path.insert(0, str(Path(__file__).parent.parent))
    try:
        import tools as parent_tools
    except Exception:
        return ""
    key = (city or "").lower().strip()
    if key in getattr(parent_tools, "SOCRATA_DATASETS", {}):
        return "socrata"
    if key in getattr(parent_tools, "CARTO_DATASETS", {}):
        return "carto"
    # County pages are keyed (county,state), not (city,state) — can't easily match here.
    return ""


# ─────────────────────────── Channel probes ───────────────────────────

def check_smtp() -> Check:
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER", "")
    pwd  = os.environ.get("SMTP_PASS", "")
    if not (user and pwd):
        return Check(name="SMTP creds", severity="P0", status="fail",
                     detail="SMTP_USER / SMTP_PASS not set",
                     fix_hint="Required to email seller outreach + monthly client reports")
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


# ─────────────────────────── Client roster ───────────────────────────

def check_roster() -> Check:
    clients = _load(OAS_FILE, {})
    if not isinstance(clients, dict):
        return Check(name="outreach_clients.json shape", severity="P0", status="fail",
                     detail=f"expected dict, got {type(clients).__name__}")
    n = len(clients)
    if n == 0:
        return Check(name="Client roster", severity="P1", status="warn",
                     detail="0 — no OAS clients onboarded yet",
                     fix_hint=("Run client_prospector to find OAS leads, then "
                               "onboard_client.py to convert one"))
    by_tier = {"basic": 0, "standard": 0, "premium": 0}
    active = 0
    pending = 0
    for c in clients.values():
        tier = (c.get("tier") or "").lower()
        if tier in by_tier:
            by_tier[tier] += 1
        if c.get("status") == "active":
            active += 1
        elif c.get("status") in ("pending_payment", "pending"):
            pending += 1
    parts = "  ".join(f"{t}={n}" for t, n in by_tier.items())
    return Check(
        name="Client roster",
        severity="info", status="info",
        detail=f"total={n}  active={active}  pending={pending}  ·  {parts}",
    )


def check_serviceable() -> Check:
    """Active clients with payment_verified AND markets configured."""
    clients = _load(OAS_FILE, {})
    if not isinstance(clients, dict) or not clients:
        return Check(name="Serviceable this cycle", severity="info", status="info",
                     detail="(no clients)")
    active = [c for c in clients.values() if c.get("status") == "active"]
    if not active:
        return Check(name="Serviceable this cycle", severity="P1", status="warn",
                     detail="0 active clients — nothing to run",
                     fix_hint="Activate clients via onboard_client.py or paywall.gate.verify_payment()")
    unpaid       = [c for c in active if not c.get("payment_verified")]
    no_markets   = [c for c in active if c.get("payment_verified") and not c.get("target_markets")]
    serviceable  = [c for c in active if c.get("payment_verified") and c.get("target_markets")]
    detail = (f"active={len(active)}  serviceable={len(serviceable)}  "
              f"unpaid={len(unpaid)}  no_markets={len(no_markets)}")
    if unpaid or no_markets:
        return Check(
            name="Serviceable this cycle",
            severity="P1", status="warn",
            detail=detail,
            fix_hint=(
                ("Unpaid: run paywall.gate.verify_payment(<id>) or onboard_client.py --activate. "
                 if unpaid else "")
                + ("Missing markets: update_outreach_client(<id>, markets=[...])."
                   if no_markets else "")
            ),
        )
    return Check(name="Serviceable this cycle", severity="info", status="info", detail=detail)


def check_monthly_cap() -> Check:
    """Show how many active clients have hit their monthly campaign cap."""
    clients = _load(OAS_FILE, {})
    if not isinstance(clients, dict) or not clients:
        return Check(name="Monthly cap usage", severity="info", status="info",
                     detail="(no clients)")
    active = [c for c in clients.values() if c.get("status") == "active"]
    if not active:
        return Check(name="Monthly cap usage", severity="info", status="info", detail="(none active)")
    at_cap = []
    rows = []
    for c in active:
        tier = (c.get("tier") or "").lower()
        cap = TIER_CAPS.get(tier, c.get("campaigns_per_month", 0))
        used = int(c.get("campaigns_run_this_month", 0))
        rows.append((c.get("name", c.get("client_id", "?")), used, cap))
        if cap and used >= cap:
            at_cap.append(c)
    detail = "  ".join(f"{nm}={u}/{cp}" for nm, u, cp in rows[:4])
    if at_cap:
        return Check(
            name="Monthly cap usage",
            severity="info", status="info",
            detail=f"{len(at_cap)}/{len(active)} at cap  ({detail})",
            fix_hint="Clients at cap are skipped until --monthly-reset on the 1st",
        )
    return Check(name="Monthly cap usage", severity="info", status="info",
                 detail=detail or "(no campaigns yet this month)")


def check_lead_sources() -> Check:
    """Per-market structured-source coverage for active clients."""
    clients = _load(OAS_FILE, {})
    if not isinstance(clients, dict) or not clients:
        return Check(name="Lead-source coverage", severity="info", status="info", detail="(no clients)")
    structured = 0
    bing_only  = 0
    bing_cities = []
    for c in clients.values():
        if c.get("status") != "active":
            continue
        for m in c.get("target_markets", []):
            city = m.get("city", "")
            if _structured_coverage_for(city):
                structured += 1
            else:
                bing_only += 1
                bing_cities.append(f"{city}, {m.get('state','')}")
    total = structured + bing_only
    if total == 0:
        return Check(name="Lead-source coverage", severity="info", status="info",
                     detail="(no markets configured)")
    if bing_only and not structured:
        return Check(
            name="Lead-source coverage",
            severity="P1", status="warn",
            detail=f"{bing_only}/{total} markets Bing-only ({', '.join(bing_cities[:3])}...)",
            fix_hint=("All markets fall back to Bing search — lower-quality leads. "
                      "Steer new clients toward Chicago, Kansas City, Norfolk, NYC, SF, "
                      "Buffalo, or Philadelphia for Socrata/Carto coverage."),
        )
    detail = f"structured={structured}  bing_only={bing_only}"
    if bing_only:
        detail += f"  ({', '.join(bing_cities[:3])}{'…' if len(bing_cities) > 3 else ''})"
    return Check(name="Lead-source coverage", severity="info", status="info", detail=detail)


def check_renewals() -> Check:
    clients = _load(OAS_FILE, {})
    if not isinstance(clients, dict) or not clients:
        return Check(name="Upcoming renewals (next 7d)", severity="info", status="info", detail="(none)")
    today = date.today()
    horizon = (today + timedelta(days=7)).isoformat()
    upcoming = []
    overdue  = []
    for c in clients.values():
        if c.get("status") != "active":
            continue
        nb = c.get("next_billing_date", "")
        if not nb:
            continue
        if nb < today.isoformat():
            overdue.append(c)
        elif nb <= horizon:
            upcoming.append(c)
    if overdue:
        ids = ", ".join(c.get("client_id", "?") for c in overdue[:5])
        return Check(
            name="Renewals overdue",
            severity="P1", status="warn",
            detail=f"{len(overdue)} overdue (>0d past billing): {ids}",
            fix_hint="Run --renewal-reminders to chase, or onboard_client.py --activate after PayPal proof",
        )
    if upcoming:
        ids = ", ".join(c.get("client_id", "?") for c in upcoming[:5])
        return Check(
            name="Upcoming renewals (next 7d)",
            severity="info", status="info",
            detail=f"{len(upcoming)} upcoming: {ids}",
        )
    return Check(name="Upcoming renewals (next 7d)", severity="info", status="info",
                 detail="none in window")


def check_revenue() -> Check:
    clients = _load(OAS_FILE, {})
    if not isinstance(clients, dict):
        return Check(name="Revenue (MRR)", severity="info", status="info", detail="(no clients)")
    active = [c for c in clients.values() if c.get("status") == "active"]
    mrr = sum(float(c.get("monthly_fee", 0)) for c in active if c.get("payment_verified"))
    pending = sum(float(c.get("monthly_fee", 0)) for c in active if not c.get("payment_verified"))
    return Check(
        name="Revenue (MRR)",
        severity="info", status="info",
        detail=f"actual=${mrr:.0f}  pending=${pending:.0f}  arr=${mrr*12:.0f}",
    )


# ─────────────────────────── Aggregate ───────────────────────────

def run_diagnostics() -> dict:
    checks = [
        check_smtp(),
        check_roster(),
        check_serviceable(),
        check_monthly_cap(),
        check_lead_sources(),
        check_renewals(),
        check_revenue(),
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
        print(f"  [{icon[c['status']]}] [{c['severity']:>4s}] {c['name']:36s} {c['detail']}")
        if c["fix_hint"] and c["status"] in ("fail", "warn"):
            print(f"        ↳ {c['fix_hint']}")
    s = report["summary"]
    print()
    print(f"  Result: {s['passed']}/{s['total']} passed · P0 fails={s['P0_fail']} · "
          f"P1 warns={s['P1_warn']}")
    if s["ready_to_run"]:
        print("  ✓ Ready to run cycles. P1 warns above don't block the cron — they "
              "just mean specific clients won't be serviced until fixed.")
    else:
        print("  ✗ Fix P0 items above first — campaigns won't deliver.")


def main() -> int:
    print("Outreach-as-a-Service preflight\n")
    report = run_diagnostics()
    print_report(report)
    return 0 if report["summary"]["ready_to_run"] else 1


if __name__ == "__main__":
    sys.exit(main())
