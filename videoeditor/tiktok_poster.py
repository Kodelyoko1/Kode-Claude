"""
TikTok poster module for VideoEditor.

Two modes (auto-selected):
  Path A — TikTok Content Posting API (set TIKTOK_ACCESS_TOKEN in .env).
            App approval at developers.tiktok.com takes 1-3 weeks for new accounts.
  Path B — Handoff: agent emails you the MP4 + paste-ready caption + hashtags.
            Works today with zero API setup.

Env vars:
  TIKTOK_ACCESS_TOKEN   — if set, use the official API (Path A)
  TIKTOK_POST_REELS=1   — post reels to TikTok (default on when tiktok_post=True)
  ANTHROPIC_API_KEY     — if set, Claude Haiku writes the caption + hashtags
"""

import json
import os
from pathlib import Path

DATA = Path("data")


# ── caption generation ────────────────────────────────────────────────────────

def _slug_to_title(slug: str) -> str:
    return slug.replace("_", " ").replace("-", " ").title()


def _heuristic_caption(slug: str, duration_s: float) -> dict:
    title = _slug_to_title(slug)
    hashtags = ["#fyp", "#foryou", "#viral", "#trending", "#video"]
    return {"caption": title, "hashtags": hashtags}


def _ai_caption(slug: str, transcript: str | None, duration_s: float) -> dict:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return _heuristic_caption(slug, duration_s)

    try:
        import anthropic
    except ImportError:
        return _heuristic_caption(slug, duration_s)

    context = f"Video name: {slug}\nDuration: {duration_s:.0f}s\nFormat: vertical TikTok / Short"
    if transcript:
        context += f"\n\nTranscript (excerpt):\n{transcript[:2000]}"

    prompt = (
        "You write TikTok captions. Based on the video info, produce a hook caption and hashtags.\n\n"
        f"{context}\n\n"
        "Rules:\n"
        "- caption: 1-2 punchy sentences, hook-first, conversational, under 150 chars\n"
        "- hashtags: 5-8 relevant TikTok hashtags as a JSON array (include #fyp)\n"
        "- total caption+hashtags must stay under 2200 chars\n\n"
        'Return ONLY valid JSON (no markdown):\n'
        '{"caption":"...","hashtags":["#fyp","..."]}'
    )

    try:
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
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
        print(f"[VideoEditor/TikTok] AI caption failed ({exc}); using heuristic")
        return _heuristic_caption(slug, duration_s)


def generate_caption(slug: str, transcript: str | None, duration_s: float) -> dict:
    """Return {caption, hashtags}. Uses Claude Haiku if ANTHROPIC_API_KEY is set."""
    return _ai_caption(slug, transcript, duration_s)


# ── main entry point ──────────────────────────────────────────────────────────

def post_to_tiktok(meta: dict) -> dict:
    """
    Upload VideoEditor reels to TikTok.

    meta — dict returned by videoeditor.tools.process_video()

    Returns {"tiktok_posts": [...per-upload result dicts...]}
    """
    from viral_recycler.tiktok import upload as tt_upload

    slug = meta["slug"]

    # Load transcript if available
    transcript: str | None = None
    tr_txt = DATA / "tr_outputs" / f"{slug}.txt"
    if tr_txt.exists():
        transcript = tr_txt.read_text(encoding="utf-8", errors="replace")[:3000]

    reels = meta.get("reels", [])
    if not reels:
        return {"tiktok_posts": [], "note": "no reels to post"}

    # Prefer 60s reel; fall back to 30s
    reel = next((r for r in reels if r["duration_s"] == 60), reels[-1])

    md = generate_caption(slug, transcript, reel["duration_s"])
    caption = md["caption"]
    hashtags = md.get("hashtags", ["#fyp", "#foryou", "#viral"])

    r = tt_upload(
        video_path=reel["file"],
        caption=caption,
        hashtags=hashtags,
    )
    r["file"] = reel["file"]
    r["duration_s"] = reel["duration_s"]
    r["caption_used"] = caption
    r["hashtags_used"] = hashtags

    return {"tiktok_posts": [r]}
