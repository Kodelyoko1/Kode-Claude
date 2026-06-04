"""
Transcribe preflight + revenue-pipeline audit.

The product: $19/episode, $79/mo (10 hrs), $297 bulk 30-episode pack.
The cycle reads audio/video out of data/tr_inputs/, transcribes with
faster-whisper (CPU int8) — extracting WAV via ffmpeg first for video
formats — and writes {slug}.txt + {slug}.srt + {slug}.meta.json to
data/tr_outputs/. Outputs are auto-consumed by the ShowNotes agent.

Silent failure modes — none of these alert today:
  · ffmpeg not in PATH → every video input fails with "ffmpeg audio
    extract failed"; audio-only inputs still work
  · faster-whisper not installed → every input fails with the
    `_has_whisper()` early return; build_queue counts failures but
    nobody knows the cause
  · Per-file Whisper failure (corrupt file, weird codec, OOM on
    long input) → retried every cron with no detail surfaced
  · tr_inputs/ has unsupported file extensions (.txt, .pdf, .jpg
    accidentally dropped) → silently filtered before processing,
    owner doesn't realize the file was there
  · monthly_10hr_79 advertises 10 hours/mo with no enforcement —
    a single subscriber could push through 200+ hours
  · mail_failed silent retry-loop (same shape as careerforge)
  · Downstream: when Transcribe goes dark, ShowNotes' tr_outputs/
    chain idles silently
  · tr_subscribers.json was consumed but never written

This module answers, in one read-only command:
  1. SMTP creds + login (P0)
  2. ffmpeg in PATH (P0)
  3. faster-whisper importable (P0)
  4. tr_inputs/ inventory + by-extension + unsupported files (P0 empty)
  5. Per-file outcome distribution (P1 if failure-modes dominate)
  6. Per-slug stuck failures (P1)
  7. monthly_10hr_79 cap usage (P1 if any sub is at/over)
  8. Stuck mail_failed (P1)
  9. Subscribers + MRR + by-plan
 10. Downstream ShowNotes chain (info — directional reference)
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
from transcribe.health import (
    probe_inputs,
    probe_ffmpeg,
    probe_whisper,
    file_outcome_summary,
    stuck_files,
    stuck_mail_failed,
    monthly_duration_per_email,
    MONTHLY_CAP_SECONDS,
    OVER_CAP_WARN_SECONDS,
)
from transcribe.subscribers import listing as sub_listing

DATA_DIR    = Path(__file__).parent.parent / "data"
LEADS       = DATA_DIR / "tr_leads.json"
SN_INPUTS   = DATA_DIR / "sn_inputs"
SN_OUTPUTS  = DATA_DIR / "sn_outputs"
TR_OUTPUTS  = DATA_DIR / "tr_outputs"


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
                     fix_hint="Required for trial outreach + transcript delivery")
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


# ─────────────────────────── External deps ───────────────────────────

def check_ffmpeg() -> Check:
    r = probe_ffmpeg()
    if r.get("ok"):
        return Check(name="ffmpeg", severity="P0", status="pass",
                     detail=f"{r.get('path')} · {r.get('version', '')[:60]}")
    return Check(name="ffmpeg", severity="P0", status="fail",
                 detail=r.get("error", "missing"),
                 fix_hint=("Install ffmpeg (apt: `sudo apt install ffmpeg`, "
                           "brew: `brew install ffmpeg`). Without it every "
                           "video file silently errors with `ffmpeg audio "
                           "extract failed`; audio-only inputs still work."))


def check_whisper() -> Check:
    r = probe_whisper()
    if r.get("ok"):
        return Check(name="faster-whisper", severity="P0", status="pass",
                     detail=f"version {r.get('version', '?')}")
    return Check(name="faster-whisper", severity="P0", status="fail",
                 detail=r.get("error", "missing"),
                 fix_hint=("pip install faster-whisper. Without it EVERY "
                           "transcription attempt returns "
                           "`faster-whisper not installed` — the whole "
                           "agent is a no-op."))


# ─────────────────────────── Inputs ───────────────────────────

def check_inputs() -> Check:
    p = probe_inputs()
    if not p.get("ok"):
        return Check(name="Inputs", severity="P0", status="fail",
                     detail=p.get("error") or "tr_inputs/ empty",
                     fix_hint=(f"Drop audio/video into data/tr_inputs/<slug>.<ext>. "
                               f"Audio: {','.join(p.get('audio_exts', []))}. "
                               f"Video: {','.join(p.get('video_exts', []))}."))
    by_ext = ", ".join(f"{k}={v}" for k, v in sorted(p["by_ext"].items()))
    age = p.get("newest_age_days")
    age_s = f" newest {age}d old" if age is not None else ""
    detail = f"tr_inputs={p['tr_inputs']} ({by_ext}) tr_outputs={p['tr_outputs']}{age_s}"
    if p["unsupported"]:
        sample = ", ".join(p["unsupported"][:3])
        extra = f" +{len(p['unsupported']) - 3}" if len(p['unsupported']) > 3 else ""
        return Check(name="Inputs", severity="P1", status="warn",
                     detail=f"{detail} · unsupported (silently skipped): {sample}{extra}",
                     fix_hint=("Remove these files from tr_inputs/ — they're being "
                               "skipped without warning and the owner may think they're "
                               "queued."))
    return Check(name="Inputs", severity="info", status="info", detail=detail)


# ─────────────────────────── File outcomes ───────────────────────────

def check_file_outcomes() -> Check:
    s = file_outcome_summary()
    if s["total"] == 0:
        return Check(name="File outcomes", severity="info", status="info",
                     detail="(no files processed yet)")
    lang = "  ".join(f"{k}={v}" for k, v in s["language_dist"].items())
    detail = (f"log={s['total']}  success={s['success']}  "
              f"ffmpeg_failed={s['ffmpeg_failed']}  "
              f"whisper_failed={s['whisper_failed']}  "
              f"whisper_missing={s['whisper_missing']}"
              + (f" · {s['total_duration_seconds']/3600:.1f}h transcribed" if s["success"] else "")
              + (f" · lang: {lang}" if lang else ""))
    fail = (s["ffmpeg_failed"] + s["whisper_failed"] + s["whisper_missing"])
    if fail > s["success"] and s["total"] >= 5:
        return Check(name="File outcomes", severity="P1", status="warn",
                     detail=detail,
                     fix_hint="Failure outcomes outnumber successes — triage above.")
    return Check(name="File outcomes", severity="info", status="info", detail=detail)


def check_stuck_files() -> Check:
    stuck = stuck_files(min_attempts=3)
    if not stuck:
        return Check(name="Stuck files", severity="info", status="info",
                     detail="(no slugs with ≥3 failures)")
    sample = ", ".join(f"{s['slug']}(×{s['attempts']}, {s['last_outcome']})"
                       for s in stuck[:4])
    extra = f" +{len(stuck) - 4}" if len(stuck) > 4 else ""
    return Check(name="Stuck files", severity="P1", status="warn",
                 detail=f"{len(stuck)} slug(s) stuck after ≥3 failed transcriptions: {sample}{extra}",
                 fix_hint=("Same files keep failing. Inspect the source (corrupt, "
                           "weird codec, or unusual length) or drop them from "
                           "tr_inputs/ — they cost the cron a Whisper attempt every cycle."))


# ─────────────────────────── Monthly cap (10hr) ───────────────────────────

def check_monthly_cap() -> Check:
    out = sub_listing()
    if out["active"] == 0:
        return Check(name="Monthly cap", severity="info", status="info",
                     detail="(no active subscribers)")
    plan_for = {(s["email"].lower()): s.get("plan", "")
                for s in out["subscribers"] if s.get("status") == "active"}
    usage = monthly_duration_per_email()
    near, over = [], []
    for email, dur in usage.items():
        if plan_for.get(email, "") != "monthly_10hr_79":
            continue
        if dur > MONTHLY_CAP_SECONDS:
            over.append((email, dur))
        elif dur >= OVER_CAP_WARN_SECONDS:
            near.append((email, dur))
    if not (near or over):
        return Check(name="Monthly cap", severity="info", status="info",
                     detail=f"no monthly_10hr_79 sub is at/over the "
                            f"{MONTHLY_CAP_SECONDS/3600:.0f}h cap")
    pieces = []
    if over:
        pieces.append("OVER cap: " + ", ".join(
            f"{e}({d/3600:.1f}h)" for e, d in over[:3]))
    if near:
        pieces.append(f"AT cap ({OVER_CAP_WARN_SECONDS/3600:.1f}h): " + ", ".join(
            f"{e}({d/3600:.1f}h)" for e, d in near[:3]))
    return Check(name="Monthly cap", severity="P1", status="warn",
                 detail=" · ".join(pieces),
                 fix_hint=(f"monthly_10hr_79 advertises 10 hours/mo. fulfill_cycle "
                           "has no enforcement — either tighten the docs to 'fair "
                           "use' or add a per-subscriber duration gate before "
                           "delivering more transcripts."))


# ─────────────────────────── Mail ───────────────────────────

def check_stuck_mail() -> Check:
    stuck = stuck_mail_failed(min_attempts=3)
    if not stuck:
        return Check(name="Stuck mail_failed", severity="info", status="info",
                     detail="(no recipients with ≥3 mail_failed attempts)")
    sample = ", ".join(f"{s['email']}({s['attempts']}×)" for s in stuck[:4])
    extra = f" +{len(stuck) - 4}" if len(stuck) > 4 else ""
    return Check(name="Stuck mail_failed", severity="P1", status="warn",
                 detail=f"{len(stuck)} recipient(s) stuck after ≥3 mail_failed attempts: {sample}{extra}",
                 fix_hint=("Transcripts were produced but mailer keeps rejecting. "
                           "Check the email address in tr_subscribers.json."))


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
                     detail="0 leads — populate tr_leads.json for trial outreach")
    teased = sum(1 for l in leads if l.get("trial_sent"))
    return Check(name="Lead pipeline", severity="info", status="info",
                 detail=f"{len(leads)} lead(s) · trial_sent={teased} · pending={len(leads) - teased}")


# ─────────────────────────── Downstream ───────────────────────────

def check_downstream() -> Check:
    """Reference: ShowNotes auto-consumes tr_outputs/. Surface that
    chain so when Transcribe is healthy, the owner knows downstream
    is being fed."""
    p = probe_inputs()
    if p["tr_outputs"] == 0:
        return Check(name="Downstream chain", severity="info", status="info",
                     detail="tr_outputs/ empty — ShowNotes chain has nothing to ingest")
    sn_built = 0
    if SN_OUTPUTS.exists():
        sn_built = sum(1 for _ in SN_OUTPUTS.glob("*.md"))
    return Check(name="Downstream chain", severity="info", status="info",
                 detail=f"tr_outputs={p['tr_outputs']} feeding ShowNotes "
                        f"(currently {sn_built} sn_outputs/*.md built)")


# ─────────────────────────── Aggregate ───────────────────────────

def run_diagnostics() -> dict:
    checks = [
        check_smtp(),
        check_ffmpeg(),
        check_whisper(),
        check_inputs(),
        check_file_outcomes(),
        check_stuck_files(),
        check_monthly_cap(),
        check_stuck_mail(),
        check_subscribers(),
        check_leads(),
        check_downstream(),
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
        print("  ✓ Ready to run. See `--files N` / `--usage` / `--stuck` for detail.")
    else:
        print("  ✗ Fix P0 items above first — every transcription would fail.")


def main() -> int:
    print("Transcribe preflight\n")
    report = run_diagnostics()
    print_report(report)
    return 0 if report["summary"]["ready_to_run"] else 1


if __name__ == "__main__":
    sys.exit(main())
