"""StoryForge preflight."""
from __future__ import annotations
import json, os, smtplib, sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from storyforge.health import (probe_inputs, prompt_outcome_summary, stuck_mail_failed)
from storyforge.subscribers import listing as sub_listing

DATA_DIR = Path(__file__).parent.parent / "data"
LEADS = DATA_DIR / "sf_leads.json"


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
        return Check("SMTP creds", "P0", "fail", "SMTP_USER/SMTP_PASS not set", "Required for delivery")
    try:
        with smtplib.SMTP(os.environ.get("SMTP_HOST", "smtp.gmail.com"),
                          int(os.environ.get("SMTP_PORT", "587")), timeout=10) as s:
            s.starttls(); s.login(user, pwd)
        return Check("SMTP auth", "P0", "pass", f"smtp.gmail.com as {user}")
    except Exception as e:
        return Check("SMTP auth", "P0", "fail", f"{type(e).__name__}: {str(e)[:80]}")


def check_inputs():
    p = probe_inputs()
    if not p.get("ok"):
        return Check("Inputs", "P0", "fail", "sf_inputs/ empty",
                     "Drop manifest at data/sf_inputs/<slug>.json")
    age = p.get("newest_age_days")
    age_s = f" newest {age}d old" if age is not None else ""
    detail = f"sf_inputs={p['sf_inputs']}  sf_outputs={p['sf_outputs']}{age_s}"
    if age is not None and age > 30:
        return Check("Inputs", "P1", "warn", detail, "Newest input >30d old — owner stopped feeding")
    return Check("Inputs", "info", "info", detail)


def check_outcomes():
    s = prompt_outcome_summary()
    if s["total"] == 0: return Check("Outcomes", "info", "info", "(no prompts logged)")
    detail = f"log={s['total']}  " + "  ".join(f"{k}={v}" for k, v in s.items() if k not in ("total",))
    fail = sum(v for k, v in s.items() if k not in ("total", "success"))
    if fail > s.get("success", 0) and s["total"] >= 5:
        return Check("Outcomes", "P1", "warn", detail, "Skips outnumber successes")
    return Check("Outcomes", "info", "info", detail)


def check_stuck_mail():
    stuck = stuck_mail_failed(min_attempts=3)
    if not stuck: return Check("Stuck mail_failed", "info", "info", "(none)")
    sample = ", ".join(f"{s['email']}({s['attempts']}×)" for s in stuck[:4])
    return Check("Stuck mail_failed", "P1", "warn", f"{len(stuck)} stuck: {sample}",
                 "Builds succeeded but mailer keeps rejecting")


def check_subscribers():
    out = sub_listing()
    if out["total"] == 0: return Check("Subscribers", "info", "info", "0 — owner-only mode")
    by_plan = " ".join(f"{p}={n}" for p, n in out["by_plan"].items())
    return Check("Subscribers", "info", "info",
                 f"active={out['active']}  MRR≈${out['mrr']:.0f}/mo  "
                 f"one-time=${out['one_time_collected']}  · {by_plan}")


def check_leads():
    leads = _load(LEADS, [])
    if not isinstance(leads, list) or not leads:
        return Check("Lead pipeline", "info", "info", "0 leads — populate sf_leads.json")
    teased = sum(1 for l in leads if l.get("trial_sent"))
    return Check("Lead pipeline", "info", "info",
                 f"{len(leads)} lead(s) · trial_sent={teased}")


def run_diagnostics():
    checks = [check_smtp(), check_inputs(), check_outcomes(),
              check_stuck_mail(), check_subscribers(), check_leads()]
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
    print("StoryForge preflight\n")
    r = run_diagnostics(); print_report(r)
    return 0 if r["summary"]["ready_to_run"] else 1


if __name__ == "__main__": sys.exit(main())
