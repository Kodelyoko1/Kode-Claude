"""
Higgsfield connector — turns each ad in the pack into a video-generation
prompt the owner pastes into Higgsfield (higgsfield.ai) to render a
cinematic short-form video creative.

Why prompts, not API:
  Higgsfield's product is primarily a web app + Discord-style UX. They
  publish image+video models (Diffuse, Soul, Higgsfield 3) but a public
  REST API for programmatic generation isn't documented. So the
  responsible integration is:
    1. We translate each ad's hook/body/audience into a Higgsfield-
       style prompt (camera, scene, motion, mood, duration).
    2. Owner pastes the prompt into Higgsfield, renders the video,
       downloads the MP4.
    3. The MP4 becomes the ad's image/video asset (replace image_hint).

If/when HIGGSFIELD_API_KEY becomes available, we can wire a true POST
push using the same prompts in api_push() below — currently a stub
that returns a helpful error so the diagnose path can warn early.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

DATA_DIR  = Path(__file__).parent.parent / "data"
PACK_DIR  = DATA_DIR / "fb_packs"


def _audience_scene(audience: str) -> dict:
    """Per-audience scene direction for Higgsfield. Each ad inherits
    these defaults; we override with the ad's hook."""
    return {
        "sellers": {
            "scene":  "warm exterior shot of a modest American home at golden hour, "
                      "a 'For Sale' sign on the lawn in the foreground",
            "camera": "slow dolly-in from the street, shallow depth of field",
            "motion": "gentle wind in tree branches, late-afternoon light",
            "mood":   "warm, reassuring, slightly melancholy — the relief of a fresh start",
            "duration_seconds": 5,
            "aspect": "9:16",  # Reels/Stories
        },
        "buyers": {
            "scene":  "modern investor at a kitchen island with laptop open showing a "
                      "spreadsheet of property addresses, coffee, morning light",
            "camera": "slow push-in to the laptop screen, 35mm",
            "motion": "subtle hand movement on trackpad, steam rising from coffee",
            "mood":   "focused, professional, the calm of a well-oiled operation",
            "duration_seconds": 6,
            "aspect": "9:16",
        },
        "wholesalers": {
            "scene":  "split-screen: left side a wholesaler frowning at a paper deal "
                      "sheet, right side the same person smiling at a laptop showing "
                      "a clean AI-generated LOI",
            "camera": "match-cut between sides, mid-shot",
            "motion": "the paper sheet drops, the laptop screen unfolds the LOI",
            "mood":   "before/after relief, problem→solution arc",
            "duration_seconds": 5,
            "aspect": "9:16",
        },
        "creators": {
            "scene":  "an indie creator's MacBook on a clean wood desk, screen shows "
                      "a Gumroad sales page being scanned — red issues highlight then "
                      "transform into green check-marks",
            "camera": "top-down lock-off, 50mm",
            "motion": "overlay annotations animate in (issues → fixes), tasteful UI motion",
            "mood":   "competent, calm — the quiet satisfaction of fixing your own product",
            "duration_seconds": 6,
            "aspect": "9:16",
        },
        "jobseekers": {
            "scene":  "applicant at home desk, frustrated at a stack of identical-looking "
                      "rejection emails, then the screen pivots to show a single tailored "
                      "resume highlighting matching keywords in green",
            "camera": "medium close-up shifting to over-the-shoulder",
            "motion": "rejection emails fade into the corner, single resume rises",
            "mood":   "frustration → control, the relief of finally being seen",
            "duration_seconds": 6,
            "aspect": "9:16",
        },
        "podcasters": {
            "scene":  "podcast host at the mic, then jump-cut to the same person in pajamas "
                      "the next morning opening an email with a finished transcript + SRT "
                      "attached, no manual work shown",
            "camera": "first half handheld energy, second half locked-off domestic warmth",
            "motion": "before: hands on the controls; after: relaxed mug of coffee",
            "mood":   "from grind to leverage — the magic of the work being already done",
            "duration_seconds": 6,
            "aspect": "9:16",
        },
        "local_biz": {
            "scene":  "small-business owner standing in their shop, looking down at a "
                      "phone showing a 1-star review, then visibly relaxing as a drafted "
                      "thoughtful reply appears on the screen",
            "camera": "mid-shot, then close-up on phone, then back to face",
            "motion": "shoulders drop, slight nod, slight smile",
            "mood":   "anxiety → professional control",
            "duration_seconds": 5,
            "aspect": "9:16",
        },
    }.get(audience, {
        "scene": "cinematic product moment matching the ad's hook",
        "camera": "smooth push-in",
        "motion": "gentle parallax",
        "mood": "competent and warm",
        "duration_seconds": 5,
        "aspect": "9:16",
    })


