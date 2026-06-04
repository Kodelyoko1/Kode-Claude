"""
ShowNotes preflight + revenue-pipeline audit.

The product: $29/episode, $99/mo (4 eps), $297/mo unlimited. The cycle
ingests transcripts from TWO sources (data/sn_inputs/*.txt owner-dropped
+ data/tr_outputs/*.txt auto-chained from Transcribe), produces
structured markdown show notes per slug, and emails *new* notes (those
not already in sn_delivery_log.json for the recipient) to active
subscribers.

Silent failure modes — none of these alert today:
  · Both input sources empty / stale → 0 produced, no alert
  · Transcribe chain idle (tr_outputs/ stops getting fresh files) but
    sn_inputs/ still has a few stragglers — quality degrades silently
  · ANTHROPIC_API_KEY set but invalid → Claude TL;DR silently swallows
    the exception and falls back to heuristic; customer paying for
    the LLM experience gets the free-tier output
  · SRT format malformed (Whisper timestamp drift) → chapters missing
  · Transcripts < SN_MIN_TRANSCRIPT_CHARS → silently skipped
  · monthly_99 advertises 4 episodes; no enforcement of the cap
  · mail_failed silent retry-loop (same shape as careerforge/pantrychef)
  · sn_subscribers.json was consumed but never written

This module answers:
  1. SMTP creds + login (P0)
  2. Input triangulation across BOTH sources (P0 if 0 candidates)
  3. Transcribe-chain idle (P1 if tr_outputs/ empty OR newest > SN_TR_CHAIN_STALE_DAYS)
  4. Claude availability — when ANTHROPIC_API_KEY is set, verify the
     key actually works (P1 if set-but-broken — paying customer issue)
  5. Episode outcome distribution (P1 if skip outcomes dominate)
  6. SRT parse failures (P1 if malformed >> parsed)
  7. Stuck mail_failed (P1, same as careerforge/pantrychef)
  8. monthly_99 cap usage (P1 if any sub is at/over 4/mo)
  9. Subscribers + MRR + one-time + by-plan
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
from shownotes.health import (
    probe_inputs,
    probe_anthropic,
    episode_outcome_summary,
    srt_outcome_summary,
    stuck_mail_failed,
    monthly_deliveries_per_email,
    TR_CHAIN_STALE_DAYS,
    MIN_TRANSCRIPT_CHARS,
)
from shownotes.subscribers import listing as sub_listing, PLANS as SUB_PLANS

DATA_DIR  = Path(__file__).parent.parent / "data"
LEADS     = DATA_DIR / "sn_leads.json"


@dataclass
class Check:
    name: str
    severity: str
    status: str
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
                     fix_hint="Required for trial outreach + show-notes delivery")
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

def check_inputs() -> Check:
    p = probe_inputs()
    if not p.get("ok"):
        return Check(name="Inputs", severity="P0", status="fail",
                     detail="sn_inputs/ empty AND tr_outputs/ empty",
                     fix_hint=("Drop transcripts at data/sn_inputs/<slug>.txt "
                               "(optional matching <slug>.srt) or run the "
                               "Transcribe agent to populate data/tr_outputs/."))
    sn_age = p["sn_inputs_newest_age_days"]
    tr_age = p["tr_outputs_newest_age_days"]
    sn_age_s = f"{sn_age}d" if sn_age is not None else "—"
    tr_age_s = f"{tr_age}d" if tr_age is not None else "—"
    return Check(name="Inputs", severity="info", status="info",
                 detail=(f"sn_inputs={p['sn_inputs']} (newest {sn_age_s})  "
                         f"tr_outputs={p['tr_outputs']} (newest {tr_age_s})  "
                         f"candidates={p['candidates']}  "
                         f"sn_outputs={p['sn_outputs']}"))


def check_tr_chain() -> Check:
    """Transcribe chain idle — tr_outputs/ stale or empty.
    Soft signal: P1 only if tr_outputs has files but ALL are stale, OR
    if it's empty AND sn_inputs is also stale (entire intake dry)."""
    p = probe_inputs()
    if not p["tr_chain_idle"]:
        return Check(name="Transcribe chain", severity="info", status="info",
                     detail=f"tr_outputs newest {p['tr_outputs_newest_age_days']}d old (within {TR_CHAIN_STALE_DAYS}d window)")
    detail = ("tr_outputs/ empty" if p["tr_outputs"] == 0
              else f"tr_outputs newest {p['tr_outputs_newest_age_days']}d old (>{TR_CHAIN_STALE_DAYS}d)")
    # If sn_inputs is fresh, this is just informational — owner is feeding directly
    sn_age = p["sn_inputs_newest_age_days"]
    if sn_age is not None and sn_age <= 14:
        return Check(name="Transcribe chain", severity="info", status="info",
                     detail=f"{detail} (sn_inputs is fresh — owner feeding directly)")
    return Check(name="Transcribe chain", severity="P1", status="warn",
                 detail=detail,
                 fix_hint=("Transcribe agent isn't producing new transcripts. "
                           "Run `python3 run_transcribe_auto.py --diagnose` to "
                           "find out why, or drop transcripts directly into "
                           "data/sn_inputs/."))


