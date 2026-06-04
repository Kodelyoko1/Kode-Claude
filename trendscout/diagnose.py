"""
TrendScout preflight + revenue-pipeline audit.

The product: paid weekly digital-product-niche newsletter ($29/mo basic,
$79/mo pro, $497/yr). The cycle reads owner-supplied snapshots from
ts_inputs/, extracts bigrams, keeps the ones appearing in ≥2 sources,
scores them, emails the top 5 to active subscribers, and emails a free
teaser to leads.

Silent failure modes — none of these alert today:
  · Owner stops dropping new snapshots → same stale niches recycle weekly
  · Only 1 input file present → cross-source dedupe degrades; quality
    collapses; cycle still "runs"
  · No subscribers + no leads → revenue dark
  · build_report returns None (low_signal) → no digest, no notice
  · ts_subscribers.json was consumed but never written; same gap fixed
    in towncrier — subscribers.py is the writer

This module answers, in one read-only command:
  1. Channels: SMTP creds + login
  2. Inputs: ts_inputs/ inventory by suffix + newest age
  3. Cross-source quality: P1 if only 1 accepted file (dedupe degraded)
  4. Recent yield: consecutive-skip streak from ts_weekly_health.json
  5. Cadence: newest ts_reports/*.md age
  6. Subscribers + MRR
  7. Leads inventory + teaser-sent rate
"""
from __future__ import annotations

import json
import os
import smtplib
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from trendscout.health import (
    summary as health_summary,
    consecutive_skips,
    probe_inputs,
    ALERT_AFTER_SKIPS,
    MIN_SOURCES_HEALTHY,
)
from trendscout.subscribers import listing as sub_listing

DATA_DIR    = Path(__file__).parent.parent / "data"
INPUT_DIR   = DATA_DIR / "ts_inputs"
REPORTS_DIR = DATA_DIR / "ts_reports"
LEADS_FILE  = DATA_DIR / "ts_leads.json"


@dataclass
class Check:
    name: str
    severity: str   # "P0" | "P1" | "info"
    status: str     # "pass" | "fail" | "warn" | "info"
    detail: str = ""
    fix_hint: str = ""


