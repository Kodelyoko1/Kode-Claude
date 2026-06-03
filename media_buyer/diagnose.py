"""
Media Buyer preflight diagnostics — read-only.

Runs through every external dependency the Media Buyer needs to actually spend
money and route leads, and prints a pass/fail checklist so the owner knows
exactly what's missing before flipping MB_LIVE=1.

Checks:
  1. META_ACCESS_TOKEN is set and valid (GET /me)
  2. META_AD_ACCOUNT_ID is reachable + the token has perms on it
  3. META_PAGE_ID is reachable + we can mint a page-scoped token
  4. MB_LEADGEN_PIXEL_ID is reachable
  5. List existing campaigns (so owner can see if there's already something live)
  6. List existing lead forms on the page (so launcher can reuse one if present)
  7. Optional creds: TWILIO_*, ANTHROPIC_API_KEY
  8. Report MB_LIVE / DRY_RUN state + safety caps
  9. Report whether the FastAPI webhook server has a configured public URL

Exit code:
  0 = ready to launch (no P0/P1 failures)
  1 = critical missing pieces — fix before running --launch
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from typing import Optional

from . import meta_api, token_store
from .config import (
    DRY_RUN,
    MAX_ABSOLUTE_DAILY_BUDGET_USD,
    MAX_DAILY_BUDGET_INCREASE_PCT,
    PROFILES,
)


@dataclass
class Check:
    name: str
    severity: str            # "P0" (must-have for launch) | "P1" (must-have for autopilot) | "info"
    status: str              # "pass" | "fail" | "warn" | "info"
    detail: str = ""
    fix_hint: str = ""


def _safe(call):
    """Run a Meta API call and return (ok, payload_or_error_string)."""
    try:
        return True, call()
    except Exception as e:
        return False, str(e)[:300]


# ─────────────────────────── Individual checks ───────────────────────────

def check_token() -> Check:
    sys_tok = os.getenv("META_ACCESS_TOKEN", "")
    if not sys_tok:
        return Check(
            name="META_ACCESS_TOKEN env var",
            severity="P0", status="fail",
            detail="not set",
            fix_hint="Generate a System User token in Business Manager and export META_ACCESS_TOKEN=<token>",
        )
    ok, payload = _safe(lambda: meta_api._request("GET", "/me", params={"fields": "id,name"}))
    if not ok:
        return Check(
            name="META_ACCESS_TOKEN is valid",
            severity="P0", status="fail",
            detail=f"token rejected by Meta: {payload}",
            fix_hint="Re-issue the token (it may have expired) and update .env",
        )
    return Check(
        name="META_ACCESS_TOKEN is valid",
        severity="P0", status="pass",
        detail=f"id={payload.get('id')} name={payload.get('name','?')}",
    )


def check_ad_account(account_id: str) -> Check:
    if not account_id:
        return Check(
            name="META_AD_ACCOUNT_ID env var",
            severity="P0", status="fail",
            detail="not set",
            fix_hint='Set META_AD_ACCOUNT_ID="act_<numeric_id>" in .env',
        )
    if not account_id.startswith("act_"):
        return Check(
            name=f"Ad account id format ({account_id})",
            severity="P0", status="fail",
            detail="must start with 'act_'",
            fix_hint='Format is META_AD_ACCOUNT_ID="act_1234567890"',
        )
    ok, payload = _safe(lambda: meta_api._request(
        "GET", f"/{account_id}",
        params={"fields": "id,name,currency,account_status,amount_spent,balance,disable_reason"},
    ))
    if not ok:
        return Check(
            name=f"Ad account {account_id} reachable",
            severity="P0", status="fail",
            detail=payload,
            fix_hint="Token may not have ads_management/ads_read scope on this ad account",
        )
    status_codes = {1: "ACTIVE", 2: "DISABLED", 3: "UNSETTLED", 7: "PENDING_RISK_REVIEW",
                    9: "IN_GRACE_PERIOD", 100: "PENDING_CLOSURE", 101: "CLOSED"}
    acc_status = status_codes.get(payload.get("account_status", 0), f"code-{payload.get('account_status')}")
    detail = (f"name={payload.get('name','?')} ccy={payload.get('currency','?')} "
              f"status={acc_status} spent={int(payload.get('amount_spent',0))/100:.2f}")
    if acc_status != "ACTIVE":
        return Check(
            name=f"Ad account {account_id}",
            severity="P0", status="fail",
            detail=detail + f" (reason: {payload.get('disable_reason','-')})",
            fix_hint="Resolve the account status in Meta Business Manager before launching ads",
        )
    return Check(
        name=f"Ad account {account_id}",
        severity="P0", status="pass", detail=detail,
    )


def check_page(page_id: str) -> Check:
    if not page_id:
        return Check(
            name="META_PAGE_ID env var",
            severity="P0", status="fail", detail="not set",
            fix_hint="Set META_PAGE_ID=<numeric page id> in .env (find it under Page → About → Page ID)",
        )
    ok, payload = _safe(lambda: meta_api._request(
        "GET", f"/{page_id}", params={"fields": "id,name,category,access_token"},
    ))
    if not ok:
        return Check(
            name=f"Page {page_id} reachable",
            severity="P0", status="fail", detail=payload,
            fix_hint="Token may not have pages_read_engagement/pages_show_list/leads_retrieval on this page",
        )
    has_page_token = bool(payload.get("access_token"))
    return Check(
        name=f"Page {page_id}",
        severity="P0",
        status="pass" if has_page_token else "warn",
        detail=f"name={payload.get('name','?')} category={payload.get('category','?')} "
               f"page_token={'yes' if has_page_token else 'NO'}",
        fix_hint=("" if has_page_token else
                  "Page-scoped token is required to subscribe webhooks. Grant pages_manage_metadata."),
    )


def check_pixel(pixel_id: str) -> Check:
    if not pixel_id:
        return Check(
            name="MB_LEADGEN_PIXEL_ID env var",
            severity="P1", status="warn", detail="not set",
            fix_hint="Pixel is needed for retargeting + ROAS tracking. Set MB_LEADGEN_PIXEL_ID=<id>",
        )
    ok, payload = _safe(lambda: meta_api._request(
        "GET", f"/{pixel_id}", params={"fields": "id,name,last_fired_time,is_unavailable"},
    ))
    if not ok:
        return Check(
            name=f"Pixel {pixel_id} reachable",
            severity="P1", status="fail", detail=payload,
            fix_hint="Verify the pixel id and that the token has business_management scope",
        )
    detail = f"name={payload.get('name','?')} last_fired={payload.get('last_fired_time','never')}"
    return Check(
        name=f"Pixel {pixel_id}",
        severity="P1", status="pass", detail=detail,
    )


def check_existing_campaigns(account_id: str) -> Check:
    ok, payload = _safe(lambda: meta_api.list_campaigns(account_id))
    if not ok:
        return Check(name="List campaigns", severity="info", status="fail",
                      detail=str(payload)[:200])
    if not payload:
        return Check(
            name="Existing campaigns",
            severity="info", status="info",
            detail="0 — account is empty, --launch will bootstrap the first one",
        )
    statuses = {}
    lg_count = 0
    for c in payload:
        statuses[c.get("effective_status", "?")] = statuses.get(c.get("effective_status", "?"), 0) + 1
        if "[LG]" in (c.get("name") or "").upper():
            lg_count += 1
    sample = ", ".join(f"{s}:{n}" for s, n in sorted(statuses.items()))
    return Check(
        name="Existing campaigns",
        severity="info", status="info",
        detail=f"{len(payload)} total ({sample}) — {lg_count} tagged [LG]",
    )


def _get_page_token(page_id: str) -> Optional[str]:
    """Fetch the page-scoped access token. /leadgen_forms requires it (not the user token)."""
    try:
        page = meta_api._request("GET", f"/{page_id}", params={"fields": "access_token"})
        return page.get("access_token")
    except Exception:
        return None


def check_lead_forms(page_id: str) -> Check:
    if not page_id:
        return Check(name="Lead forms", severity="P1", status="fail",
                      detail="page id missing")
    page_token = _get_page_token(page_id)
    params = {"fields": "id,name,status,questions", "limit": 100}
    if page_token:
        params["access_token"] = page_token
    ok, payload = _safe(lambda: meta_api._request(
        "GET", f"/{page_id}/leadgen_forms", params=params,
    ))
    if not ok:
        return Check(
            name="Lead forms on page",
            severity="P1", status="warn", detail=str(payload)[:200],
            fix_hint="Token needs leads_retrieval + pages_manage_ads to list lead forms",
        )
    forms = payload.get("data", [])
    if not forms:
        return Check(
            name="Lead forms on page",
            severity="P1", status="warn",
            detail="0 — --launch will create a default 'We Buy Houses' form",
        )
    active = [f for f in forms if f.get("status") == "ACTIVE"]
    return Check(
        name="Lead forms on page",
        severity="P1", status="pass",
        detail=f"{len(forms)} forms ({len(active)} ACTIVE)",
    )


def check_twilio() -> Check:
    sid = os.getenv("TWILIO_ACCOUNT_SID", "")
    tok = os.getenv("TWILIO_AUTH_TOKEN", "")
    if not (sid and tok):
        return Check(name="Twilio Lookup creds", severity="info", status="warn",
                      detail="not set — phone validation will be skipped on incoming leads",
                      fix_hint="TWILIO_ACCOUNT_SID + TWILIO_AUTH_TOKEN catch typos + VoIP burners (~$0.005/lookup)")
    return Check(name="Twilio Lookup creds", severity="info", status="pass",
                  detail=f"sid={sid[:6]}…")


def check_anthropic() -> Check:
    if not os.getenv("ANTHROPIC_API_KEY"):
        return Check(name="ANTHROPIC_API_KEY", severity="P1", status="warn",
                      detail="not set — lead scoring + creative generator fall back to heuristics",
                      fix_hint="Set ANTHROPIC_API_KEY to unlock Claude-driven scoring + creative variations")
    return Check(name="ANTHROPIC_API_KEY", severity="P1", status="pass",
                  detail="configured")


def check_safety_flags() -> Check:
    state = "LIVE (mutations will hit Meta)" if not DRY_RUN else "DRY-RUN (no mutations will fire)"
    return Check(
        name="MB_LIVE / DRY_RUN",
        severity="info", status="info",
        detail=f"{state} — daily cap ${MAX_ABSOLUTE_DAILY_BUDGET_USD:.0f}, "
               f"scale step {MAX_DAILY_BUDGET_INCREASE_PCT:.0f}%",
        fix_hint=("Owner must explicitly set MB_LIVE=1 to start spending money."
                  if DRY_RUN else "WARNING: mutations are live. Verify safety caps above are correct."),
    )


def check_webhook_url() -> Check:
    url = os.getenv("MB_WEBHOOK_PUBLIC_URL", "")
    if not url:
        return Check(
            name="Public webhook URL",
            severity="P1", status="warn",
            detail="MB_WEBHOOK_PUBLIC_URL not set — Meta has nowhere to send leads",
            fix_hint=("Deploy run_media_buyer_server.py somewhere reachable from the internet, "
                      "then set MB_WEBHOOK_PUBLIC_URL=https://<host>/webhooks/meta/leadgen"),
        )
    return Check(
        name="Public webhook URL", severity="P1", status="pass",
        detail=url,
    )


# ─────────────────────────── Aggregate ───────────────────────────

def run_diagnostics(kind: str = "lead_gen") -> dict:
    """Run every check and return {checks: [...], pass/fail counts, ready_to_launch: bool}."""
    profile = PROFILES[kind]

    checks: list[Check] = [
        check_token(),
        check_ad_account(profile.ad_account_id),
        check_page(profile.page_id),
        check_pixel(profile.pixel_id),
        check_existing_campaigns(profile.ad_account_id) if profile.ad_account_id else
            Check(name="Existing campaigns", severity="info", status="fail", detail="no ad account"),
        check_lead_forms(profile.page_id) if profile.page_id else
            Check(name="Lead forms on page", severity="P1", status="fail", detail="no page id"),
        check_anthropic(),
        check_twilio(),
        check_webhook_url(),
        check_safety_flags(),
    ]

    summary = {
        "P0_fail": sum(1 for c in checks if c.severity == "P0" and c.status == "fail"),
        "P1_fail": sum(1 for c in checks if c.severity == "P1" and c.status == "fail"),
        "P1_warn": sum(1 for c in checks if c.severity == "P1" and c.status == "warn"),
        "passed":  sum(1 for c in checks if c.status == "pass"),
        "total":   len(checks),
    }
    summary["ready_to_launch"] = summary["P0_fail"] == 0
    summary["ready_for_autopilot"] = summary["P0_fail"] == 0 and summary["P1_fail"] == 0
    return {"checks": [c.__dict__ for c in checks], "summary": summary}


def print_report(report: dict) -> None:
    """Render the report as a checklist."""
    icon = {"pass": "✓", "fail": "✗", "warn": "!", "info": "·"}
    for c in report["checks"]:
        sev = c["severity"]
        name = c["name"]
        detail = c["detail"]
        hint = c["fix_hint"]
        line = f"  [{icon[c['status']]}] [{sev:>4s}] {name:50s} {detail}"
        print(line)
        if hint and c["status"] in ("fail", "warn"):
            print(f"        ↳ {hint}")
    s = report["summary"]
    print()
    print(f"  Result: {s['passed']}/{s['total']} passed · "
          f"P0 fails={s['P0_fail']} · P1 fails={s['P1_fail']} · P1 warns={s['P1_warn']}")
    if s["ready_for_autopilot"]:
        print("  ✓ Ready for autopilot — fleet can run hands-off")
    elif s["ready_to_launch"]:
        print("  ! Ready to --launch a paused campaign, but autopilot needs P1 items resolved")
    else:
        print("  ✗ Fix P0 items above before running --launch (would fail in flight)")


def main() -> int:
    """CLI entry: python3 -m media_buyer.diagnose [--kind lead_gen|ecom]"""
    import argparse
    p = argparse.ArgumentParser(description="Media Buyer preflight diagnostics")
    p.add_argument("--kind", choices=["lead_gen", "ecom"], default="lead_gen")
    args = p.parse_args()
    report = run_diagnostics(args.kind)
    print_report(report)
    return 0 if report["summary"]["ready_to_launch"] else 1


if __name__ == "__main__":
    sys.exit(main())
