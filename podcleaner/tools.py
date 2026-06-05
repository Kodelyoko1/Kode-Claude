"""
PodCleaner — autonomous podcast audio editor.
Revenue: $9/episode, $49/mo (10 episodes), $199 bulk 30-pack.

Owner workflow:
  Drop raw audio into data/pd_inputs/{slug}.{mp3,wav,m4a,flac}.
  Agent runs an ffmpeg pipeline:
    1. silenceremove   — trim silent runs to 250ms max
    2. loudnorm        — EBU R128 normalization to -16 LUFS (podcast standard)
    3. acompressor     — gentle 2:1 compression to even out level swings
    4. highpass=80hz   — kill rumble + AC hum below speech range
  Output: data/pd_outputs/{slug}.mp3 (192kbps) + {slug}.meta.json with
  duration deltas (raw vs cleaned).

Chain:
  - If a transcript exists at data/tr_outputs/{slug}.txt (from Transcribe),
    a stats line about words-per-minute is added to the meta.
"""
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from autonomous import storage, mailer, billing, metrics
from podcleaner import health

AGENT_KEY = "podcleaner"
INPUTS_DIR = Path(__file__).parent.parent / "data" / "pd_inputs"
OUTPUTS_DIR = Path(__file__).parent.parent / "data" / "pd_outputs"
TR_OUTPUTS_DIR = Path(__file__).parent.parent / "data" / "tr_outputs"

SUPPORTED = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".opus", ".aac"}

# Order matters. silenceremove first so loudnorm/compressor work on speech only.
FILTER_CHAIN = (
    "silenceremove="
    "stop_periods=-1:stop_duration=0.25:stop_threshold=-40dB:"
    "start_periods=1:start_duration=0.15:start_threshold=-40dB,"
    "highpass=f=80,"
    "acompressor=threshold=-18dB:ratio=2:attack=20:release=200,"
    "loudnorm=I=-16:LRA=11:TP=-1.5"
)


def _duration(audio_path: Path) -> float:
    if not shutil.which("ffprobe"):
        return 0.0
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(audio_path)],
            capture_output=True, text=True, timeout=30,
        )
        return float(r.stdout.strip() or 0)
    except Exception:
        return 0.0


def clean_audio(src: Path) -> dict:
    """Run the ffmpeg cleanup chain and produce data/pd_outputs/{slug}.mp3."""
    if not shutil.which("ffmpeg"):
        return {"error": "ffmpeg not installed"}
    slug = src.stem
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUTS_DIR / f"{slug}.mp3"
    if out_path.exists():
        return {"slug": slug, "status": "already_done", "out": str(out_path)}

    raw_dur = _duration(src)
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(src),
        "-af", FILTER_CHAIN,
        "-c:a", "libmp3lame", "-b:a", "192k", "-ar", "44100",
        str(out_path),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    if r.returncode != 0:
        return {"slug": slug, "error": f"ffmpeg failed: {r.stderr[:300]}"}

    clean_dur = _duration(out_path)
    saved = max(0.0, raw_dur - clean_dur)
    pct = (saved / raw_dur * 100) if raw_dur else 0.0

    meta = {
        "slug": slug,
        "source": src.name,
        "raw_duration_s": round(raw_dur, 2),
        "clean_duration_s": round(clean_dur, 2),
        "removed_s": round(saved, 2),
        "removed_pct": round(pct, 1),
        "cleaned_at": datetime.now().isoformat(),
    }

    tr_path = TR_OUTPUTS_DIR / f"{slug}.txt"
    if tr_path.exists() and clean_dur > 0:
        word_count = len(tr_path.read_text(errors="ignore").split())
        meta["transcript_words"] = word_count
        meta["wpm"] = round(word_count / (clean_dur / 60), 1)

    (OUTPUTS_DIR / f"{slug}.meta.json").write_text(json.dumps(meta, indent=2))
    return {"slug": slug, "status": "produced", **meta}


