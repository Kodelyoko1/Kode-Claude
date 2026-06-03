"""
Batman self-preflight.

Ironic that the fleet-manager that diagnoses everyone else has no diagnose of
its own. When Batman silently breaks — SMTP credentials rotated, runlog/ dir
missing, agent_metrics.json deleted by an over-eager cleanup — the entire
fleet goes invisible: no daily digest, no quarantine, no escalation.

This module answers, in one read-only command:
  1. Channels: SMTP + owner email target
  2. Mode: dry-run vs LIVE — and whether LIVE was set without warning
  3. Directory health: runlog/, bm_reports/, .batman_quarantine/ — all
     writable, present, and (for runlog/) not stale
  4. Inputs: data/agent_metrics.json present (stale-agent check silently
     returns [] when missing — would mask a fully-frozen fleet)
  5. Schema coverage: data/*.json files NOT in DEFAULT_SCHEMAS. These get
     quarantined on corruption but can't be auto-restored — owner must
     hand-restore. This is the biggest hidden risk.
  6. Last report freshness: when did Batman actually emit a report? If it's
     been > BM_REPORT_STALENESS_HOURS, the cron probably stopped firing.
  7. Subscribers + MRR
"""
from __future__ import annotations

import json
import os
import smtplib
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from batman.tools import (
    DATA_DIR, RUNLOG_DIR, QUARANTINE, REPORTS_DIR, DEFAULT_SCHEMAS,
    STALE_THRESHOLD_HOURS, LIVE,
)

REPORT_STALENESS_HOURS = int(os.environ.get("BM_REPORT_STALENESS_HOURS", "36"))


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


def _writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".batman_writable_probe"
        probe.write_text("ok")
        probe.unlink()
        return True
    except OSError:
        return False


# ─────────────────────────── Channels ───────────────────────────

def check_smtp() -> Check:
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER", "")
    pwd  = os.environ.get("SMTP_PASS", "")
    if not (user and pwd):
        return Check(name="SMTP creds", severity="P0", status="fail",
                     detail="SMTP_USER / SMTP_PASS not set",
                     fix_hint="Owner won't receive Batman digests without this")
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


def check_owner_email() -> Check:
    target = os.environ.get("BM_OWNER_EMAIL") or os.environ.get("SMTP_USER", "")
    if not target:
        return Check(name="Owner email target", severity="P1", status="warn",
                     detail="neither BM_OWNER_EMAIL nor SMTP_USER set",
                     fix_hint="Reports will not be delivered until one is set")
    return Check(name="Owner email target", severity="info", status="info",
                 detail=target)


# ─────────────────────────── Mode ───────────────────────────

def check_mode() -> Check:
    if LIVE:
        return Check(name="Mode", severity="info", status="info",
                     detail="LIVE (BATMAN_LIVE=1)  ·  corrupted files WILL be quarantined + restored")
    return Check(name="Mode", severity="info", status="info",
                 detail="DRY-RUN (BATMAN_LIVE unset)  ·  reports only, no filesystem changes")


# ─────────────────────────── Directory health ───────────────────────────

def check_runlog_dir() -> Check:
    if not RUNLOG_DIR.exists():
        return Check(name="runlog/ directory", severity="P1", status="warn",
                     detail=f"{RUNLOG_DIR} does not exist",
                     fix_hint="Cron should be writing here — check run_all_autonomous_agents.sh")
    logs = list(RUNLOG_DIR.glob("*.log"))
    if not logs:
        return Check(name="runlog/ directory", severity="P1", status="warn",
                     detail=f"{RUNLOG_DIR.name}/ exists but has 0 logs",
                     fix_hint="The autonomous cron script writes here — verify it's actually running")
    newest = max(logs, key=lambda p: p.stat().st_mtime)
    age_h = (datetime.now() - datetime.fromtimestamp(newest.stat().st_mtime)).total_seconds() / 3600
    if age_h > 36:
        return Check(name="runlog/ directory", severity="P1", status="warn",
                     detail=f"{len(logs)} logs, newest {age_h:.1f}h old",
                     fix_hint="Cron probably stopped firing")
    return Check(name="runlog/ directory", severity="info", status="info",
                 detail=f"{len(logs)} logs, newest {age_h:.1f}h old ({newest.name})")


def check_reports_dir() -> Check:
    if not _writable(REPORTS_DIR):
        return Check(name="bm_reports/ writable", severity="P0", status="fail",
                     detail=f"cannot write to {REPORTS_DIR}",
                     fix_hint="Reports won't be persisted")
    return Check(name="bm_reports/ writable", severity="P0", status="pass",
                 detail=str(REPORTS_DIR.relative_to(DATA_DIR.parent)))


def check_quarantine_dir() -> Check:
    if LIVE and not _writable(QUARANTINE):
        return Check(name=".batman_quarantine/ writable", severity="P0", status="fail",
                     detail=f"cannot write to {QUARANTINE}",
                     fix_hint="Live mode needs this dir to safely move corrupted files aside")
    if not LIVE:
        return Check(name=".batman_quarantine/ writable", severity="info", status="info",
                     detail="(not required in dry-run mode)")
    return Check(name=".batman_quarantine/ writable", severity="info", status="info",
                 detail=str(QUARANTINE.relative_to(DATA_DIR.parent)))


# ─────────────────────────── Inputs ───────────────────────────