# ─────────────────────────── Claude ───────────────────────────

def check_anthropic() -> Check:
    r = probe_anthropic()
    if not r.get("enabled"):
        return Check(name="Claude TL;DR", severity="info", status="info",
                     detail=r.get("detail", "ANTHROPIC_API_KEY unset — heuristic mode"))
    if r.get("ok"):
        return Check(name="Claude TL;DR", severity="info", status="info",
                     detail=f"enabled · {r.get('detail','')}")
    # enabled=True but broken — customers paying for LLM are getting heuristic
    return Check(name="Claude TL;DR", severity="P1", status="warn",
                 detail=r.get("error", "Claude probe failed"),
                 fix_hint=("ANTHROPIC_API_KEY is set but the key is rejected "
                           "or quota is exhausted. Every TL;DR silently falls "
                           "back to the heuristic — customers paying for the "
                           "LLM experience are getting the free tier."))


# ─────────────────────────── Outcomes ───────────────────────────

def check_episode_outcomes() -> Check:
    s = episode_outcome_summary()
    if s["total"] == 0:
        return Check(name="Episode outcomes", severity="info", status="info",
                     detail="(no episodes logged yet)")
    by_source = " ".join(f"{k}={v}" for k, v in s["by_source"].items())
    detail = (f"log={s['total']}  success={s['success']}  "
              f"too_short={s['too_short']}  build_failed={s['build_failed']}  "
              f"· src: {by_source}")
    if (s["too_short"] + s["build_failed"]) > s["success"] and s["total"] >= 5:
        return Check(name="Episode outcomes", severity="P1", status="warn",
                     detail=detail,
                     fix_hint=(f"Skip outcomes outnumber successes. too_short "
                               f"= transcript <{MIN_TRANSCRIPT_CHARS} chars; "
                               "build_failed = exception in build_show_notes."))
    return Check(name="Episode outcomes", severity="info", status="info", detail=detail)


def check_srt() -> Check:
    s = srt_outcome_summary()
    if s["total"] == 0:
        return Check(name="SRT parse", severity="info", status="info",
                     detail="(no SRT outcomes logged yet)")
    detail = (f"total={s['total']}  parsed={s['parsed']}  "
              f"no_srt={s['no_srt']}  malformed={s['malformed']}")
    parseable = s["parsed"] + s["malformed"]
    if parseable >= 3 and s["malformed"] > s["parsed"]:
        sample = ", ".join(s["malformed_recent"][:4])
        return Check(name="SRT parse", severity="P1", status="warn",
                     detail=detail + f" · recent malformed: {sample}",
                     fix_hint=("More SRT files are failing to parse than "
                               "succeeding — Whisper timestamp drift or a "
                               "format change. Spot-check _parse_srt_timestamps "
                               "against one of the malformed slugs."))
    return Check(name="SRT parse", severity="info", status="info", detail=detail)


def check_stuck_mail() -> Check:
    stuck = stuck_mail_failed(min_attempts=3)
    if not stuck:
        return Check(name="Stuck mail_failed", severity="info", status="info",
                     detail="(no recipients with ≥3 mail_failed attempts)")
    sample = ", ".join(f"{s['email']}({s['attempts']}×)" for s in stuck[:4])
    extra = f" +{len(stuck) - 4}" if len(stuck) > 4 else ""
    return Check(name="Stuck mail_failed", severity="P1", status="warn",
                 detail=f"{len(stuck)} recipient(s) stuck after ≥3 mail_failed attempts: {sample}{extra}",
                 fix_hint=("Shownotes were built but mailer keeps rejecting. "
                           "Check the email address in sn_subscribers.json."))


