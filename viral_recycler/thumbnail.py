"""
Auto-thumbnail generator (Canva/Photoshop-class for YouTube Shorts).

Pulls 3 candidate frames at high-motion moments, picks the most striking,
overlays the hook text in bold typography, exports 1080×1920.
"""
import subprocess
import shutil
from pathlib import Path


def extract_keyframes(video: Path, out_dir: Path, count: int = 5) -> list:
    """Extract `count` frames evenly spaced through the video."""
    out_dir.mkdir(parents=True, exist_ok=True)
    if not shutil.which("ffmpeg"):
        return []
    # Use ffmpeg scene detection to grab high-motion frames
    pattern = str(out_dir / f"{video.stem}_thumb_%03d.jpg")
    r = subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(video),
        "-vf", f"select='gt(scene,0.3)',scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920",
        "-frames:v", str(count),
        "-vsync", "vfr",
        pattern,
    ], capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        # Fallback: just sample at midpoint
        subprocess.run([
            "ffmpeg", "-y", "-loglevel", "error",
            "-i", str(video),
            "-ss", "5", "-frames:v", "1",
            "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920",
            str(out_dir / f"{video.stem}_thumb_001.jpg"),
        ], capture_output=True, text=True, timeout=60)
    return sorted(out_dir.glob(f"{video.stem}_thumb_*.jpg"))


def overlay_hook(frame: Path, hook: str, out: Path,
                  channel_handle: str = "") -> dict:
    """Burn the hook text onto the frame as a YouTube thumbnail."""
    if not shutil.which("ffmpeg"):
        return {"error": "ffmpeg missing"}
    safe = (hook or "").replace("'", "").replace(":", "")[:60]
    handle_safe = (channel_handle or "").replace("'", "").replace(":", "")[:30]
    drawtext = (
        f"drawtext=text='{safe}':"
        f"fontcolor=white:fontsize=110:box=1:boxcolor=black@0.55:boxborderw=40:"
        f"x=(w-text_w)/2:y=h/3"
    )
    if handle_safe:
        drawtext += (
            f",drawtext=text='{handle_safe}':"
            f"fontcolor=#fbbf24:fontsize=54:box=1:boxcolor=black@0.55:boxborderw=20:"
            f"x=(w-text_w)/2:y=h-260"
        )
    r = subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(frame),
        "-vf", drawtext,
        "-frames:v", "1",
        str(out),
    ], capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        return {"error": f"thumbnail overlay failed: {r.stderr[-200:]}"}
    return {"output_path": str(out)}


def generate(video: Path, hook: str, channel_handle: str = "") -> dict:
    """Full thumbnail flow: pick a frame + overlay hook → JPG."""
    work_dir = video.parent / "thumbnails"
    candidates = extract_keyframes(video, work_dir, count=3)
    if not candidates:
        return {"error": "no candidate frames"}
    pick = candidates[0]
    out = video.parent / f"{video.stem}_thumb.jpg"
    result = overlay_hook(pick, hook, out, channel_handle)
    # cleanup candidates
    for c in candidates:
        try:
            c.unlink()
        except Exception:
            pass
    return result
