"""
Transcribe health: per-file outcomes + duration tracking + binary probes.

Transcribe is unusual among wave-2 agents because it depends on two
heavy externals: ffmpeg (for video → wav extraction) and faster-whisper
(for the actual transcription). Either being missing causes every
transcription to silently fail, but the existing build_queue counts
failures without surfacing WHY.

Per-file failure paths in tools.py:
  · src.suffix.lower() not in SUPPORTED → silently skipped (filter)
  · _has_whisper() False → `faster-whisper not installed` error
  · _extract_audio_wav fails (no ffmpeg OR ffmpeg subprocess error)
  · WhisperModel(...) or .transcribe() raises → `whisper failed: ...`
  · Mailer rejects on fulfillment → silent retry-loop

Subscription-cap concern: monthly_10hr_79 advertises 10 hours/month
($79). The cycle has no enforcement — a single subscriber could push
through 200 hours/mo without alert.

State files:
  data/tr_file_log.json       — per-file transcription outcomes + duration
  data/tr_delivery_outcomes.json — per-attempt mailer outcomes

Env:
  TR_LOG_MAX               default 300 — rolling log cap
  TR_MONTHLY_CAP_SECONDS   default 36000 — matches the 10hr=$79 plan
  TR_OVER_CAP_WARN_SECONDS default 30600 — warn-on-approach (~8.5 hr)
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

DATA_DIR     = Path(__file__).parent.parent / "data"
INPUTS_DIR   = DATA_DIR / "tr_inputs"
OUTPUTS_DIR  = DATA_DIR / "tr_outputs"
FILE_LOG     = DATA_DIR / "tr_file_log.json"
DELIVERY_LOG = DATA_DIR / "tr_delivery_outcomes.json"

LOG_MAX                  = int(os.environ.get("TR_LOG_MAX", "300"))
MONTHLY_CAP_SECONDS      = int(os.environ.get("TR_MONTHLY_CAP_SECONDS", "36000"))
OVER_CAP_WARN_SECONDS    = int(os.environ.get("TR_OVER_CAP_WARN_SECONDS", "30600"))

VALID_FILE_OUTCOMES = {"success", "unsupported_ext", "ffmpeg_failed",
                       "whisper_failed", "whisper_missing"}
VALID_DELIVERY_OUTCOMES = {"success", "mail_failed", "no_email"}

AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".opus", ".aac"}
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi"}
SUPPORTED  = AUDIO_EXTS | VIDEO_EXTS


def _now() -> str:
    return datetime.now().isoformat()


def _load(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return default


def _save(path: Path, data) -> None:
    path.parent.mkdir(exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except Exception:
        try: os.unlink(tmp)
        except OSError: pass
        raise


def _append_capped(path: Path, entry: dict) -> None:
    log = _load(path, [])
    if not isinstance(log, list):
        log = []
    log.append(entry)
    if len(log) > LOG_MAX:
        log = log[-LOG_MAX:]
    _save(path, log)


def _month_key(ts: str = "") -> str:
    return ts[:7] if ts else datetime.now().strftime("%Y-%m")


# ─────────────────────────── Per-file outcomes ───────────────────────────

def record_file(slug: str, outcome: str, duration_seconds: float = 0,
                language: str = "", detail: str = "") -> None:
    """outcome ∈ {success, unsupported_ext, ffmpeg_failed,
                   whisper_failed, whisper_missing}.
    duration_seconds is the audio duration of the source for success
    paths (used for monthly cap math)."""
    if not slug:
        return
    _append_capped(FILE_LOG, {
        "ts": _now(), "slug": slug, "outcome": outcome,
        "duration_seconds": float(duration_seconds) if duration_seconds else 0.0,
        "language": language or "",
        "detail":   detail or "",
    })


def recent_files(limit: int = 50) -> list[dict]:
    log = _load(FILE_LOG, [])
    if not isinstance(log, list):
        return []
    return log[-limit:][::-1]


def file_outcome_summary() -> dict:
    log = _load(FILE_LOG, [])
    if not isinstance(log, list) or not log:
        return {"total": 0, **{oc: 0 for oc in VALID_FILE_OUTCOMES},
                "total_duration_seconds": 0.0,
                "language_dist": {}}
    counts = {oc: 0 for oc in VALID_FILE_OUTCOMES}
    total_dur = 0.0
    lang: dict[str, int] = {}
    for r in log:
        oc = r.get("outcome", "")
        if oc in counts:
            counts[oc] += 1
        if oc == "success":
            total_dur += float(r.get("duration_seconds", 0) or 0)
            l = r.get("language", "")
            if l:
                lang[l] = lang.get(l, 0) + 1
    return {"total": len(log), **counts,
            "total_duration_seconds": round(total_dur, 1),
            "language_dist": lang}


def stuck_files(min_attempts: int = 3) -> list[dict]:
    """Per-slug repeated failures — same shape as careerforge/pantrychef
    stuck mail detection, but for transcription failures."""
    log = _load(FILE_LOG, [])
    if not isinstance(log, list):
        return []
    by_slug: dict[str, dict] = {}
    for r in log:
        oc = r.get("outcome", "")
        if oc == "success":
            # Reset on success
            by_slug.pop(r.get("slug", ""), None)
            continue
        if oc in ("unsupported_ext",):
            continue  # Skip-by-design, not a stuck case
        s = r.get("slug", "")
        if not s:
            continue
        rec = by_slug.setdefault(s, {"attempts": 0, "last_outcome": "",
                                     "last_ts": "", "last_detail": ""})
        rec["attempts"] += 1
        rec["last_outcome"] = oc
        rec["last_ts"] = r.get("ts", "")
        rec["last_detail"] = r.get("detail", "")
    return sorted(
        [{"slug": s, **rec} for s, rec in by_slug.items() if rec["attempts"] >= min_attempts],
        key=lambda r: -r["attempts"],
    )


# ─────────────────────────── Delivery outcomes ───────────────────────────

def record_delivery(email: str, outcome: str, slugs: int = 0,
                    detail: str = "") -> None:
    if not email:
        return
    _append_capped(DELIVERY_LOG, {
        "ts": _now(), "email": email.lower(), "outcome": outcome,
        "slugs": int(slugs), "detail": detail or "",
    })


def stuck_mail_failed(min_attempts: int = 3) -> list[dict]:
    log = _load(DELIVERY_LOG, [])
    if not isinstance(log, list):
        return []
    by_email: dict[str, dict] = {}
    for r in log:
        if r.get("outcome") != "mail_failed":
            continue
        e = r.get("email", "")
        if not e:
            continue
        rec = by_email.setdefault(e, {"attempts": 0, "last_ts": "", "last_detail": ""})
        rec["attempts"] += 1
        rec["last_ts"] = r.get("ts", "")
        rec["last_detail"] = r.get("detail", "")
    return sorted(
        [{"email": e, **rec} for e, rec in by_email.items() if rec["attempts"] >= min_attempts],
        key=lambda r: -r["attempts"],
    )


def monthly_duration_per_email(month: str = "") -> dict[str, float]:
    """For each subscriber email, sum the durations of files delivered
    to them this month. Used for monthly_10hr_79 cap enforcement.

    Joins the delivery log (records what slugs went to whom) with the
    file log (records duration per slug)."""
    month = month or _month_key()
    delivery_log = _load(DELIVERY_LOG, [])
    file_log     = _load(FILE_LOG, [])
    if not isinstance(delivery_log, list) or not isinstance(file_log, list):
        return {}
    # NOTE: tr_delivery_log.json (the historical file used by fulfill_cycle)
    # keeps the email→slug history. That's the authoritative source for
    # who got what. Load it here too.
    historical = _load(DATA_DIR / "tr_delivery_log.json", {})
    if not isinstance(historical, dict):
        historical = {}
    # Build slug → duration map (sum if multiple successes — last wins)
    slug_dur: dict[str, float] = {}
    for r in file_log:
        if r.get("outcome") != "success":
            continue
        if _month_key(r.get("ts", "")) != month:
            continue
        slug_dur[r.get("slug", "")] = float(r.get("duration_seconds", 0) or 0)
    # For each email, sum duration of THIS-MONTH's slugs delivered
    out: dict[str, float] = {}
    for email, slugs in historical.items():
        if not isinstance(slugs, list):
            continue
        total = sum(slug_dur.get(s, 0.0) for s in slugs)
        if total > 0:
            out[email.lower()] = round(total, 1)
    return out


# ─────────────────────────── Probes ───────────────────────────

def probe_ffmpeg() -> dict:
    """Verify ffmpeg is on PATH and report version. Without it video
    files silently fail with "ffmpeg audio extract failed"."""
    path = shutil.which("ffmpeg")
    if not path:
        return {"ok": False, "error": "ffmpeg not in PATH"}
    try:
        r = subprocess.run([path, "-version"],
                           capture_output=True, text=True, timeout=5)
        first_line = (r.stdout.splitlines() or [""])[0]
        return {"ok": r.returncode == 0, "path": path, "version": first_line[:100]}
    except Exception as e:
        return {"ok": False, "path": path, "error": f"{type(e).__name__}: {e}"}


def probe_whisper() -> dict:
    """Verify faster-whisper is importable. We don't load the model
    here — that's expensive and downloads to ~/.cache on first call.
    Just confirm the package is present."""
    try:
        import faster_whisper
        ver = getattr(faster_whisper, "__version__", "?")
        return {"ok": True, "version": ver}
    except ImportError as e:
        return {"ok": False, "error": f"faster-whisper not importable: {e}"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def probe_inputs() -> dict:
    """Triangulate input queue + by-extension breakdown + output count.

    Returns {
      "ok": bool,
      "tr_inputs":  N,
      "tr_outputs": N,
      "by_ext":     {ext: count},
      "unsupported": [paths, ...],
      "newest_age_days": N|None,
    }
    """
    if not INPUTS_DIR.exists():
        return {"ok": False, "error": "tr_inputs/ does not exist",
                "tr_inputs": 0, "tr_outputs": 0,
                "by_ext": {}, "unsupported": [], "newest_age_days": None}
    files = [f for f in INPUTS_DIR.iterdir() if f.is_file()]
    by_ext: dict[str, int] = {}
    unsupported = []
    newest_mtime = 0
    for f in files:
        suf = f.suffix.lower() or "(none)"
        by_ext[suf] = by_ext.get(suf, 0) + 1
        if f.suffix.lower() not in SUPPORTED:
            unsupported.append(f.name)
        m = f.stat().st_mtime
        if m > newest_mtime:
            newest_mtime = m
    newest_age = None
    if newest_mtime:
        newest_age = (datetime.now() - datetime.fromtimestamp(newest_mtime)).days
    outputs_n = 0
    if OUTPUTS_DIR.exists():
        outputs_n = sum(1 for _ in OUTPUTS_DIR.glob("*.meta.json"))
    return {
        "ok":              len(files) > 0,
        "tr_inputs":       len(files),
        "tr_outputs":      outputs_n,
        "by_ext":          by_ext,
        "unsupported":     unsupported,
        "newest_age_days": newest_age,
        "audio_exts":      sorted(AUDIO_EXTS),
        "video_exts":      sorted(VIDEO_EXTS),
    }


def _cli():
    import argparse
    p = argparse.ArgumentParser(description="Transcribe health + probes")
    p.add_argument("--probe",   action="store_true",
                   help="Triangulate tr_inputs + tr_outputs + by-extension")
    p.add_argument("--ffmpeg",  action="store_true",
                   help="Probe ffmpeg availability + version")
    p.add_argument("--whisper", action="store_true",
                   help="Probe faster-whisper importability + version")
    p.add_argument("--files",   type=int, default=0,
                   help="Show last N per-file outcomes")
    p.add_argument("--usage",   action="store_true",
                   help="Per-email duration this month (for monthly_10hr_79 cap)")
    p.add_argument("--stuck",   action="store_true",
                   help="Per-slug stuck transcription failures (≥3 attempts)")
    args = p.parse_args()
    if args.probe:
        print(json.dumps(probe_inputs(), indent=2))
        return
    if args.ffmpeg:
        print(json.dumps(probe_ffmpeg(), indent=2))
        return
    if args.whisper:
        print(json.dumps(probe_whisper(), indent=2))
        return
    if args.files:
        for r in recent_files(args.files):
            print(f"  {r['ts'][:19]}  {r['outcome']:<16s}  "
                  f"dur={r['duration_seconds']:>7.1f}s  "
                  f"{r['slug']}  {(r.get('detail') or '')[:40]}")
        s = file_outcome_summary()
        print(f"\n  log_total={s['total']}  success={s['success']}  "
              f"ffmpeg_failed={s['ffmpeg_failed']}  "
              f"whisper_failed={s['whisper_failed']}  "
              f"whisper_missing={s['whisper_missing']}  "
              f"unsupported_ext={s['unsupported_ext']}")
        print(f"  total_duration={s['total_duration_seconds']:.1f}s "
              f"({s['total_duration_seconds']/3600:.1f}h)")
        return
    if args.usage:
        usage = monthly_duration_per_email()
        if not usage:
            print("(no deliveries with duration recorded this month)")
        else:
            print(f"{'EMAIL':<40s}  {'HOURS':>6s} / 10  STATUS")
            for e, dur in sorted(usage.items(), key=lambda kv: -kv[1]):
                hours = dur / 3600
                if dur > MONTHLY_CAP_SECONDS:
                    tag = "OVER"
                elif dur >= OVER_CAP_WARN_SECONDS:
                    tag = "warn"
                else:
                    tag = "ok"
                print(f"  {e:<40s}  {hours:>6.2f}     [{tag}]")
        return
    if args.stuck:
        stuck = stuck_files(min_attempts=3)
        if not stuck:
            print("(no slugs with ≥3 transcription failures)")
        else:
            for r in stuck:
                print(f"  {r['slug']}  {r['attempts']}× attempts  "
                      f"last={r['last_outcome']}  detail={r['last_detail'][:60]}")
        return
    s = file_outcome_summary()
    print(f"  file log: total={s['total']}  success={s['success']}  "
          f"ffmpeg_failed={s['ffmpeg_failed']}  whisper_failed={s['whisper_failed']}  "
          f"whisper_missing={s['whisper_missing']}  unsupported_ext={s['unsupported_ext']}")
    if s["total"]:
        print(f"  total_duration={s['total_duration_seconds']/3600:.1f}h")


if __name__ == "__main__":
    _cli()