def check_agent_metrics() -> Check:
    p = DATA_DIR / "agent_metrics.json"
    if not p.exists():
        return Check(
            name="agent_metrics.json",
            severity="P1", status="warn",
            detail="missing — stale-agent detection silently returns []",
            fix_hint=("Most agents write to this file at the end of run_full_cycle. "
                      "If it's truly missing, restore as {} and let agents repopulate."),
        )
    m = _load(p, None)
    if not isinstance(m, dict):
        return Check(name="agent_metrics.json", severity="P1", status="warn",
                     detail=f"wrong shape: {type(m).__name__}",
                     fix_hint="Should be a dict keyed by agent_key")
    return Check(name="agent_metrics.json", severity="info", status="info",
                 detail=f"{len(m)} agents tracked")


# ─────────────────────────── Schema coverage ───────────────────────────

def check_schema_coverage() -> Check:
    all_json = sorted(p.name for p in DATA_DIR.glob("*.json"))
    covered  = set(DEFAULT_SCHEMAS.keys())
    uncovered = [f for f in all_json if f not in covered]
    total = len(all_json)
    cov_pct = (total - len(uncovered)) * 100 // max(total, 1)
    detail = (f"{total - len(uncovered)}/{total} files have auto-restore schemas "
              f"({cov_pct}%) · {len(uncovered)} uncovered")
    if uncovered and cov_pct < 50:
        # Sample some of the most-recently-touched uncovered files so the hint
        # surfaces the most-painful gaps first.
        try:
            sample = sorted(
                ((DATA_DIR / f).stat().st_mtime, f) for f in uncovered if (DATA_DIR / f).exists()
            )[-5:]
            sample_names = [s[1] for s in reversed(sample)]
        except OSError:
            sample_names = uncovered[:5]
        return Check(
            name="DEFAULT_SCHEMAS coverage",
            severity="P1", status="warn",
            detail=detail,
            fix_hint=(f"Recently-touched uncovered files: {', '.join(sample_names)}. "
                      "Add entries to DEFAULT_SCHEMAS in batman/tools.py for the agents "
                      "you can't afford to manually restore — corruption on these files "
                      "still gets quarantined but auto-repair is skipped."),
        )
    return Check(name="DEFAULT_SCHEMAS coverage", severity="info", status="info",
                 detail=detail)


# ─────────────────────────── Last report freshness ───────────────────────────

def check_last_report() -> Check:
    if not REPORTS_DIR.exists():
        return Check(name="Last report", severity="info", status="info",
                     detail="bm_reports/ does not exist (Batman has never run)")
    reports = sorted(REPORTS_DIR.glob("*.md"))
    if not reports:
        return Check(name="Last report", severity="P1", status="warn",
                     detail="bm_reports/ is empty",
                     fix_hint="Batman has never produced a report — run `python3 run_batman_auto.py` once")
    latest = reports[-1]
    age_h = (datetime.now() - datetime.fromtimestamp(latest.stat().st_mtime)).total_seconds() / 3600
    if age_h > REPORT_STALENESS_HOURS:
        return Check(
            name="Last report",
            severity="P1", status="warn",
            detail=f"newest {latest.name} is {age_h:.1f}h old (budget {REPORT_STALENESS_HOURS}h)",
            fix_hint="Daily cron probably stopped — check run_all_autonomous_agents.sh tail entry",
        )
    return Check(name="Last report", severity="info", status="info",
                 detail=f"newest {latest.name} ({age_h:.1f}h ago)  ·  {len(reports)} total")


# ─────────────────────────── Subscribers ───────────────────────────

PLAN_PRICES_MO = {"monthly_147": 147, "quarterly_497": 166, "enterprise_997": 83}


def check_subscribers() -> Check:
    subs = _load(DATA_DIR / "bm_subscribers.json", [])
    if not isinstance(subs, list):
        return Check(name="Subscribers", severity="P1", status="warn",
                     detail="bm_subscribers.json wrong shape (expected list)")
    if not subs:
        return Check(name="Subscribers", severity="info", status="info",
                     detail="0 — owner-only mode")
    active = [s for s in subs if s.get("status") == "active"]
    mrr = sum(PLAN_PRICES_MO.get(s.get("plan", ""), 0) for s in active)
    return Check(name="Subscribers", severity="info", status="info",
                 detail=f"total={len(subs)}  active={len(active)}  MRR≈${mrr}/mo")


# ─────────────────────────── Aggregate ───────────────────────────

def run_diagnostics() -> dict:
    checks = [
        check_smtp(),
        check_owner_email(),
        check_mode(),
        check_runlog_dir(),
        check_reports_dir(),
        check_quarantine_dir(),
        check_agent_metrics(),
        check_schema_coverage(),
        check_last_report(),
        check_subscribers(),
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
        print(f"  [{icon[c['status']]}] [{c['severity']:>4s}] {c['name']:32s} {c['detail']}")
        if c["fix_hint"] and c["status"] in ("fail", "warn"):
            print(f"        ↳ {c['fix_hint']}")
    s = report["summary"]
    print()
    print(f"  Result: {s['passed']}/{s['total']} passed · P0 fails={s['P0_fail']} · "
          f"P1 warns={s['P1_warn']}")
    if s["ready_to_run"]:
        print("  ✓ Batman ready to sweep. Run `--snapshot` for a no-side-effect fleet check.")
    else:
        print("  ✗ Fix P0 items above first — Batman can't operate.")


def main() -> int:
    print("Batman self-preflight\n")
    report = run_diagnostics()
    print_report(report)
    return 0 if report["summary"]["ready_to_run"] else 1


if __name__ == "__main__":
    sys.exit(main())
