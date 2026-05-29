"""
Studio-grade auto-captioning using Whisper (Descript/CapCut-class).

Uses faster-whisper if available (fast + accurate), else falls back to
yt-dlp auto-subs.
"""
import re
import subprocess
import shutil
from pathlib import Path


def has_whisper() -> bool:
    try:
        import faster_whisper  # noqa
        return True
    except ImportError:
        return False


def extract_audio(video: Path, out_wav: Path) -> bool:
    if not shutil.which("ffmpeg"):
        return False
    r = subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(video),
        "-vn", "-ac", "1", "-ar", "16000",
        "-c:a", "pcm_s16le",
        str(out_wav),
    ], capture_output=True, text=True, timeout=300)
    return r.returncode == 0


def transcribe(video: Path, model_size: str = "base") -> dict:
    """Return word-level timed transcript as captions list."""
    if not has_whisper():
        return {"error": "faster-whisper not installed. pip install faster-whisper"}
    from faster_whisper import WhisperModel
    wav = video.parent / f"{video.stem}.wav"
    if not extract_audio(video, wav):
        return {"error": "audio extract failed"}
    try:
        model = WhisperModel(model_size, device="cpu", compute_type="int8")
        segments, info = model.transcribe(str(wav), word_timestamps=True)
        captions = []
        full_text = []
        for s in segments:
            full_text.append(s.text.strip())
            if s.words:
                # Group words into 3-word chunks
                w = list(s.words)
                for i in range(0, len(w), 3):
                    grp = w[i:i + 3]
                    if not grp:
                        continue
                    captions.append({
                        "start": grp[0].start,
                        "end":   grp[-1].end,
                        "text":  " ".join(x.word.strip() for x in grp).upper().rstrip(".,!?"),
                    })
            else:
                captions.append({
                    "start": s.start, "end": s.end,
                    "text": s.text.strip()[:60].upper().rstrip(".,!?")
                })
        return {
            "captions":  captions,
            "language":  info.language,
            "duration":  info.duration,
            "full_text": " ".join(full_text),
        }
    except Exception as e:
        return {"error": f"whisper failed: {e}"}
    finally:
        try:
            wav.unlink()
        except Exception:
            pass
