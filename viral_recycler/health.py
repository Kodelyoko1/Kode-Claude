"""
Derived health stats for ViralRecycler.

ViralRecycler already writes a per-upload record to vr_uploads_log.json with
every YouTube/TikTok result inline. And vr_sources.json carries last_error on
items that failed before upload. This module derives per-niche + per-stage
breakdowns from those existing files so the owner can answer questions like:

  · Which niche is converting the most (uploads / day)?
  · Which pipeline stage is breaking most often? (download, license_gate,
    pipeline, youtube, tiktok)
  · Has anything succeeded in the last week?

No new state file — pure derivation.
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

DATA_DIR    = Path(__file__).parent.parent / "data"
QUEUE_FILE  = DATA_DIR / "vr_sources.json"
LOG_FILE    = DATA_DIR / "vr_uploads_log.json"


def _load(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return default


def derive_health(window_days: int = 30) -> dict:
    log = _load(LOG_FILE, [])
    queue = _load(QUEUE_FILE, [])
    cutoff = (datetime.now() - timedelta(days=window_days)).isoformat()

    # From the upload log
    by_niche = Counter()
    youtube_ok = 0
    youtube_fail = 0
    tiktok_ok = 0
    tiktok_skipped = 0
    last_upload = ""
    if isinstance(log, list):
        for r in log:
            if r.get("uploaded_at", "") < cutoff:
                continue
            niche = r.get("niche", "?")
            by_niche[niche] += 1
            yt = r.get("youtube", {})
            if yt.get("status") == "uploaded":
                youtube_ok += 1
                ts = r.get("uploaded_at", "")
                if ts > last_upload:
                    last_upload = ts
            elif yt.get("error"):
                youtube_fail += 1
            tt = r.get("tiktok", {})
            if tt.get("status") == "uploaded":
                tiktok_ok += 1
            elif tt.get("status") in ("skipped", "handed_off"):
                tiktok_skipped += 1

    # From the queue
    queue_errors_by_stage = Counter()
    if isinstance(queue, list):
        for s in queue:
            if not s.get("last_error"):
                continue
            # We don't always know the stage on the queue side, but the runner
            # tags it in the error dict; here we just bucket by a heuristic
            err = s["last_error"].lower()
            if "download" in err or "yt-dlp" in err or "youtube-dl" in err:
                stage = "download"
            elif "license" in err or "creative commons" in err:
                stage = "license_gate"
            elif "ffmpeg" in err:
                stage = "pipeline"
            elif "quota" in err or "credentials" in err or "oauth" in err:
                stage = "youtube_upload"
            else:
                stage = "other"
            queue_errors_by_stage[stage] += 1

    return {
        "window_days":            window_days,
        "uploads_by_niche":       dict(by_niche),
        "youtube_uploaded":       youtube_ok,
        "youtube_failed":         youtube_fail,
        "tiktok_uploaded":        tiktok_ok,
        "tiktok_skipped":         tiktok_skipped,
        "last_youtube_upload_at": last_upload,
        "queue_errors_by_stage":  dict(queue_errors_by_stage),
    }


def report_lines() -> list[str]:
    h = derive_health()
    lines = [f"== ViralRecycler — last {h['window_days']}d =="]

    if h["uploads_by_niche"]:
        lines.append("")
        lines.append("Uploads by niche:")
        for niche, n in sorted(h["uploads_by_niche"].items(), key=lambda x: -x[1]):
            lines.append(f"  {niche:<14s}  {n}")
    else:
        lines.append("\n(no uploads in the window)")

    lines.append("")
    lines.append("YouTube:")
    lines.append(f"  uploaded={h['youtube_uploaded']}  failed={h['youtube_failed']}")
    if h["last_youtube_upload_at"]:
        try:
            ts = datetime.fromisoformat(h["last_youtube_upload_at"].split("+")[0])
            age_h = (datetime.now() - ts).total_seconds() / 3600
            lines.append(f"  last_upload: {age_h:.1f}h ago")
        except ValueError:
            lines.append(f"  last_upload: {h['last_youtube_upload_at']}")

    lines.append("")
    lines.append("TikTok:")
    lines.append(f"  uploaded={h['tiktok_uploaded']}  skipped/handed_off={h['tiktok_skipped']}")

    if h["queue_errors_by_stage"]:
        lines.append("")
        lines.append("Queue errors by inferred stage:")
        for stage, n in sorted(h["queue_errors_by_stage"].items(), key=lambda x: -x[1]):
            lines.append(f"  {stage:<16s}  {n}")
    return lines
