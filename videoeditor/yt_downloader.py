"""
YouTube downloader for VideoEditor using yt-dlp.
Downloads the best available quality up to 1080p.
"""

import subprocess
from pathlib import Path

VE_INPUTS = Path("data/ve_inputs")


def download_youtube(url: str, slug: str | None = None) -> dict:
    """
    Download a YouTube video into data/ve_inputs/.
    Returns {"path": ..., "slug": ..., "title": ...} or {"error": ...}
    """
    VE_INPUTS.mkdir(parents=True, exist_ok=True)

    # Get video info first (title/id for slug)
    info_cmd = [
        "python3", "-m", "yt_dlp",
        "--print", "%(id)s\t%(title)s",
        "--no-playlist",
        url,
    ]
    r = subprocess.run(info_cmd, capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        return {"error": f"yt-dlp info failed: {r.stderr[:300]}"}

    line = r.stdout.strip()
    if "\t" in line:
        vid_id, title = line.split("\t", 1)
    else:
        vid_id, title = line, line

    # Use provided slug or sanitise the title
    if not slug:
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in title)
        slug = safe[:60].strip("_") or vid_id

    out_path = VE_INPUTS / f"{slug}.mp4"

    # Download best video+audio up to 1080p, mux to mp4
    dl_cmd = [
        "python3", "-m", "yt_dlp",
        "-f", "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080][ext=mp4]/best",
        "--merge-output-format", "mp4",
        "--no-playlist",
        "-o", str(out_path),
        url,
    ]
    r = subprocess.run(dl_cmd, capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        return {"error": f"yt-dlp download failed: {r.stderr[:500]}"}

    if not out_path.exists():
        # yt-dlp may have used a different extension
        candidates = list(VE_INPUTS.glob(f"{slug}.*"))
        if candidates:
            out_path = candidates[0]
        else:
            return {"error": "Downloaded file not found"}

    return {"path": str(out_path), "slug": slug, "title": title, "video_id": vid_id}
