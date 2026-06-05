"""ShortsForge preflight — Shorts content architect."""
from __future__ import annotations
import json, os, smtplib, sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shortsforge.health import probe_inputs, brief_outcome_summary, recent_briefs


@dataclass
class Check:
    name: str; severity: str; status: str; detail: str = ""; fix_hint: str = ""


DATA_DIR = Path(__file__).parent.parent / "data"


def check_smtp():
    user = os.environ.get("SMTP_USER", "")
    pwd  = os.environ.get("SMTP_PASS", "")
    if not (user and pwd):
        return Check("SMTP creds", "P0", "fail", "SMTP_USER/SMTP_PASS not set",
                     "Required for substack-style digest delivery")
    try:
        with smtplib.SMTP(os.environ.get("SMTP_HOST", "smtp.gmail.com"),
                          int(os.environ.get("SMTP_PORT", "587")), timeout=10) as s:
            s.starttls(); s.login(user, pwd)
        return Check("SMTP auth", "P0", "pass", f"as {user}")
    except Exception as e:
        return Check("SMTP auth", "P0", "fail", f"{type(e).__name__}: {str(e)[:80]}")


def check_inputs():
    p = probe_inputs()
    if not p.get("ok"):
        return Check("Inputs", "P0", "fail",
                     "sf_transcripts/ empty — ShortsForge has no source material",
                     "Drop transcripts into data/sf_transcripts/<slug>.txt")
    age = p.get("transcripts_newest_age")
    age_s = f" newest {age}d old" if age is not None else ""
    detail = (f"transcripts={p['transcripts']}  briefs={p['briefs']}  "
              f"newsletters={p['newsletters']}{age_s}")
    if age is not None and age > 14:
        return Check("Inputs", "P1", "warn", detail,
                     "Newest transcript >14d old — content pool aging out")
    return Check("Inputs", "info", "info", detail)


def check_brief_outcomes():
    s = brief_outcome_summary()
    if s["total"] == 0:
        return Check("Brief outcomes", "info", "info", "(no briefs logged yet)")
    by_niche = " ".join(f"{k}={v}" for k, v in s["by_niche"].items())
    detail = (f"log={s['total']}  success={s['success']}  "
              f"too_short={s['too_short']}  no_niche={s['no_niche_detected']}  "
              f"build_failed={s['build_failed']}"
              + (f" · niches: {by_niche}" if by_niche else ""))
    fail = s["too_short"] + s["no_niche_detected"] + s["build_failed"]
    if fail > s["success"] and s["total"] >= 5:
        return Check("Brief outcomes", "P1", "warn", detail,
                     "Skips outnumber successes — check transcript quality + niche heuristics")
    return Check("Brief outcomes", "info", "info", detail)


def check_newsletter_cadence():
    nl_dir = DATA_DIR / "sf_newsletters"
    if not nl_dir.exists():
        return Check("Newsletter cadence", "info", "info",
                     "sf_newsletters/ does not exist (no digests sent yet)")
    files = list(nl_dir.glob("*"))
    if not files:
        return Check("Newsletter cadence", "info", "info", "(empty)")
    from datetime import datetime
    newest = max(f.stat().st_mtime for f in files)
    age = (datetime.now() - datetime.fromtimestamp(newest)).days
    if age > 14:
        return Check("Newsletter cadence", "P1", "warn",
                     f"{len(files)} digest(s), newest {age}d old",
                     "Weekly cadence slipping")
    return Check("Newsletter cadence", "info", "info",
                 f"{len(files)} digest(s), newest {age}d old")


def check_subscribers():
    # ShortsForge shares sf_subscribers.json with StoryForge by design
    subs_path = DATA_DIR / "sf_subscribers.json"
    if not subs_path.exists():
        return Check("Subscribers", "info", "info",
                     "sf_subscribers.json not present — see storyforge/subscribers.py for lifecycle CLI")
    try:
        subs = json.loads(subs_path.read_text())
    except (OSError, json.JSONDecodeError):
        return Check("Subscribers", "P1", "warn", "sf_subscribers.json malformed")
    if not isinstance(subs, list) or not subs:
        return Check("Subscribers", "info", "info", "0 subscribers")
    active = sum(1 for s in subs if s.get("status") == "active")
    return Check("Subscribers", "info", "info",
                 f"total={len(subs)}  active={active}  "
                 f"(shared with storyforge — use storyforge/subscribers.py CLI)")


def run_diagnostics():
    checks = [check_smtp(), check_inputs(), check_brief_outcomes(),
              check_newsletter_cadence(), check_subscribers()]
    summary = {
        "P0_fail": sum(1 for c in checks if c.severity == "P0" and c.status == "fail"),
        "P1_warn": sum(1 for c in checks if c.severity == "P1" and c.status == "warn"),
        "passed":  sum(1 for c in checks if c.status == "pass"),
        "total":   len(checks),
    }
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
    print("ShortsForge preflight\n")
    r = run_diagnostics(); print_report(r)
    return 0 if r["summary"]["ready_to_run"] else 1


if __name__ == "__main__":
    sys.exit(main())
