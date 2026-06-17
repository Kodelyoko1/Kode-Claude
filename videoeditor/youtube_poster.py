"""
YouTube poster module for VideoEditor.

Env vars (all optional):
  YT_AUTO_POST=1        enable auto-posting after every process run (default off)
  YT_POST_MASTER=1      also post the full master video (default: reels only)
  YT_PRIVACY=public     public | unlisted | private  (default: public)
  YT_CATEGORY=22        YouTube category ID (default 22 = People & Blogs)
  ANTHROPIC_API_KEY     if set, Claude haiku generates title/description/tags

Caption upload requires the captions scope — re-run authorize_youtube_manual.py
if your token pre-dates this update.
"""

import json
import os
from pathlib import Path

DATA = Path("data")

# ── metadata generation ───────────────────────────────────────────────────────

def _slug_to_title(slug: str) -> str:
    return slug.replace("_", " ").replace("-", " ").title()


def _heuristic_metadata(slug: str, is_short: bool) -> dict:
    title = _slug_to_title(slug)
    if is_short:
        title = f"{title} #Shorts"
    description = (
        f"{_slug_to_title(slug)}\n\n"
        "Subscribe for more content!\n\n"
        "#shorts #video #content"
    )
    tags = slug.replace("_", " ").replace("-", " ").split() + ["shorts", "video", "content"]
    return {"title": title[:100], "description": description[:5000], "tags": tags[:15]}


def _ai_metadata(slug: str, transcript: str | None, duration_s: float, is_short: bool) -> dict:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return _heuristic_metadata(slug, is_short)

    try:
        import anthropic
    except ImportError:
        return _heuristic_metadata(slug, is_short)

    context = f"Video name: {slug}\nDuration: {duration_s:.0f}s"
    if is_short:
        context += "\nFormat: vertical Short / Reel (under 60s)"
    if transcript:
        context += f"\n\nTranscript (excerpt):\n{transcript[:3000]}"

    prompt = (
        "You write YouTube metadata. Based on the video info below, produce an "
        "engaging title, description, and tags.\n\n"
        f"{context}\n\n"
        "Rules:\n"
        "- title: under 100 chars, hook-first; append ' #Shorts' if it's a Short\n"
        "- description: 3-4 sentences, conversational; end with 3-5 hashtags\n"
        "- tags: 10-15 specific tags as a JSON array\n\n"
        'Return ONLY valid JSON (no markdown):\n'
        '{"title":"...","description":"...","tags":["..."]}'
    )

    try:
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}],
        )
        text = msg.content[0].text.strip()
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip().rstrip("`").strip()
        return json.loads(text)
    except Exception as exc:
        print(f"[VideoEditor/YT] AI metadata failed ({exc}); using heuristic")
        return _heuristic_metadata(slug, is_short)


def generate_metadata(
    slug: str,
    transcript: str | None,
    duration_s: float,
    is_short: bool = False,
) -> dict:
    """Return {title, description, tags}. Uses Claude if API key is set."""
    return _ai_metadata(slug, transcript, duration_s, is_short)


# ── caption upload ────────────────────────────────────────────────────────────

def _upload_captions(service, video_id: str, srt_path: Path) -> dict:
    try:
        from googleapiclient.http import MediaFileUpload
    except ImportError:
        return {"error": "googleapiclient not installed"}
    body = {
        "snippet": {
            "videoId": video_id,
            "language": "en",
            "name": "English",
            "isDraft": False,
        }
    }
    media = MediaFileUpload(str(srt_path), mimetype="text/plain", resumable=False)
    try:
        resp = service.captions().insert(
            part="snippet", body=body, media_body=media
        ).execute()
        return {"status": "uploaded", "caption_id": resp.get("id")}
    except Exception as exc:
        return {"error": f"caption upload failed: {exc}"}


# ── main entry point ──────────────────────────────────────────────────────────

def post_to_youtube(meta: dict, post_master: bool = False) -> dict:
    """
    Upload VideoEditor outputs to YouTube.

    meta         — dict returned by videoeditor.tools.process_video()
    post_master  — also upload the full master video (not just reels)

    Returns {"youtube_posts": [...per-upload result dicts...]}
    """
    from viral_recycler.youtube import _build_service, upload as yt_upload

    privacy = os.getenv("YT_PRIVACY", "public")
    category = os.getenv("YT_CATEGORY", "22")
    slug = meta["slug"]

    # Load transcript if Transcribe agent has already processed this video
    transcript: str | None = None
    tr_txt = DATA / "tr_outputs" / f"{slug}.txt"
    if tr_txt.exists():
        transcript = tr_txt.read_text(encoding="utf-8", errors="replace")[:4000]

    srt_path = DATA / "tr_outputs" / f"{slug}.srt"
    have_srt = srt_path.exists()

    service, svc_err = _build_service()
    if svc_err:
        return {"error": svc_err, "youtube_posts": []}

    results: list[dict] = []

    def _post_one(file_path: str, is_short: bool, clip_duration: float) -> dict:
        md = generate_metadata(slug, transcript, clip_duration, is_short=is_short)
        r = yt_upload(
            video_path=file_path,
            title=md["title"],
            description=md["description"],
            tags=md.get("tags", []),
            privacy=privacy,
            category_id=category,
        )
        if r.get("status") == "uploaded" and have_srt:
            r["captions"] = _upload_captions(service, r["video_id"], srt_path)
        r["file"] = file_path
        r["is_short"] = is_short
        r["metadata_used"] = md
        return r

    # Always post reels as Shorts
    for reel in meta.get("reels", []):
        results.append(_post_one(reel["file"], is_short=True, clip_duration=reel["duration_s"]))

    # Optionally post the master as a full-length video
    if post_master:
        results.append(_post_one(meta["master"], is_short=False, clip_duration=meta["source_duration_s"]))

    return {"youtube_posts": results}