def _load(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return default


# ─────────────────────────── Channels ───────────────────────────

def check_smtp() -> Check:
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER", "")
    pwd  = os.environ.get("SMTP_PASS", "")
    if not (user and pwd):
        return Check(name="SMTP creds", severity="P0", status="fail",
                     detail="SMTP_USER / SMTP_PASS not set",
                     fix_hint="Required for teaser outreach + subscriber digest")
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


# ─────────────────────────── Inputs ───────────────────────────

def check_input_inventory() -> Check:
    p = probe_inputs()
    if not p.get("ok"):
        return Check(name="Input inventory", severity="P0", status="fail",
                     detail=p.get("error") or f"0 accepted files in {INPUT_DIR}",
                     fix_hint=(f"Drop snapshots into data/ts_inputs/ (suffixes "
                               f"{','.join(p.get('accepted_suffixes', []))}). "
                               "Owner-fed agent — no auto-scrape."))
    by_suf = ", ".join(f"{k}={v}" for k, v in sorted(p["by_suffix"].items()))
    age = p.get("newest_age_days")
    age_str = f" · newest {age}d old" if age is not None else ""
    severity, status = "info", "info"
    fix = ""
    if age is not None and age > 14:
        severity, status = "P1", "warn"
        fix = ("Inputs are stale — same bigrams will keep ranking and "
               "subscribers will see repeat niches. Refresh ts_inputs/.")
    return Check(name="Input inventory", severity=severity, status=status,
                 detail=f"{p['accepted']} accepted file(s) — {by_suf}{age_str}",
                 fix_hint=fix)


def check_cross_source_quality() -> Check:
    """Below MIN_SOURCES_HEALTHY accepted files, the cross-source dedupe
    in scan_signals() degrades to single-source mode."""
    p = probe_inputs()
    if not p.get("ok"):
        return Check(name="Cross-source dedupe", severity="info", status="info",
                     detail="(skipped — no inputs)")
    n = p["accepted"]
    if n < MIN_SOURCES_HEALTHY:
        return Check(name="Cross-source dedupe", severity="P1", status="warn",
                     detail=f"{n}/{MIN_SOURCES_HEALTHY} sources — single-source fallback active",
                     fix_hint=("scan_signals() only enforces the ≥2-source filter when "
                               "≥2 inputs exist. Below that, every popular bigram wins. "
                               "Add a second source."))
    return Check(name="Cross-source dedupe", severity="info", status="info",
                 detail=f"{n} source(s) ≥ threshold {MIN_SOURCES_HEALTHY}")


# ─────────────────────────── Weekly yield ───────────────────────────

def check_recent_yield() -> Check:
    s = health_summary()
    if s["weeks"] == 0:
        return Check(name="Recent yield", severity="P1", status="warn",
                     detail="no weeks tracked yet — run a cycle first",
                     fix_hint="Run `python3 run_trendscout_auto.py` once to populate ts_weekly_health.json")
    cs = consecutive_skips()
    if cs >= ALERT_AFTER_SKIPS:
        return Check(name="Recent yield", severity="P1", status="warn",
                     detail=(f"{s['delivered']}/{s['weeks']} delivered · "
                             f"streak: -{cs} consecutive skip(s) "
                             f"(threshold {ALERT_AFTER_SKIPS}) · "
                             f"last delivered: {s['last_delivered'] or '—'}"),
                     fix_hint=("Multiple weeks in a row produced <3 scored niches "
                               "(low_signal). Check input freshness and "
                               "Cross-source dedupe above."))
    return Check(name="Recent yield", severity="info", status="info",
                 detail=(f"{s['delivered']}/{s['weeks']} weeks delivered · "
                         f"all-time sent: {s['total_sent']} · "
                         f"last delivered: {s['last_delivered'] or '—'}"))


# ─────────────────────────── Output cadence ───────────────────────────

def check_reports() -> Check:
    if not REPORTS_DIR.exists():
        return Check(name="Report cadence", severity="info", status="info",
                     detail="ts_reports/ does not exist (no cycles run yet)")
    files = sorted(REPORTS_DIR.glob("*.md"))
    if not files:
        return Check(name="Report cadence", severity="info", status="info", detail="(empty)")
    last = files[-1]
    age = (datetime.now() - datetime.fromtimestamp(last.stat().st_mtime)).days
    # Weekly cadence: >10d suspicious, >21d firmly broken.
    if age > 21:
        return Check(name="Report cadence", severity="P1", status="warn",
                     detail=f"{len(files)} report(s), newest {age}d old ({last.name})",
                     fix_hint="No digest in 3+ weeks — see Recent yield + Input inventory.")
    if age > 10:
        return Check(name="Report cadence", severity="P1", status="warn",
                     detail=f"{len(files)} report(s), newest {age}d old ({last.name})",
                     fix_hint="Weekly cadence slipping — check cron + low-signal streak.")
    return Check(name="Report cadence", severity="info", status="info",
                 detail=f"{len(files)} report(s), newest {age}d old")


# ─────────────────────────── Subscribers + MRR ───────────────────────────

def check_subscribers() -> Check:
    out = sub_listing()
    if out["total"] == 0:
        return Check(name="Subscribers", severity="info", status="info",
                     detail="0 — owner-only mode")
    return Check(name="Subscribers", severity="info", status="info",
                 detail=(f"active={out['active']}  pending={out['pending']}  "
                         f"churned={out['churned']}  MRR≈${out['mrr']:.0f}/mo"))


# ─────────────────────────── Leads (teaser pipeline) ───────────────────────────

def check_leads() -> Check:
    leads = _load(LEADS_FILE, [])
    if not isinstance(leads, list) or not leads:
        return Check(name="Lead pipeline", severity="info", status="info",
                     detail="0 leads — populate ts_leads.json to power teaser outreach")
    teased = sum(1 for l in leads if l.get("teaser_sent"))
    unteased = len(leads) - teased
    return Check(name="Lead pipeline", severity="info", status="info",
                 detail=f"{len(leads)} lead(s) · teaser_sent={teased} · pending={unteased}")


# ─────────────────────────── Aggregate ───────────────────────────

def run_diagnostics() -> dict:
    checks = [
        check_smtp(),
        check_input_inventory(),
        check_cross_source_quality(),
        check_recent_yield(),
        check_reports(),
        check_subscribers(),
        check_leads(),
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
        print(f"  [{icon[c['status']]}] [{c['severity']:>4s}] {c['name']:25s} {c['detail']}")
        if c["fix_hint"] and c["status"] in ("fail", "warn"):
            print(f"        ↳ {c['fix_hint']}")
    s = report["summary"]
    print()
    print(f"  Result: {s['passed']}/{s['total']} passed · P0 fails={s['P0_fail']} · "
          f"P1 warns={s['P1_warn']}")
    if s["ready_to_run"]:
        print("  ✓ Ready to run. See `--health-report` for per-week detail.")
    else:
        print("  ✗ Fix P0 items above first — cycle would produce no digest.")


def main() -> int:
    print("TrendScout preflight\n")
    report = run_diagnostics()
    print_report(report)
    return 0 if report["summary"]["ready_to_run"] else 1


if __name__ == "__main__":
    sys.exit(main())
