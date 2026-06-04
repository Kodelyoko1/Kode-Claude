"""
CarouselForge preflight + revenue-pipeline audit.
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
from carouselforge.health import (
    probe_pillow, probe_fonts, probe_inputs,
    carousel_outcome_summary, stuck_mail_failed, monthly_deliveries_per_email,
)
from carouselforge.subscribers import listing as sub_listing, PLANS as SUB_PLANS

DATA_DIR    = Path(__file__).parent.parent / "data"
LEADS       = DATA_DIR / "cr_leads.json"
OUTPUTS_DIR = DATA_DIR / "cr_outputs"


@dataclass
class Check:
    name: str
    severity: str
    status: str
    detail: str = ""
    fix_hint: str = ""


def _load(p, default):
    if not p.exists(): return default
    try: return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError): return default


def check_smtp() -> Check:
    user = os.environ.get("SMTP_USER", "")
    pwd  = os.environ.get("SMTP_PASS", "")
    if not (user and pwd):
        return Check(name="SMTP creds", severity="P0", status="fail",
                     detail="SMTP_USER / SMTP_PASS not set",
                     fix_hint="Required for outreach + carousel delivery")
    try:
        with smtplib.SMTP(os.environ.get("SMTP_HOST", "smtp.gmail.com"),
                          int(os.environ.get("SMTP_PORT", "587")), timeout=10) as srv:
            srv.starttls(); srv.login(user, pwd)
        return Check(name="SMTP auth", severity="P0", status="pass",
                     detail=f"smtp.gmail.com as {user}")
    except smtplib.SMTPAuthenticationError as e:
        return Check(name="SMTP auth", severity="P0", status="fail",
                     detail=f"Gmail rejected: {str(e)[:120]}",
                     fix_hint="Re-generate the Gmail app password")
    except Exception as e:
        return Check(name="SMTP connection", severity="P0", status="fail",
                     detail=f"{type(e).__name__}: {str(e)[:120]}")


def check_pillow() -> Check:
    r = probe_pillow()
    if r.get("ok"):
        return Check(name="Pillow", severity="P0", status="pass",
                     detail=f"version {r.get('version', '?')}")
    return Check(name="Pillow", severity="P0", status="fail",
                 detail=r.get("error", "missing"),
                 fix_hint="pip install pillow — module-level import in tools.py would raise ImportError; agent is dead without it.")


def check_fonts() -> Check:
    r = probe_fonts()
    if r.get("ok"):
        return Check(name="Fonts", severity="info", status="info",
                     detail=f"{len(r['bold_found'])} bold + {len(r['regular_found'])} regular candidates resolved")
    # Partial / total missing — Pillow falls back to its tiny default font
    missing = (r["bold_missing"] + r["regular_missing"])[:3]
    return Check(name="Fonts", severity="P1", status="warn",
                 detail=(f"bold_found={len(r['bold_found'])} "
                         f"regular_found={len(r['regular_found'])}  "
                         f"missing sample: {', '.join(Path(p).name for p in missing)}"),
                 fix_hint=("Install DejaVu / Liberation / FreeFont packages — without "
                           "any bundled font, _font() falls back to PIL's default "
                           "which is ~12pt and looks broken on 1080×1080 slides."))


def check_inputs() -> Check:
    p = probe_inputs()
    if not p.get("ok"):
        return Check(name="Inputs", severity="P0", status="fail",
                     detail="cr_inputs/ empty AND sn_outputs/ empty",
                     fix_hint=("Drop a manifest at data/cr_inputs/<slug>.json or run "
                               "ShowNotes to populate data/sn_outputs/ for auto-ingest."))
    detail = (f"cr_inputs={p['cr_inputs']}  sn_outputs={p['sn_outputs']}  "
              f"skip_markers={p['sn_skip_markers']}  candidates={p['candidates']}  "
              f"cr_outputs={p['cr_outputs']}")
    return Check(name="Inputs", severity="info", status="info", detail=detail)


def check_shownotes_chain() -> Check:
    p = probe_inputs()
    if p["sn_outputs"] == 0:
        return Check(name="ShowNotes chain", severity="P1" if p["cr_inputs"] == 0 else "info",
                     status="warn" if p["cr_inputs"] == 0 else "info",
                     detail="sn_outputs/ empty — auto-ingest has nothing to read",
                     fix_hint=("Run `python3 run_shownotes_auto.py --diagnose` to find "
                               "out why ShowNotes isn't producing.") if p["cr_inputs"] == 0 else "")
    skipped_ratio = p["sn_skip_markers"] / max(p["sn_outputs"], 1)
    if skipped_ratio >= 0.5:
        return Check(name="ShowNotes chain", severity="P1", status="warn",
                     detail=f"{p['sn_skip_markers']}/{p['sn_outputs']} shownotes have .carousel.skip markers",
                     fix_hint="More than half of ShowNotes outputs are skipped — review markers.")
    return Check(name="ShowNotes chain", severity="info", status="info",
                 detail=f"feeding from {p['sn_outputs']} sn_outputs ({p['sn_skip_markers']} skipped)")


def check_outcomes() -> Check:
    s = carousel_outcome_summary()
    if s["total"] == 0:
        return Check(name="Outcomes", severity="info", status="info",
                     detail="(no carousels logged yet)")
    by_source = " ".join(f"{k}={v}" for k, v in s["by_source"].items())
    by_plat = " ".join(f"{k}={v}" for k, v in s["by_platform"].items())
    detail = (f"log={s['total']}  success={s['success']}  "
              f"spec_invalid={s['spec_invalid']}  no_slides={s['no_slides']}  "
              f"build_failed={s['build_failed']}"
              + (f" · src: {by_source}" if by_source else "")
              + (f" · plat: {by_plat}" if by_plat else ""))
    fail = s["spec_invalid"] + s["no_slides"] + s["build_failed"]
    if fail > s["success"] and s["total"] >= 5:
        return Check(name="Outcomes", severity="P1", status="warn", detail=detail,
                     fix_hint="Skip/failure outcomes outnumber successes — triage above.")
    return Check(name="Outcomes", severity="info", status="info", detail=detail)


def check_monthly_cap() -> Check:
    out = sub_listing()
    if out["active"] == 0:
        return Check(name="Monthly cap", severity="info", status="info", detail="(no active subs)")
    plan_for = {(s["email"].lower()): s.get("plan", "")
                for s in out["subscribers"] if s.get("status") == "active"}
    usage = monthly_deliveries_per_email()
    cap_99 = SUB_PLANS["monthly_99"]["monthly_cap"]
    near, over = [], []
    for email, n in usage.items():
        if plan_for.get(email, "") != "monthly_99": continue
        if n > cap_99: over.append((email, n))
        elif n == cap_99: near.append((email, n))
    if not (near or over):
        return Check(name="Monthly cap", severity="info", status="info",
                     detail=f"no monthly_99 sub at/over {cap_99}/mo cap")
    pieces = []
    if over: pieces.append("OVER: " + ", ".join(f"{e}({n})" for e, n in over[:3]))
    if near: pieces.append(f"AT cap ({cap_99}): " + ", ".join(f"{e}({n})" for e, n in near[:3]))
    return Check(name="Monthly cap", severity="P1", status="warn",
                 detail=" · ".join(pieces),
                 fix_hint=f"monthly_99 advertises {cap_99}/mo with no enforcement.")


def check_stuck_mail() -> Check:
    stuck = stuck_mail_failed(min_attempts=3)
    if not stuck:
        return Check(name="Stuck mail_failed", severity="info", status="info",
                     detail="(no recipients with ≥3 mail_failed attempts)")
    sample = ", ".join(f"{s['email']}({s['attempts']}×)" for s in stuck[:4])
    return Check(name="Stuck mail_failed", severity="P1", status="warn",
                 detail=f"{len(stuck)} recipient(s) stuck: {sample}",
                 fix_hint="Carousels built but mailer keeps rejecting — fix the address.")


def check_subscribers() -> Check:
    out = sub_listing()
    if out["total"] == 0:
        return Check(name="Subscribers", severity="info", status="info", detail="0 — owner-only mode")
    by_plan = " ".join(f"{p}={n}" for p, n in out["by_plan"].items())
    return Check(name="Subscribers", severity="info", status="info",
                 detail=(f"active={out['active']}  pending={out['pending']}  "
                         f"churned={out['churned']}  MRR≈${out['mrr']:.0f}/mo  "
                         f"one-time=${out['one_time_collected']}  · {by_plan}"))


def check_leads() -> Check:
    leads = _load(LEADS, [])
    if not isinstance(leads, list) or not leads:
        return Check(name="Lead pipeline", severity="info", status="info",
                     detail="0 leads — populate cr_leads.json for trial outreach")
    teased = sum(1 for l in leads if l.get("trial_sent"))
    return Check(name="Lead pipeline", severity="info", status="info",
                 detail=f"{len(leads)} lead(s) · trial_sent={teased}")


def run_diagnostics() -> dict:
    checks = [check_smtp(), check_pillow(), check_fonts(), check_inputs(),
              check_shownotes_chain(), check_outcomes(), check_monthly_cap(),
              check_stuck_mail(), check_subscribers(), check_leads()]
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
        print(f"  [{icon[c['status']]}] [{c['severity']:>4s}] {c['name']:20s} {c['detail']}")
        if c["fix_hint"] and c["status"] in ("fail", "warn"):
            print(f"        ↳ {c['fix_hint']}")
    s = report["summary"]
    print(f"\n  Result: {s['passed']}/{s['total']} passed · P0={s['P0_fail']} · P1={s['P1_warn']}")
    if not s["ready_to_run"]:
        print("  ✗ Fix P0 items first — no carousels would be produced.")


def main() -> int:
    print("CarouselForge preflight\n")
    report = run_diagnostics()
    print_report(report)
    return 0 if report["summary"]["ready_to_run"] else 1


if __name__ == "__main__":
    sys.exit(main())
