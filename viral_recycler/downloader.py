"""
Video downloader — wraps yt-dlp to pull viral videos from YouTube, TikTok,
Instagram, Twitter/X, Reddit.

Owner pastes URLs into data/vr_sources.json; the agent downloads them.
Default source filter: Creative Commons / explicit permission only.
Unsafe sources can be allowed only with `allow_copyrighted: true` per item.
"""
import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

DOWNLOAD_DIR = Path(__file__).parent.parent / "data" / "vr_downloads"


def has_yt_dlp() -> bool:
    return shutil.which("yt-dlp") is not None


def download(url: str, slug: str = "") -> dict:
    """Download a video URL to a local MP4. Returns metadata."""
    if not has_yt_dlp():
        return {"error": "yt-dlp not installed. Run: pip install yt-dlp"}
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    slug = slug or f"video_{datetime.now():%Y%m%d_%H%M%S}"
    out_template = str(DOWNLOAD_DIR / f"{slug}.%(ext)s")
    info_path = DOWNLOAD_DIR / f"{slug}.info.json"
    cmd = [
        "yt-dlp",
        "-f", "best[height<=1080][ext=mp4]/best[ext=mp4]/best",
        "--merge-output-format", "mp4",
        "--write-info-json",
        "--write-auto-subs",
        "--sub-lang", "en",
        "--restrict-filenames",
        "-o", out_template,
        url,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode != 0:
            return {"error": f"yt-dlp failed: {result.stderr[-400:]}"}
    except subprocess.TimeoutExpired:
        return {"error": "yt-dlp timed out (>10min)"}

    mp4 = DOWNLOAD_DIR / f"{slug}.mp4"
    if not mp4.exists():
        # Pick whatever extension yt-dlp produced
        candidates = list(DOWNLOAD_DIR.glob(f"{slug}.*"))
        candidates = [c for c in candidates if c.suffix not in (".json", ".vtt", ".srt")]
        if not candidates:
            return {"error": "Download produced no video file"}
        mp4 = candidates[0]

    meta = {}
    if info_path.exists():
        try:
            meta = json.loads(info_path.read_text())
        except Exception:
            meta = {}

    return {
        "video_path":  str(mp4),
        "info_path":   str(info_path),
        "title":       meta.get("title", ""),
        "uploader":    meta.get("uploader", ""),
        "uploader_url": meta.get("uploader_url", ""),
        "duration":    meta.get("duration", 0),
        "license":     meta.get("license", ""),
        "is_creative_commons": "creative commons" in (meta.get("license", "") or "").lower(),
        "original_url": url,
        "downloaded_at": datetime.now().isoformat(),
    }


def fetch_subtitles(slug: str) -> str:
    """Read auto-generated subtitles if yt-dlp pulled them."""
    for ext in (".en.vtt", ".en.srt", ".vtt", ".srt"):
        p = DOWNLOAD_DIR / f"{slug}{ext}"
        if p.exists():
            text = p.read_text(errors="ignore")
            # Crude VTT/SRT cleanup: strip timestamps and tags
            lines = []
            for line in text.splitlines():
                line = line.strip()
                if not line or "-->" in line or line.startswith(("WEBVTT", "NOTE", "STYLE")):
                    continue
                if line.isdigit():
                    continue
                import re
                line = re.sub(r"<[^>]+>", "", line)
                lines.append(line)
            return " ".join(lines)
    return ""
