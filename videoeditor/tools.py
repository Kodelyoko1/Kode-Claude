"""
VideoEditor — autonomous video polish + reels cutter.

Pipeline per video:
  1. ffprobe → duration, resolution, audio presence
  2. ffmpeg → polished master (color grade + audio cleanup + silence trim)
  3. Audio energy scan → find the best 30 s and 60 s windows
  4. ffmpeg → vertical 9:16 reels with fade in/out (30 s + 60 s)
  5. Meta JSON written to data/ve_outputs/{slug}/

Input:  data/ve_inputs/{slug}.{mp4,mov,avi,mkv,webm,m4v}
Output: data/ve_outputs/{slug}/{slug}_master.mp4
                            {slug}_30s_reel.mp4
                            {slug}_60s_reel.mp4
                            {slug}_meta.json
"""

import json
import math
import shutil
import struct
import subprocess
import time
import wave
from datetime import datetime
from pathlib import Path

DATA = Path("data")
VE_INPUTS = DATA / "ve_inputs"
VE_OUTPUTS = DATA / "ve_outputs"
VE_PROCESSED = DATA / "ve_processed"
VE_FAILED = DATA / "ve_failed"

SUPPORTED_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"}

# ── ffmpeg filter constants ──────────────────────────────────────────────────

# Video: subtle brightness lift, mild contrast + saturation boost, light sharpen
_VF_GRADE = (
    "eq=brightness=0.04:contrast=1.08:saturation=1.15,"
    "unsharp=5:5:0.6:3:3:0"
)

# Audio: remove rumble, denoise, gentle compress, EBU R128 loudnorm
_AF_CLEAN = (
    "highpass=f=80,"
    "afftdn=nf=-25,"
    "acompressor=threshold=-18dB:ratio=3:attack=5:release=50,"
    "loudnorm=I=-16:TP=-1.5:LRA=11"
)

# Vertical reel scale + letterbox to 1080×1920
_VF_VERTICAL = (
    "scale=1080:1920:force_original_aspect_ratio=decrease,"
    "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=black"
)


def ensure_dirs() -> None:
    for d in [VE_INPUTS, VE_OUTPUTS, VE_PROCESSED, VE_FAILED]:
        d.mkdir(parents=True, exist_ok=True)


def check_ffmpeg() -> None:
    """Raise RuntimeError if ffmpeg / ffprobe are not on PATH."""
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            raise RuntimeError(
                f"'{tool}' not found on PATH.\n"
                "Install it with:  sudo apt install ffmpeg   (Debian/Ubuntu)\n"
                "                  brew install ffmpeg       (macOS)"
            )


# ── probe ────────────────────────────────────────────────────────────────────

def _probe(src: Path) -> dict:
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_streams", "-show_format",
        str(src),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {r.stderr[:500]}")
    return json.loads(r.stdout)


def _duration(info: dict) -> float:
    return float(info["format"].get("duration", 0))


def _video_stream(info: dict) -> dict | None:
    for s in info.get("streams", []):
        if s.get("codec_type") == "video":
            return s
    return None


def _audio_stream(info: dict) -> dict | None:
    for s in info.get("streams", []):
        if s.get("codec_type") == "audio":
            return s
    return None


# ── audio energy analysis ────────────────────────────────────────────────────

def _extract_wav(src: Path, out: Path) -> None:
    """Mono 16 kHz WAV for energy analysis (fast, small)."""
    cmd = [
        "ffmpeg", "-y", "-i", str(src),
        "-vn", "-ac", "1", "-ar", "16000",
        "-f", "wav", str(out),
    ]
    r = subprocess.run(cmd, capture_output=True, timeout=300)
    if r.returncode != 0:
        raise RuntimeError(f"Audio extract failed: {r.stderr.decode()[-500:]}")


def _rms_chunks(wav_path: Path, chunk_sec: float = 1.0) -> list[float]:
    """Return list of per-chunk RMS values (one per chunk_sec of audio)."""
    with wave.open(str(wav_path), "rb") as wf:
        rate = wf.getframerate()
        sampwidth = wf.getsampwidth()
        n_frames = wf.getnframes()
        raw = wf.readframes(n_frames)

    if sampwidth == 2:
        samples = list(struct.unpack(f"<{n_frames}h", raw))
    else:
        samples = [b - 128 for b in raw]

    chunk = int(rate * chunk_sec)
    rms_list: list[float] = []
    for i in range(0, len(samples), chunk):
        window = samples[i : i + chunk]
        if not window:
            break
        rms = math.sqrt(sum(s * s for s in window) / len(window))
        rms_list.append(rms)
    return rms_list


def _best_start(rms_chunks: list[float], clip_sec: int, chunk_sec: float = 1.0) -> float:
    """Sliding-window sum over rms_chunks → return start time of best window."""
    n = int(clip_sec / chunk_sec)
    if len(rms_chunks) <= n:
        return 0.0

    window_sum = sum(rms_chunks[:n])
    best_sum = window_sum
    best_idx = 0

    for i in range(1, len(rms_chunks) - n + 1):
        window_sum += rms_chunks[i + n - 1] - rms_chunks[i - 1]
        if window_sum > best_sum:
            best_sum = window_sum
            best_idx = i

    return best_idx * chunk_sec


# ── ffmpeg encode ─────────────────────────────────────────────────────────────

