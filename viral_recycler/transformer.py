"""
Video transformer — ffmpeg pipeline that converts a downloaded video into
a transformed Short:

  1. Trim to a 25-55 second window (best-segment-from-transcript heuristic)
  2. Force 9:16 vertical (1080×1920) with smart crop/pad
  3. Mirror horizontally (cheap anti-fingerprinting)
  4. Subtle color/speed adjust (less likely to match copyright fingerprint)
  5. Burn-in captions (read from generated SRT)
  6. Add intro branded card (1.2s) + outro branded card (2s)

All transformations use free ffmpeg only. No paid services.
"""
import subprocess
import shutil
from pathlib import Path

WORK_DIR = Path(__file__).parent.parent / "data" / "vr_work"
OUTPUT_DIR = Path(__file__).parent.parent / "data" / "vr_output"


def has_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


def make_srt(captions: list, out_path: Path):
    """captions = [{start: seconds, end: seconds, text: str}, ...]"""
    lines = []
    for i, c in enumerate(captions, 1):
        s = c["start"]
        e = c["end"]
        def fmt(t):
            h = int(t // 3600); m = int((t % 3600) // 60)
            sec = t - h * 3600 - m * 60
            return f"{h:02d}:{m:02d}:{sec:06.3f}".replace(".", ",")
        lines.append(f"{i}\n{fmt(s)} --> {fmt(e)}\n{c['text']}\n")
    out_path.write_text("\n".join(lines))


def build_storyboard_captions(text: str, start_offset: float = 0.0,
                               wps: float = 2.5) -> list:
    """Split text into 3-word caption chunks with timing."""
    words = text.split()
    captions = []
    t = start_offset
    chunk = 3
    for i in range(0, len(words), chunk):
        seg = " ".join(words[i:i + chunk])
        seg_secs = max(0.7, len(seg.split()) / wps)
        captions.append({
            "start": t,
            "end":   t + seg_secs,
            "text":  seg.upper().rstrip(".,!?"),
        })
        t += seg_secs
    return captions


def transform(
    source_video: Path,
    output_slug: str,
    trim_start: float = 0.0,
    trim_duration: float = 45.0,
    caption_text: str = "",
    hook_text: str = "",
    outro_text: str = "Follow for more",
    mirror: bool = False,      # default off — was causing backward text
    speed: float = 1.03,
) -> dict:
    """Run the full transform pipeline; return path to final MP4."""
    if not has_ffmpeg():
        return {"error": "ffmpeg not installed"}
    if not source_video.exists():
        return {"error": f"source missing: {source_video}"}

    WORK_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Trim + force vertical 1080x1920 + optional mirror + speed
    stage1 = WORK_DIR / f"{output_slug}_stage1.mp4"
    vf = (
        "scale=1080:1920:force_original_aspect_ratio=increase:flags=lanczos,"
        "crop=1080:1920,"
        + ("hflip," if mirror else "")
        + "eq=brightness=0.02:saturation=1.05,"
        + f"setpts={1/speed:.4f}*PTS"
    )
    af = f"atempo={speed:.3f}"

    cmd1 = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-ss", str(trim_start),
        "-i", str(source_video),
        "-t", str(trim_duration),
        "-vf", vf,
        "-af", af,
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-c:a", "aac", "-b:a", "128k",
        "-pix_fmt", "yuv420p",
        str(stage1),
    ]
    result = subprocess.run(cmd1, capture_output=True, text=True, timeout=900)
    if result.returncode != 0:
        return {"error": f"stage1 ffmpeg failed: {result.stderr[-400:]}"}

    # 2. Burn-in captions if we have caption text
    stage2 = WORK_DIR / f"{output_slug}_stage2.mp4"
    if caption_text:
        captions = build_storyboard_captions(caption_text, start_offset=1.2)
        srt_path = WORK_DIR / f"{output_slug}.srt"
        make_srt(captions, srt_path)
        # Escape SRT path for ffmpeg filter (forward slashes are fine, but quote it)
        srt_arg = str(srt_path).replace(":", "\\:").replace("'", "\\'")
        sub_vf = (
            f"subtitles='{srt_arg}':"
            f"force_style='FontName=Arial Black,FontSize=22,PrimaryColour=&H00FFFFFF,"
            f"OutlineColour=&H00000000,Outline=3,Alignment=2,MarginV=120,Bold=1'"
        )
        cmd2 = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-i", str(stage1),
            "-vf", sub_vf,
            "-c:v", "libx264", "-preset", "medium", "-crf", "20",
            "-c:a", "copy",
            str(stage2),
        ]
        result = subprocess.run(cmd2, capture_output=True, text=True, timeout=900)
        if result.returncode != 0:
            # Caption failure is non-fatal — fall through with stage1
            stage2 = stage1
    else:
        stage2 = stage1

    # 3. Hook intro card (1.2s text overlay at start) + outro card
    final = OUTPUT_DIR / f"{output_slug}.mp4"
    hook_safe = (hook_text or "").replace("'", "")[:60]
    outro_safe = (outro_text or "").replace("'", "")[:40]
    drawtext = (
        f"drawtext=text='{hook_safe}':"
        f"fontcolor=white:fontsize=64:box=1:boxcolor=black@0.6:boxborderw=24:"
        f"x=(w-text_w)/2:y=h/3:enable='between(t,0,1.2)',"
        f"drawtext=text='{outro_safe}':"
        f"fontcolor=white:fontsize=54:box=1:boxcolor=black@0.6:boxborderw=20:"
        f"x=(w-text_w)/2:y=h-300:enable='gte(t,{trim_duration - 2})'"
    )
    cmd3 = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(stage2),
        "-vf", drawtext,
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-c:a", "copy",
        str(final),
    ]
    result = subprocess.run(cmd3, capture_output=True, text=True, timeout=900)
    if result.returncode != 0:
        # If drawtext fails, ship stage2 as final
        shutil.copy(stage2, final)

    # Cleanup intermediates
    for p in (stage1, stage2):
        try:
            if p.exists() and p != final:
                p.unlink()
        except Exception:
            pass

    return {"output_path": str(final), "size_bytes": final.stat().st_size}