def check_monthly_cap() -> Check:
    """monthly_99 advertises 4 episodes; surface anyone at/over that count."""
    out = sub_listing()
    if out["active"] == 0:
        return Check(name="Monthly cap", severity="info", status="info",
                     detail="(no active subscribers)")
    cap_99 = SUB_PLANS["monthly_99"]["monthly_cap"]
    sub_plan = {(s["email"].lower()): s.get("plan", "") for s in out["subscribers"]
                if s.get("status") == "active"}
    usage = monthly_deliveries_per_email()
    near = []
    over = []
    for email, count in usage.items():
        plan = sub_plan.get(email, "")
        if plan != "monthly_99":
            continue
        if count > cap_99:
            over.append((email, count))
        elif count == cap_99:
            near.append((email, count))
    if not (near or over):
        return Check(name="Monthly cap", severity="info", status="info",
                     detail=f"no monthly_99 sub is at/over the {cap_99}/mo cap")
    pieces = []
    if over:
        pieces.append("OVER cap: " + ", ".join(f"{e}({c})" for e, c in over[:3]))
    if near:
        pieces.append(f"AT cap ({cap_99}): " + ", ".join(f"{e}({c})" for e, c in near[:3]))
    return Check(name="Monthly cap", severity="P1", status="warn",
                 detail=" · ".join(pieces),
                 fix_hint=(f"monthly_99 advertises {cap_99} episodes/mo. "
                           "fulfill_cycle has no enforcement — either tighten "
                           "the docs to 'fair use' or add a per-subscriber "
                           "count gate before mailer.send."))


# ─────────────────────────── Audience + revenue ───────────────────────────

def check_subscribers() -> Check:
    out = sub_listing()
    if out["total"] == 0:
        return Check(name="Subscribers", severity="info", status="info",
                     detail="0 — owner-only mode")
    by_plan = " ".join(f"{p}={n}" for p, n in out["by_plan"].items())
    return Check(name="Subscribers", severity="info", status="info",
                 detail=(f"active={out['active']}  pending={out['pending']}  "
                         f"fulfilled={out['fulfilled']}  churned={out['churned']}  "
                         f"MRR≈${out['mrr']:.0f}/mo  "
                         f"one-time=${out['one_time_collected']}  · {by_plan}"))


def check_leads() -> Check:
    leads = _load(LEADS, [])
    if not isinstance(leads, list) or not leads:
        return Check(name="Lead pipeline", severity="info", status="info",
                     detail="0 leads — populate sn_leads.json for trial outreach")
    teased = sum(1 for l in leads if l.get("trial_sent"))
    return Check(name="Lead pipeline", severity="info", status="info",
                 detail=f"{len(leads)} lead(s) · trial_sent={teased} · pending={len(leads) - teased}")


# ─────────────────────────── Aggregate ───────────────────────────

def run_diagnostics() -> dict:
    checks = [
        check_smtp(),
        check_inputs(),
        check_tr_chain(),
        check_anthropic(),
        check_episode_outcomes(),
        check_srt(),
        check_stuck_mail(),
        check_monthly_cap(),
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
        print(f"  [{icon[c['status']]}] [{c['severity']:>4s}] {c['name']:20s} {c['detail']}")
        if c["fix_hint"] and c["status"] in ("fail", "warn"):
            print(f"        ↳ {c['fix_hint']}")
    s = report["summary"]
    print()
    print(f"  Result: {s['passed']}/{s['total']} passed · P0 fails={s['P0_fail']} · "
          f"P1 warns={s['P1_warn']}")
    if s["ready_to_run"]:
        print("  ✓ Ready to run.")
    else:
        print("  ✗ Fix P0 items above first — no shownotes would be produced.")


def main() -> int:
    print("ShowNotes preflight\n")
    report = run_diagnostics()
    print_report(report)
    return 0 if report["summary"]["ready_to_run"] else 1


if __name__ == "__main__":
    sys.exit(main())