def _encode_master(src: Path, out: Path, has_audio: bool) -> None:
    cmd = ["ffmpeg", "-y", "-i", str(src)]
    if has_audio:
        cmd += [
            "-vf", _VF_GRADE,
            "-af", _AF_CLEAN,
            "-c:v", "libx264", "-preset", "slow", "-crf", "18",
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart",
        ]
    else:
        cmd += [
            "-vf", _VF_GRADE,
            "-an",
            "-c:v", "libx264", "-preset", "slow", "-crf", "18",
            "-movflags", "+faststart",
        ]
    cmd.append(str(out))
    r = subprocess.run(cmd, capture_output=True, timeout=7200)
    if r.returncode != 0:
        raise RuntimeError(f"Master encode failed:\n{r.stderr.decode()[-1500:]}")


def _encode_reel(src: Path, out: Path, start: float, duration: int, has_audio: bool) -> None:
    fade_out_st = max(duration - 0.5, 0)
    vf = (
        f"{_VF_GRADE},"
        f"{_VF_VERTICAL},"
        f"fade=t=in:st=0:d=0.5,"
        f"fade=t=out:st={fade_out_st}:d=0.5"
    )
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(round(start, 3)),
        "-i", str(src),
        "-t", str(duration),
    ]
    if has_audio:
        af = (
            f"{_AF_CLEAN},"
            f"afade=t=in:st=0:d=0.5,"
            f"afade=t=out:st={fade_out_st}:d=0.5"
        )
        cmd += [
            "-vf", vf, "-af", af,
            "-c:v", "libx264", "-preset", "slow", "-crf", "20",
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart",
        ]
    else:
        cmd += [
            "-vf", vf, "-an",
            "-c:v", "libx264", "-preset", "slow", "-crf", "20",
            "-movflags", "+faststart",
        ]
    cmd.append(str(out))
    r = subprocess.run(cmd, capture_output=True, timeout=3600)
    if r.returncode != 0:
        raise RuntimeError(f"Reel encode failed:\n{r.stderr.decode()[-1500:]}")


# ── main pipeline ─────────────────────────────────────────────────────────────

def process_video(src: Path) -> dict:
    """Run the full pipeline for a single video file. Returns metadata dict."""
    slug = src.stem
    out_dir = VE_OUTPUTS / slug
    out_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.time()

    # 1. Probe
    info = _probe(src)
    duration = _duration(info)
    vs = _video_stream(info)
    has_audio = _audio_stream(info) is not None
    width = int(vs.get("width", 1920)) if vs else 1920
    height = int(vs.get("height", 1080)) if vs else 1080

    # 2. Master
    master = out_dir / f"{slug}_master.mp4"
    _encode_master(src, master, has_audio)

    # 3. Best-segment detection
    start_30 = 0.0
    start_60 = 0.0
    wav_tmp = out_dir / "_audio_analysis.wav"

    if has_audio and duration > 5:
        _extract_wav(src, wav_tmp)
        chunks = _rms_chunks(wav_tmp)
        if duration >= 30:
            start_30 = _best_start(chunks, 30)
        if duration >= 60:
            start_60 = _best_start(chunks, 60)
        wav_tmp.unlink(missing_ok=True)

    # 4. Reels  (encode from master so audio is already clean)
    reels: list[dict] = []

    if duration >= 30:
        reel_30 = out_dir / f"{slug}_30s_reel.mp4"
        _encode_reel(master, reel_30, start_30, 30, has_audio)
        reels.append({"file": str(reel_30), "duration_s": 30, "source_start_s": round(start_30, 2)})

    if duration >= 60:
        reel_60 = out_dir / f"{slug}_60s_reel.mp4"
        _encode_reel(master, reel_60, start_60, 60, has_audio)
        reels.append({"file": str(reel_60), "duration_s": 60, "source_start_s": round(start_60, 2)})

    meta = {
        "slug": slug,
        "source": str(src),
        "processed_at": datetime.utcnow().isoformat() + "Z",
        "source_duration_s": round(duration, 2),
        "source_resolution": f"{width}x{height}",
        "has_audio": has_audio,
        "master": str(master),
        "reels": reels,
        "processing_time_s": round(time.time() - t0, 1),
    }
    (out_dir / f"{slug}_meta.json").write_text(json.dumps(meta, indent=2))
    return meta


def run_full_cycle(input_path: str | None = None) -> dict:
    """
    Scan data/ve_inputs/ for pending videos (or process a single explicit path)
    and run the full pipeline on each one.
    """
    ensure_dirs()

    check_ffmpeg()

    if input_path:
        pending = [Path(input_path)]
    else:
        pending = sorted(
            f for f in VE_INPUTS.iterdir()
            if f.is_file() and f.suffix.lower() in SUPPORTED_EXTS
        )

    if not pending:
        return {"processed": 0, "errors": 0, "results": []}

    processed = 0
    errors = 0
    results: list[dict] = []

    for src in pending:
        try:
            meta = process_video(src)
            dest = VE_PROCESSED / src.name
            shutil.move(str(src), str(dest))
            results.append(meta)
            processed += 1
        except Exception as exc:
            dest = VE_FAILED / src.name
            try:
                shutil.move(str(src), str(dest))
            except Exception:
                pass
            errors += 1
            print(f"[VideoEditor] FAILED {src.name}: {exc}")

    return {"processed": processed, "errors": errors, "results": results}