def build_higgsfield_prompt(ad: dict) -> dict:
    """Compose a Higgsfield-style prompt block for one ad."""
    scene = _audience_scene(ad["audience"])
    # Higgsfield's prompt format favors visual specificity over copy.
    # We compress the ad's hook into a single visual anchor.
    hook = ad["headline_full"]
    prompt = (
        f"{scene['scene']}.  "
        f"{scene['camera']}.  "
        f"{scene['motion']}.  "
        f"Mood: {scene['mood']}.  "
        f"On-screen text overlay (single line, sans-serif, "
        f"appears at second 1): \"{hook}\".  "
        f"Render as a cinematic vertical short, no logo, no watermark, "
        f"natural color grade."
    )
    return {
        "ad_name":     ad["ad_name"],
        "audience":    ad["audience"],
        "prompt":      prompt,
        "duration_s":  scene["duration_seconds"],
        "aspect":      scene["aspect"],
        "model_hint":  "Higgsfield 3  (or Soul if photoreal portrait is needed)",
        "post_text":   ad["primary_text"],   # context for the human, not the prompt
    }


def emit_prompts(pack: dict) -> Path:
    """Write a paste-ready prompts file for the owner."""
    if not pack.get("ads"):
        raise ValueError("Empty pack")
    PACK_DIR.mkdir(parents=True, exist_ok=True)
    out_path = PACK_DIR / f"{pack['date']}_higgsfield.txt"
    blocks = []
    for ad in pack["ads"]:
        p = build_higgsfield_prompt(ad)
        blocks.append(
            f"───────────────────────────────────────────────\n"
            f"  {p['ad_name']}\n"
            f"  audience: {p['audience']}  ·  aspect: {p['aspect']}  ·  "
            f"duration: {p['duration_s']}s  ·  model: {p['model_hint']}\n"
            f"───────────────────────────────────────────────\n"
            f"PROMPT:\n{p['prompt']}\n\n"
            f"AD COPY (for context, not for the prompt):\n{p['post_text']}\n\n"
        )
    out_path.write_text("\n".join(blocks))
    # Also emit a JSON sidecar for programmatic use
    json_path = PACK_DIR / f"{pack['date']}_higgsfield.json"
    json_path.write_text(json.dumps(
        [build_higgsfield_prompt(a) for a in pack["ads"]], indent=2))
    return out_path


def api_push(pack: dict, dry: bool = True) -> dict:
    """Stub for true API push to Higgsfield. Returns an explanatory
    error until HIGGSFIELD_API_KEY + a documented endpoint exist."""
    if not os.environ.get("HIGGSFIELD_API_KEY"):
        return {"ok": False, "stage": "config",
                "error": "HIGGSFIELD_API_KEY not set. Higgsfield's public REST API "
                         "may not be available yet — use --higgsfield to emit "
                         "prompts you paste into the Higgsfield web app, render "
                         "the videos, and download MP4s to use as ad creatives."}
    return {"ok": False, "stage": "wiring",
            "error": "HIGGSFIELD_API_KEY is set but the api_push() stub isn't "
                     "wired — once Higgsfield publishes a stable POST endpoint, "
                     "fill in this function (POST per-prompt, poll for video URL, "
                     "download MP4 to data/fb_packs/<ad_name>.mp4)."}
