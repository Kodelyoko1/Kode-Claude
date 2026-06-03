"""
ViralRecycler preflight.

The pipeline has more moving external pieces than any other agent in the fleet:
yt-dlp, ffmpeg, YouTube Data API OAuth, optional TikTok OAuth, disk space for
downloaded video files, and the YouTube quota cap. Most failures are silent —
the cycle just records `error` in vr_uploads_log and the queue item sits
unprocessed. Owner won't notice until they wonder why no Shorts have been
posted in a week.

This module answers in one read-only command:
  1. Binaries: ffmpeg on PATH, yt-dlp importable
  2. YouTube auth: client secrets + token file present and not obviously stale
  3. TikTok auth: TIKTOK_ACCESS_TOKEN set (informational — disabled by default)
  4. Queue: vr_sources.json depth + unprocessed count + items with last_error
  5. Daily cap usage today vs DAILY_UPLOAD_CAP (and the YouTube ~6/day quota)
  6. Disk space for the download directory
  7. Last successful upload age (P1 if > VR_LAST_UPLOAD_HOURS, default 168 = 7d)
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

DATA_DIR     = Path(__file__).parent.parent / "data"
QUEUE_FILE   = DATA_DIR / "vr_sources.json"
LOG_FILE     = DATA_DIR / "vr_uploads_log.json"
YT_SECRETS   = DATA_DIR / "yt_client_secrets.json"
YT_TOKEN     = DATA_DIR / "yt_token.json"
DOWNLOAD_DIR = DATA_DIR / "vr_downloads"

# Match viral_recycler.tools constants without forcing an expensive import chain
DAILY_UPLOAD_CAP        = 5     # mirrors tools.DAILY_UPLOAD_CAP
YOUTUBE_QUOTA_PER_UPLOAD = 1600  # units; ~10000/day → ~6 uploads
LAST_UPLOAD_HOURS_BUDGET = int(os.environ.get("VR_LAST_UPLOAD_HOURS", "168"))
DISK_FREE_GB_MIN         = float(os.environ.get("VR_DISK_FREE_GB", "2.0"))


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


# ─────────────────────────── Binaries ───────────────────────────

def check_ffmpeg() -> Check:
    if shutil.which("ffmpeg"):
        return Check(name="ffmpeg on PATH", severity="P0", status="pass",
                     detail=shutil.which("ffmpeg"))
    return Check(name="ffmpeg on PATH", severity="P0", status="fail",
                 detail="not found",
                 fix_hint="apt install ffmpeg  ·  every transform stage shells out to ffmpeg")


def check_ytdlp() -> Check:
    try:
        import yt_dlp  # noqa: F401
        return Check(name="yt_dlp importable", severity="P0", status="pass",
                     detail="installed")
    except ImportError as e:
        return Check(name="yt_dlp importable", severity="P0", status="fail",
                     detail=str(e),
                     fix_hint="pip install -U yt-dlp  ·  also bump when YouTube changes its backend")


# ─────────────────────────── YouTube auth ───────────────────────────

def check_youtube_auth() -> Check:
    if not YT_SECRETS.exists():
        return Check(
            name="YouTube OAuth client secrets",
            severity="P0", status="fail",
            detail=f"{YT_SECRETS} missing",
            fix_hint=("Create OAuth Desktop client at console.cloud.google.com, "
                      "download JSON to data/yt_client_secrets.json"),
        )
    if not YT_TOKEN.exists():
        return Check(
            name="YouTube OAuth token",
            severity="P0", status="fail",
            detail=f"{YT_TOKEN} missing",
            fix_hint="Run `python3 setup_viral_recycler.py` to authorize once (opens browser)",
        )
    # Token freshness — google-auth refreshes automatically, but if mtime is
    # very old AND no upload has succeeded recently, the refresh token may
    # have been revoked. Just surface the mtime informationally.
    age_days = (datetime.now() - datetime.fromtimestamp(YT_TOKEN.stat().st_mtime)).days
    return Check(name="YouTube OAuth token", severity="P0", status="pass",
                 detail=f"token last touched {age_days}d ago")


def check_youtube_quota_budget() -> Check:
    """Inform the owner of YouTube's hard quota ceiling vs our soft daily cap."""
    yt_ceiling = 10000 // YOUTUBE_QUOTA_PER_UPLOAD  # 6 uploads at 1600 units each
    return Check(
        name="YouTube quota budget",
        severity="info", status="info",
        detail=(f"~{yt_ceiling} uploads/day per project (10K units / {YOUTUBE_QUOTA_PER_UPLOAD} per upload)  ·  "
                f"agent soft cap = {DAILY_UPLOAD_CAP}/day"),
    )


# ─────────────────────────── TikTok auth ───────────────────────────

def check_tiktok_auth() -> Check:
    if os.environ.get("TIKTOK_ACCESS_TOKEN"):
        return Check(name="TikTok auth", severity="info", status="info",
                     detail="TIKTOK_ACCESS_TOKEN set — auto-upload enabled per source")
    return Check(
        name="TikTok auth",
        severity="info", status="info",
        detail="TIKTOK_ACCESS_TOKEN unset — falls back to email handoff",
    )


# ─────────────────────────── Queue ───────────────────────────

