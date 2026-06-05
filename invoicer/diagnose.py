"""Invoicer preflight."""
from __future__ import annotations
import os, sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from invoicer.health import (probe_paypal_subscriptions, invoice_outcome_summary,
                             state_summary, stuck_failures)
from invoicer.tools import find_due_invoices, LIVE, MAX_PER_CYCLE
from invoicer.subscriptions_api import catalog_summary


@dataclass
class Check:
    name: str; severity: str; status: str; detail: str = ""; fix_hint: str = ""


def check_paypal():
    r = probe_paypal_subscriptions()
    if r.get("ok"):
        return Check("PayPal Subscriptions", "P0", "pass", r.get("detail", "ok"))
    stage = r.get("stage", "?")
    if stage == "oauth":
        return Check("PayPal OAuth", "P0", "fail",
                     r.get("error", "?"),
                     "Run ./verify_paypal.sh to triage credentials")
    code = r.get("status_code", "?")
    err = r.get("error", "?")
    msg = r.get("message", "")
    return Check("PayPal Subscriptions", "P0", "fail",
                 f"HTTP {code} {err}: {msg[:80]}",
                 "Subscriptions scope may not be enabled on the live app")


def check_catalog():
    c = catalog_summary()
    if c["products_cached"] == 0 and c["plans_cached"] == 0:
        return Check("PayPal catalog", "info", "info",
                     "(empty — first live cycle will create products + plans)")
    return Check("PayPal catalog", "info", "info",
                 f"cached: {c['products_cached']} products, {c['plans_cached']} plans")


def check_live_mode():
    if LIVE:
        return Check("Mode", "info", "info", "INVOICER_LIVE=1 — REAL invoices will be sent")
    return Check("Mode", "info", "info",
                 "INVOICER_LIVE=0 (dry-run) — set INVOICER_LIVE=1 to actually send")


def check_due():
    due = find_due_invoices()
    if not due:
        return Check("Due queue", "info", "info",
                     "0 invoices due — either no active subscribers or all already billed this cycle")
    capped = min(len(due), MAX_PER_CYCLE)
    by_agent = {}
    total_amount = 0.0
    for d in due:
        by_agent[d["agent"]] = by_agent.get(d["agent"], 0) + 1
        total_amount += d["amount"]
    samples = ", ".join(f"{k}={v}" for k, v in sorted(by_agent.items())[:5])
    return Check("Due queue", "info", "info",
                 f"{len(due)} due (would send {capped} this cycle, cap={MAX_PER_CYCLE}) · "
                 f"${total_amount:.2f} potential · agents: {samples}")


def check_outcomes():
    s = invoice_outcome_summary()
    if s["total"] == 0:
        return Check("Outcomes", "info", "info", "(no invoice attempts logged yet)")
    detail = (f"log={s['total']}  ok={s['ok']}  failed={s['failed']}  "
              f"dry_run={s['dry_run']}  live={s['live']}  "
              f"collected=${s['total_collected']:.2f}")
    if s["failed"] > s["ok"] and s["total"] >= 5:
        return Check("Outcomes", "P1", "warn", detail,
                     "Failures outnumber successes — check Stuck failures")
    return Check("Outcomes", "info", "info", detail)


def check_stuck():
    stuck = stuck_failures(min_attempts=3)
    if not stuck:
        return Check("Stuck failures", "info", "info", "(no keys with ≥3 consecutive failures)")
    sample = ", ".join(f"{s['key']}({s['attempts']}×)" for s in stuck[:3])
    return Check("Stuck failures", "P1", "warn",
                 f"{len(stuck)} key(s) stuck: {sample}",
                 "Per-customer error — investigate the email or PayPal account")


def check_state():
    s = state_summary()
    if s["keys"] == 0:
        return Check("State", "info", "info", "(no invoices marked sent yet)")
    by_agent = " ".join(f"{k}={v}" for k, v in sorted(s["agents"].items())[:6])
    return Check("State", "info", "info",
                 f"{s['keys']} (agent,email,plan) entries · {by_agent}")


def run_diagnostics():
    checks = [check_paypal(), check_catalog(), check_live_mode(), check_due(),
              check_outcomes(), check_stuck(), check_state()]
    summary = {"P0_fail": sum(1 for c in checks if c.severity == "P0" and c.status == "fail"),
               "P1_warn": sum(1 for c in checks if c.severity == "P1" and c.status == "warn"),
               "passed":  sum(1 for c in checks if c.status == "pass"),
               "total":   len(checks)}
    summary["ready_to_run"] = summary["P0_fail"] == 0
    return {"checks": [c.__dict__ for c in checks], "summary": summary}


def print_report(r):
    icon = {"pass": "✓", "fail": "✗", "warn": "!", "info": "·"}
    for c in r["checks"]:
        print(f"  [{icon[c['status']]}] [{c['severity']:>4s}] {c['name']:20s} {c['detail']}")
        if c["fix_hint"] and c["status"] in ("fail", "warn"):
            print(f"        ↳ {c['fix_hint']}")
    s = r["summary"]
    print(f"\n  Result: {s['passed']}/{s['total']} passed · P0={s['P0_fail']} · P1={s['P1_warn']}")
    if not s["ready_to_run"]:
        print("  ✗ Fix P0 items first — no invoices would post.")


def main():
    print("Invoicer preflight\n")
    r = run_diagnostics(); print_report(r)
    return 0 if r["summary"]["ready_to_run"] else 1


if __name__ == "__main__":
    sys.exit(main())