def build_queue() -> dict:
    INPUTS_DIR.mkdir(parents=True, exist_ok=True)
    produced = 0
    failed = 0
    total_removed_s = 0.0
    for src in sorted(INPUTS_DIR.iterdir()):
        if not src.is_file() or src.suffix.lower() not in SUPPORTED:
            continue
        r = clean_audio(src)
        if r.get("status") == "produced":
            produced += 1
            total_removed_s += r.get("removed_s", 0)
        elif "error" in r:
            failed += 1
    return {
        "episodes_cleaned": produced,
        "failures": failed,
        "total_silence_removed_s": round(total_removed_s, 1),
    }


def fulfill_cycle() -> dict:
    subs = storage.load("pd_subscribers.json", [])
    log = storage.load("pd_delivery_log.json", {})
    sent = 0
    for sub in subs:
        if sub.get("status") != "active":
            continue
        email = sub.get("email")
        if not email:
            continue
        already = set(log.get(email, []))
        new = [p for p in OUTPUTS_DIR.glob("*.mp3") if p.name not in already]
        if not new:
            continue
        body_parts = [f"Hi {sub.get('name', 'there')},\n",
                      f"{len(new)} new cleaned episode(s) ready:\n"]
        for p in new[:10]:
            meta_path = p.with_suffix(".meta.json")
            try:
                m = json.loads(meta_path.read_text())
                body_parts.append(
                    f"  {p.name}  ({m['raw_duration_s']}s → {m['clean_duration_s']}s, "
                    f"-{m['removed_s']}s / {m['removed_pct']}%)")
            except Exception:
                body_parts.append(f"  {p.name}")
        body = "\n".join(body_parts) + "\n"
        r = mailer.send(AGENT_KEY, email,
                        f"Cleaned episodes — {len(new)} new",
                        body, purpose="fulfillment")
        if r.get("status") == "sent":
            log[email] = list(already | {p.name for p in new})
            sent += 1
    storage.save("pd_delivery_log.json", log)
    return {"fulfillment_sent": sent}


def acquire_cycle() -> dict:
    leads = storage.load("pd_leads.json", [])
    sent = 0
    for lead in leads:
        if lead.get("trial_sent"):
            continue
        email = lead.get("email")
        if not email:
            continue
        body = (
            f"Hi {lead.get('name', 'there')},\n\n"
            f"I run an automated podcast audio cleanup service. Drop your raw episode "
            f"and you get back a podcast-ready master: silence trimmed, levels normalized "
            f"to -16 LUFS, rumble killed, gentle compression. Free first episode.\n\n"
            f"Pricing after the trial:\n"
            f"  $9 per episode\n"
            f"  $49/mo for 10 episodes\n"
            f"  $199 bulk 30-pack\n\n"
            f"Reply with a link to one episode and I'll send the cleaned master back.\n"
        )
        r = mailer.send(AGENT_KEY, email,
                        "Free podcast cleanup (-16 LUFS master)",
                        body, purpose="outreach")
        if r.get("status") == "sent":
            lead["trial_sent"] = datetime.now().isoformat()
            sent += 1
    storage.save("pd_leads.json", leads)
    return {"outreach_sent": sent}


def run_full_cycle() -> dict:
    q = build_queue()
    a = acquire_cycle()
    f = fulfill_cycle()
    rev = billing.revenue_summary(AGENT_KEY)
    subs = storage.load("pd_subscribers.json", [])
    metrics.record(
        AGENT_KEY,
        products_produced=q["episodes_cleaned"],
        outreach_sent=a["outreach_sent"],
        fulfillment_sent=f["fulfillment_sent"],
        active_subs=sum(1 for s in subs if s.get("status") == "active"),
        mrr=rev["mrr"],
        total_revenue=rev["total_paid"],
    )
    return {**q, **a, **f, **rev}