def check_queue() -> Check:
    queue = _load(QUEUE_FILE, [])
    if not isinstance(queue, list):
        return Check(name="vr_sources.json shape", severity="P0", status="fail",
                     detail=f"expected list, got {type(queue).__name__}")
    total = len(queue)
    unprocessed = sum(1 for s in queue if not s.get("processed"))
    errored = sum(1 for s in queue if s.get("last_error"))
    if total == 0:
        return Check(
            name="Queue",
            severity="P1", status="warn",
            detail="vr_sources.json is empty — agent will idle until owner adds URLs",
            fix_hint=('Drop entries into data/vr_sources.json: '
                      '[{"url": "...", "niche": "motivational", "allow_copyrighted": false}, ...]'),
        )
    detail = f"total={total}  unprocessed={unprocessed}  errored={errored}"
    if errored and unprocessed:
        sample_err = next((s.get("last_error", "")[:80] for s in queue if s.get("last_error")), "")
        return Check(
            name="Queue",
            severity="P1", status="warn",
            detail=detail,
            fix_hint=f"latest error: '{sample_err}' — check yt-dlp version + license_gate (allow_copyrighted flag)",
        )
    return Check(name="Queue", severity="info", status="info", detail=detail)


# ─────────────────────────── Daily-cap usage ───────────────────────────

def check_cap_usage() -> Check:
    log = _load(LOG_FILE, [])
    if not isinstance(log, list):
        return Check(name="Daily cap", severity="P1", status="warn",
                     detail=f"vr_uploads_log.json wrong shape: {type(log).__name__}")
    today = datetime.now().strftime("%Y-%m-%d")
    used = sum(1 for r in log if r.get("uploaded_at", "").startswith(today))
    remaining = max(0, DAILY_UPLOAD_CAP - used)
    if used >= DAILY_UPLOAD_CAP:
        return Check(
            name="Daily cap",
            severity="info", status="info",
            detail=f"{used}/{DAILY_UPLOAD_CAP} used today — at cap, next run will skip",
        )
    return Check(name="Daily cap", severity="info", status="info",
                 detail=f"{used}/{DAILY_UPLOAD_CAP} used today  ·  {remaining} remaining")


# ─────────────────────────── Last upload freshness ───────────────────────────

def check_last_upload() -> Check:
    log = _load(LOG_FILE, [])
    if not isinstance(log, list) or not log:
        return Check(name="Last upload", severity="info", status="info",
                     detail="no uploads in the log yet")
    # Find latest successful YouTube upload
    latest = ""
    for r in reversed(log):
        if r.get("youtube", {}).get("status") == "uploaded":
            latest = r.get("uploaded_at", "")
            break
    if not latest:
        return Check(
            name="Last upload",
            severity="P1", status="warn",
            detail=f"{len(log)} log entries but no successful YouTube upload",
            fix_hint="Inspect vr_uploads_log.json — likely auth or quota failure on every attempt",
        )
    try:
        ts = datetime.fromisoformat(latest.split("+")[0])
        age_h = (datetime.now() - ts).total_seconds() / 3600
    except ValueError:
        return Check(name="Last upload", severity="info", status="info",
                     detail=f"last uploaded_at={latest}")
    if age_h > LAST_UPLOAD_HOURS_BUDGET:
        return Check(
            name="Last upload",
            severity="P1", status="warn",
            detail=f"{age_h:.1f}h ago (budget {LAST_UPLOAD_HOURS_BUDGET}h)",
            fix_hint="Either queue is dry or every recent attempt failed — check --queue and --health-report",
        )
    return Check(name="Last upload", severity="info", status="info",
                 detail=f"{age_h:.1f}h ago")


# ─────────────────────────── Disk space ───────────────────────────

def check_disk() -> Check:
    target = DOWNLOAD_DIR if DOWNLOAD_DIR.exists() else DATA_DIR
    try:
        usage = shutil.disk_usage(str(target))
    except OSError as e:
        return Check(name="Disk space", severity="P1", status="warn",
                     detail=f"shutil.disk_usage failed: {e}")
    free_gb = usage.free / (1024 ** 3)
    if free_gb < DISK_FREE_GB_MIN:
        return Check(
            name="Disk space",
            severity="P1", status="warn",
            detail=f"only {free_gb:.1f} GB free on {target} (budget {DISK_FREE_GB_MIN} GB)",
            fix_hint="Clean data/vr_downloads/ — each video is 30-200 MB and the cron doesn't auto-prune",
        )
    return Check(name="Disk space", severity="info", status="info",
                 detail=f"{free_gb:.1f} GB free on {target}")


# ─────────────────────────── Aggregate ───────────────────────────

def run_diagnostics() -> dict:
    checks = [
        check_ffmpeg(),
        check_ytdlp(),
        check_youtube_auth(),
        check_youtube_quota_budget(),
        check_tiktok_auth(),
        check_queue(),
        check_cap_usage(),
        check_last_upload(),
        check_disk(),
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
        print("  ✓ Ready to run cycles. See --queue for queue contents, --health-report for upload-history breakdown.")
    else:
        print("  ✗ Fix P0 items above first — uploads will fail.")


def main() -> int:
    print("ViralRecycler preflight\n")
    report = run_diagnostics()
    print_report(report)
    return 0 if report["summary"]["ready_to_run"] else 1


if __name__ == "__main__":
    sys.exit(main())
