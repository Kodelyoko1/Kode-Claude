"""
Transcribe — bulk audio/video → .txt + .srt service for creators.
Revenue: $19 single episode, $79/mo (10 hrs), $297 bulk 30-episode pack.

Owner workflow:
  Drop audio or video files into data/tr_inputs/{slug}.{mp3,mp4,wav,m4a,...}
  Agent batch-processes the queue, writes outputs to data/tr_outputs/{slug}.{txt,srt,meta.json}
  Paid subscribers get an email with the deliverable links.

Chain: outputs in data/tr_outputs/ are also picked up by the `shownotes` agent.
"""
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from autonomous import storage, mailer, billing, metrics
from transcribe import health

AGENT_KEY = "transcribe"
INPUTS_DIR = Path(__file__).parent.parent / "data" / "tr_inputs"
OUTPUTS_DIR = Path(__file__).parent.parent / "data" / "tr_outputs"

AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".opus", ".aac"}
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi"}
SUPPORTED = AUDIO_EXTS | VIDEO_EXTS


def _has_whisper() -> bool:
    try:
        import faster_whisper  # noqa
        return True
    except ImportError:
        return False


def _extract_audio_wav(src: Path, out_wav: Path) -> bool:
    if not shutil.which("ffmpeg"):
        return False
    r = subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(src),
        "-vn", "-ac", "1", "-ar", "16000",
        "-c:a", "pcm_s16le",
        str(out_wav),
    ], capture_output=True, text=True, timeout=600)
    return r.returncode == 0


