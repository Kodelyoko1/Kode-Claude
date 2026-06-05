"""ColdCaller preflight — daily call queue builder."""
from __future__ import annotations
import json, os, smtplib, sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
LEADS = DATA_DIR / "leads.json"
PS_LEADS = DATA_DIR / "ps_leads.json"
DNC = DATA_DIR / "cd_dnc.json"
CALLS = DATA_DIR / "cd_calls.json"


@dataclass
class Check:
    name: str; severity: str; status: str; detail: str = ""; fix_hint: str = ""


def _load(p, d):
    if not p.exists(): return d
    try: return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError): return d


def check_smtp():
    user = os.environ.get("SMTP_USER", ""); pwd = os.environ.get("SMTP_PASS", "")
    if not (user and pwd):
        return Check("SMTP creds", "P0", "fail", "SMTP_USER/SMTP_PASS not set",
                     "Required to email the daily queue to owner")
    try:
        with smtplib.SMTP(os.environ.get("SMTP_HOST", "smtp.gmail.com"),
                          int(os.environ.get("SMTP_PORT", "587")), timeout=10) as s:
            s.starttls(); s.login(user, pwd)
        return Check("SMTP auth", "P0", "pass", f"as {user}")
    except Exception as e:
        return Check("SMTP auth", "P0", "fail", f"{type(e).__name__}: {str(e)[:80]}")


def check_leads_with_phone():
    total = 0; phoned = 0
    for path in (LEADS, PS_LEADS):
        data = _load(path, {})
        if isinstance(data, dict):
            data = list(data.values())
        if not isinstance(data, list):
            continue
        for lead in data:
            total += 1
            phone = (lead.get("phone") or lead.get("phone_number")
                     or lead.get("seller_phone") or lead.get("owner_phone") or "")
            if phone:
                phoned += 1
    if total == 0:
        return Check("Lead inventory", "P0", "fail", "leads.json + ps_leads.json both empty",
                     "ColdCaller has no prospects to dial — run propscout / followup first")
    pct = (phoned / total * 100) if total else 0
    detail = f"total={total}  with_phone={phoned} ({pct:.0f}%)"
    if phoned == 0:
        return Check("Lead inventory", "P0", "fail", detail,
                     "No leads have a phone number — ColdCaller cannot build a queue")
    if pct < 10:
        return Check("Lead inventory", "P1", "warn", detail,
                     "<10% of leads have phone numbers — queue will stay thin")
    return Check("Lead inventory", "info", "info", detail)


def check_dnc():
    dnc = _load(DNC, [])
    if not isinstance(dnc, list):
        return Check("DNC list", "P1", "warn", "cd_dnc.json wrong shape (expected list)",
                     "Maintain DNC list at data/cd_dnc.json — owner-curated phone numbers to skip")
    return Check("DNC list", "info", "info", f"{len(dnc)} number(s) on DNC list")


def check_call_log():
    log = _load(CALLS, [])
    if not isinstance(log, list) or not log:
        return Check("Call log", "info", "info", "no calls logged yet")
    last = log[-1] if log else {}
    ts = last.get("ts", "")
    age = None
    try:
        age = (datetime.now() - datetime.fromisoformat(ts.split("+")[0])).days
    except (ValueError, AttributeError):
        pass
    detail = f"{len(log)} call(s) logged"
    if age is not None: detail += f" · last call {age}d ago"
    if age is not None and age > 7:
        return Check("Call log", "P1", "warn", detail,
                     "No call in 7+ days — owner isn't working the queue")
    return Check("Call log", "info", "info", detail)


def run_diagnostics():
    checks = [check_smtp(), check_leads_with_phone(), check_dnc(), check_call_log()]
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


def main():
    print("ColdCaller preflight\n")
    r = run_diagnostics(); print_report(r)
    return 0 if r["summary"]["ready_to_run"] else 1


if __name__ == "__main__":
    sys.exit(main())
