"""
ViralRecycler — autonomous video repurposing.

Pipeline per source URL:
  1. Download via yt-dlp (downloader.py)
  2. Transcribe (yt-dlp subs if available; else skip captions)
  3. Pick best 30-50s segment (shortsforge segment heuristic)
  4. Generate hook + SEO pack via ShortsForge logic
  5. Transform video with ffmpeg (recut, vertical, mirror, captions, intro/outro)
  6. Upload to YouTube via Data API
  7. Upload to TikTok via official API OR email handoff
  8. Log everything; update metrics

Owner workflow:
  Drop URLs into data/vr_sources.json like:
    [{"url": "...", "niche": "motivational", "allow_copyrighted": false}, ...]
  Agent processes the queue on each run.

Niches: motivational, comedy, wellness.
"""
import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from autonomous import storage, mailer, metrics
from shortsforge.tools import (
    detect_niche, find_best_segment, generate_hook, seo_pack, get_channel_config
)
from viral_recycler import downloader, transformer, youtube, tiktok
from viral_recycler.pro_pipeline import run_pipeline

AGENT_KEY = "viral_recycler"

UPLOADS_PER_RUN_DEFAULT = 1     # very conservative: 1 video/run by default
DAILY_UPLOAD_CAP = 2            # safety: never more than 2/day to avoid spam-flag


def _today_uploads() -> int:
    log = storage.load("vr_uploads_log.json", [])
    today = datetime.now().strftime("%Y-%m-%d")
    return sum(1 for r in log if r.get("uploaded_at", "").startswith(today))


def _log_upload(record: dict):
    log = storage.load("vr_uploads_log.json", [])
    log.append(record)
    storage.save("vr_uploads_log.json", log)


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:60]


def process_one(source: dict) -> dict:
    """Run the full pro pipeline for one source URL."""
    url = source["url"]
    niche = source.get("niche", "motivational")
    allow_copyrighted = source.get("allow_copyrighted", False)
    quality_tier = source.get("quality_tier", "pro")  # basic, pro, studio

    slug = _slug(source.get("slug", "") or url.split("/")[-1] or "video")

    # 1. Download
    dl = downloader.download(url, slug=slug)
    if "error" in dl:
        return {"stage": "download", **dl}

    # Safety gate
    if not dl.get("is_creative_commons") and not allow_copyrighted:
        return {"stage": "license_gate",
                "error": "not creative commons; set allow_copyrighted: true to override",
                "uploader": dl.get("uploader", "")}

    fallback_transcript = downloader.fetch_subtitles(slug) or dl.get("title", "")

    # 2. Run the pro pipeline (handles transcribe, segment, transform, color, audio, etc.)
    pipeline = run_pipeline(
        source_video=Path(dl["video_path"]),
        output_slug=slug,
        niche=niche,
        quality_tier=quality_tier,
        fallback_transcript=fallback_transcript,
    )
    if "errors" in pipeline and pipeline.get("errors") and not pipeline.get("final_path"):
        return {"stage": "pipeline", "error": pipeline["errors"]}

    # 3. Credit + description
    description = pipeline["description"]
    if dl.get("uploader"):
        description += f"\n\n🎬 Original: {dl['uploader']}"
        if dl.get("uploader_url"):
            description += f" — {dl['uploader_url']}"

    # 4. Upload to YouTube
    yt = youtube.upload(
        video_path=pipeline["final_path"],
        title=pipeline["title"],
        description=description,
        tags=[h.lstrip("#") for h in pipeline["hashtags"]],
        privacy="public",
    )

    # 5. Upload to TikTok
    tt = tiktok.upload(
        video_path=pipeline["final_path"],
        caption=pipeline["hook"],
        hashtags=pipeline["hashtags"],
    )

    record = {
        "slug":           slug,
        "source_url":     url,
        "niche":          niche,
        "quality_tier":   quality_tier,
        "hook":           pipeline["hook"],
        "title":          pipeline["title"],
        "output_path":    pipeline["final_path"],
        "thumbnail_path": pipeline.get("thumbnail_path", ""),
        "aspects":        pipeline.get("aspects", {}),
        "variants":       pipeline.get("variants", []),
        "pipeline_stages": pipeline.get("stages", []),
        "youtube":        yt,
        "tiktok":         tt,
        "uploaded_at":    datetime.now().isoformat(),
        "credit_to":      dl.get("uploader", ""),
    }
    _log_upload(record)
    return record


def queue_cycle(max_uploads: int = UPLOADS_PER_RUN_DEFAULT) -> dict:
    queue = storage.load("vr_sources.json", [])
    if not isinstance(queue, list):
        queue = []
    today_count = _today_uploads()
    available = max(0, DAILY_UPLOAD_CAP - today_count)
    limit = min(max_uploads, available)
    processed = 0
    errors = []
    successes = []
    pending = []
    for source in queue:
        if processed >= limit:
            pending.append(source)
            continue
        if source.get("processed"):
            continue
        result = process_one(source)
        if "error" in result:
            errors.append({"url": source["url"], "error": result.get("error"),
                            "stage": result.get("stage", "unknown")})
            source["last_error"] = result.get("error")
        else:
            source["processed"] = True
            source["processed_at"] = datetime.now().isoformat()
            source["youtube_url"] = result.get("youtube", {}).get("shorts_url", "")
            successes.append(result)
            processed += 1
    storage.save("vr_sources.json", queue)
    return {
        "uploads_today_after": today_count + processed,
        "uploaded": processed,
        "errors":   errors,
        "skipped":  len(pending),
        "successes": successes,
    }


def run_full_cycle(max_uploads: int = UPLOADS_PER_RUN_DEFAULT) -> dict:
    r = queue_cycle(max_uploads=max_uploads)
    youtube_ok = sum(1 for s in r["successes"]
                     if s.get("youtube", {}).get("status") == "uploaded")
    tiktok_ok = sum(1 for s in r["successes"]
                    if s.get("tiktok", {}).get("status") in ("uploaded", "handed_off"))
    metrics.record(
        AGENT_KEY,
        videos_processed=r["uploaded"],
        youtube_posts=youtube_ok,
        tiktok_posts=tiktok_ok,
        errors=len(r["errors"]),
        last_status="ok" if r["uploaded"] else ("blocked" if r["errors"] else "idle"),
    )
    return r