def _fmt_srt_ts(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _segments_to_srt(segments: list) -> str:
    lines = []
    for i, seg in enumerate(segments, 1):
        lines.append(str(i))
        lines.append(f"{_fmt_srt_ts(seg['start'])} --> {_fmt_srt_ts(seg['end'])}")
        lines.append(seg["text"].strip())
        lines.append("")
    return "\n".join(lines)


def transcribe_file(src: Path, model_size: str = "base") -> dict:
    """Return {text, srt, segments, language, duration} or {error}."""
    if not _has_whisper():
        return {"error": "faster-whisper not installed (pip install faster-whisper)"}
    from faster_whisper import WhisperModel

    if src.suffix.lower() in AUDIO_EXTS:
        wav_path = src
        cleanup_wav = False
    else:
        wav_path = src.with_suffix(".tr.wav")
        if not _extract_audio_wav(src, wav_path):
            return {"error": "ffmpeg audio extract failed"}
        cleanup_wav = True

    try:
        model = WhisperModel(model_size, device="cpu", compute_type="int8")
        segments_iter, info = model.transcribe(str(wav_path), beam_size=1)
        segments = []
        full = []
        for s in segments_iter:
            txt = s.text.strip()
            segments.append({"start": s.start, "end": s.end, "text": txt})
            full.append(txt)
        return {
            "text": " ".join(full),
            "srt": _segments_to_srt(segments),
            "segments": segments,
            "language": info.language,
            "duration": info.duration,
        }
    except Exception as e:
        return {"error": f"whisper failed: {e}"}
    finally:
        if cleanup_wav:
            try:
                wav_path.unlink()
            except Exception:
                pass


def _classify_transcribe_error(err: str) -> str:
    """Map transcribe_file's free-text error to a stable outcome bucket."""
    e = (err or "").lower()
    if "faster-whisper not installed" in e:
        return "whisper_missing"
    if "ffmpeg" in e:
        return "ffmpeg_failed"
    return "whisper_failed"


def process_input(src: Path) -> dict:
    """Transcribe one input file and write all three outputs."""
    slug = src.stem
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    txt_path = OUTPUTS_DIR / f"{slug}.txt"
    srt_path = OUTPUTS_DIR / f"{slug}.srt"
    meta_path = OUTPUTS_DIR / f"{slug}.meta.json"

    if txt_path.exists() and srt_path.exists():
        return {"slug": slug, "status": "already_done"}

    result = transcribe_file(src)
    if "error" in result:
        health.record_file(slug, _classify_transcribe_error(result["error"]),
                           detail=result["error"][:120])
        return {"slug": slug, **result}

    txt_path.write_text(result["text"])
    srt_path.write_text(result["srt"])
    meta_path.write_text(json.dumps({
        "slug": slug,
        "source": src.name,
        "language": result["language"],
        "duration_seconds": result["duration"],
        "produced_at": datetime.now().isoformat(),
    }, indent=2))
    health.record_file(slug, "success",
                       duration_seconds=result["duration"],
                       language=result["language"])
    return {"slug": slug, "status": "produced", "duration": result["duration"]}


def build_queue() -> dict:
    """Process any new files dropped into tr_inputs/."""
    INPUTS_DIR.mkdir(parents=True, exist_ok=True)
    produced = 0
    failed = 0
    for src in sorted(INPUTS_DIR.iterdir()):
        if not src.is_file() or src.suffix.lower() not in SUPPORTED:
            continue
        res = process_input(src)
        if res.get("status") == "produced":
            produced += 1
        elif "error" in res:
            failed += 1
    return {"transcripts_produced": produced, "failures": failed}


def _delivery_email(slug: str) -> str:
    return (
        f"Your transcript is ready: {slug}\n\n"
        f"Files:\n"
        f"  data/tr_outputs/{slug}.txt   (plain text)\n"
        f"  data/tr_outputs/{slug}.srt   (subtitles, drop into your editor)\n"
        f"  data/tr_outputs/{slug}.meta.json\n\n"
        f"Want show notes from this transcript? Reply 'shownotes' and we'll route it "
        f"through our show-notes agent (no extra setup needed).\n"
    )


def fulfill_cycle() -> dict:
    """Email active subscribers any outputs produced since their last delivery."""
    subs = storage.load("tr_subscribers.json", [])
    log = storage.load("tr_delivery_log.json", {})
    sent = 0
    for sub in subs:
        if sub.get("status") != "active":
            continue
        email = sub.get("email")
        if not email:
            health.record_delivery("(missing)", "no_email", slugs=0,
                                   detail=f"sub={sub.get('name','?')}")
            continue
        already = set(log.get(email, []))
        new_slugs = []
        for meta in OUTPUTS_DIR.glob("*.meta.json"):
            try:
                m = json.loads(meta.read_text())
            except Exception:
                continue
            if m["slug"] in already:
                continue
            new_slugs.append(m["slug"])
        if not new_slugs:
            continue
        body_parts = [f"Hi {sub.get('name', 'there')},\n"]
        for slug in new_slugs[:10]:
            body_parts.append(_delivery_email(slug))
            body_parts.append("---\n")
        body = "\n".join(body_parts)
        r = mailer.send(AGENT_KEY, email,
                        f"Transcripts ready — {len(new_slugs)} new",
                        body, purpose="fulfillment")
        if r.get("status") == "sent":
            log[email] = list(already | set(new_slugs))
            sent += 1
            health.record_delivery(email, "success", slugs=len(new_slugs))
        else:
            health.record_delivery(email, "mail_failed", slugs=len(new_slugs),
                                   detail=f"mailer={r.get('status','?')}: "
                                          f"{(r.get('reason') or r.get('error',''))[:80]}")
    storage.save("tr_delivery_log.json", log)
    return {"fulfillment_sent": sent}


def acquire_cycle() -> dict:
    """Send free-sample pitch to leads (podcast hosts / video creators)."""
    leads = storage.load("tr_leads.json", [])
    sent = 0
    for lead in leads:
        if lead.get("trial_sent"):
            continue
        email = lead.get("email")
        if not email:
            continue
        body = (
            f"Hi {lead.get('name', 'there')},\n\n"
            f"I run an automated transcription service for podcasters and video creators.\n"
            f"Drop me your next episode and you'll get the .txt + .srt back within 24 hours — free, no signup.\n\n"
            f"Pricing after the trial:\n"
            f"  $19 per episode (one-off)\n"
            f"  $79/mo unlimited up to 10 hours of audio\n"
            f"  $297 for a 30-episode bulk pack\n\n"
            f"Subtitles you can drop straight into Premiere, CapCut, or YouTube.\n\n"
            f"Reply with a link to one episode and I'll send the files back.\n"
        )
        r = mailer.send(AGENT_KEY, email,
                        "Free transcript sample (24h turnaround)",
                        body, purpose="outreach")
        if r.get("status") == "sent":
            lead["trial_sent"] = datetime.now().isoformat()
            sent += 1
    storage.save("tr_leads.json", leads)
    return {"outreach_sent": sent}


def run_full_cycle() -> dict:
    q = build_queue()
    a = acquire_cycle()
    f = fulfill_cycle()
    rev = billing.revenue_summary(AGENT_KEY)
    subs = storage.load("tr_subscribers.json", [])
    metrics.record(
        AGENT_KEY,
        products_produced=q["transcripts_produced"],
        outreach_sent=a["outreach_sent"],
        fulfillment_sent=f["fulfillment_sent"],
        active_subs=sum(1 for s in subs if s.get("status") == "active"),
        mrr=rev["mrr"],
        total_revenue=rev["total_paid"],
    )
    return {**q, **a, **f, **rev}
