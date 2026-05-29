"""
TikTok uploader.

TikTok has two paths and we support both:

  Path A — TikTok Content Posting API (free; requires app approval at
           developers.tiktok.com → can take 1-3 weeks for new accounts).
           When approved, uploads run unattended like YouTube.

  Path B — Manual handoff (works today, no API needed).
           Agent emails you the finished MP4 + ready-to-paste caption + hashtags.
           You open TikTok on your phone, tap upload, paste, done. ~30 seconds.

The agent auto-picks Path A if TIKTOK_ACCESS_TOKEN is set in .env;
otherwise falls back to Path B.
"""
import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from autonomous import mailer

AGENT_KEY = "viral_recycler"


def has_official_api() -> bool:
    return bool(os.environ.get("TIKTOK_ACCESS_TOKEN"))


def upload_official(video_path: str, caption: str, hashtags: list) -> dict:
    """Upload via TikTok's Content Posting API."""
    try:
        import requests
    except ImportError:
        return {"error": "pip install requests"}
    token = os.environ.get("TIKTOK_ACCESS_TOKEN")
    if not token:
        return {"error": "no TIKTOK_ACCESS_TOKEN in env"}

    # Step 1: init upload
    init_url = "https://open.tiktokapis.com/v2/post/publish/video/init/"
    file_size = Path(video_path).stat().st_size
    init_body = {
        "post_info": {
            "title": (caption + " " + " ".join(hashtags))[:2200],
            "privacy_level": "PUBLIC_TO_EVERYONE",
            "disable_duet": False,
            "disable_stitch": False,
            "disable_comment": False,
        },
        "source_info": {
            "source": "FILE_UPLOAD",
            "video_size": file_size,
            "chunk_size": file_size,
            "total_chunk_count": 1,
        },
    }
    r = requests.post(init_url,
                      headers={"Authorization": f"Bearer {token}",
                               "Content-Type": "application/json"},
                      json=init_body, timeout=30)
    if r.status_code != 200:
        return {"error": f"init failed: {r.status_code} {r.text[:300]}"}
    init_data = r.json().get("data", {})
    upload_url = init_data.get("upload_url")
    publish_id = init_data.get("publish_id")
    if not upload_url:
        return {"error": f"no upload_url: {r.text[:300]}"}

    # Step 2: upload the file bytes
    with open(video_path, "rb") as f:
        up = requests.put(upload_url,
                          data=f,
                          headers={
                              "Content-Type": "video/mp4",
                              "Content-Range": f"bytes 0-{file_size - 1}/{file_size}",
                          }, timeout=600)
    if up.status_code not in (200, 201):
        return {"error": f"upload PUT failed: {up.status_code}"}

    return {
        "status": "uploaded",
        "publish_id": publish_id,
        "uploaded_at": datetime.now().isoformat(),
    }


def upload_via_handoff(video_path: str, caption: str, hashtags: list,
                       to_email: str = "") -> dict:
    """Path B: email the finished file with paste-ready caption."""
    to_email = to_email or os.environ.get("SMTP_USER", "")
    if not to_email:
        return {"error": "no recipient email"}
    body = (
        f"Hi — TikTok handoff for your latest Short.\n\n"
        f"📱 To post:\n"
        f"  1. Open TikTok on your phone\n"
        f"  2. Tap the + button → upload from gallery → pick the attached MP4\n"
        f"  3. Tap Next → paste the caption below → post\n\n"
        f"--- READY-TO-PASTE CAPTION ---\n\n"
        f"{caption}\n\n{' '.join(hashtags)}\n\n"
        f"--- END ---\n\n"
        f"File: {Path(video_path).name}\n"
        f"Once TikTok approves your Content Posting API app, this'll go full-auto."
    )
    result = mailer.send(AGENT_KEY, to_email,
                         f"TikTok ready-to-upload: {caption[:50]}",
                         body, purpose="fulfillment",
                         attachments=[video_path])
    if result.get("status") == "sent":
        return {"status": "handed_off", "to": to_email,
                "uploaded_at": datetime.now().isoformat()}
    return {"error": f"handoff email failed: {result.get('error', '')}"}


def upload(video_path: str, caption: str, hashtags: list, to_email: str = "") -> dict:
    if has_official_api():
        return upload_official(video_path, caption, hashtags)
    return upload_via_handoff(video_path, caption, hashtags, to_email=to_email)
