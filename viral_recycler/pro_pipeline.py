"""
Pro pipeline — the full Adobe-class production flow.

Stages (each optional, controlled by `quality_tier`):
  basic:   trim → vertical → mirror → captions → hook overlay
  pro:     basic + whisper transcription + color grade + audio master + thumbnail
  studio:  pro + stabilization + speed ramp + vignette/grain + 3 A/B variants
           + multi-aspect export (9:16, 1:1, 16:9)
"""
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from viral_recycler import transformer, effects, audio, thumbnail, transcribe, variants
from shortsforge.tools import find_best_segment, generate_hook, seo_pack, get_channel_config

WORK_DIR = Path(__file__).parent.parent / "data" / "vr_work"
OUTPUT_DIR = Path(__file__).parent.parent / "data" / "vr_output"


def run_pipeline(
    source_video: Path,
    output_slug: str,
    niche: str = "motivational",
    quality_tier: str = "pro",
    color_preset: str = "",
    target_duration: float = 45.0,
    fallback_transcript: str = "",
    mirror: bool = False,
) -> dict:
    """Full pipeline. Returns dict with output paths + metadata for upload."""
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    artifacts = {"stages": [], "errors": []}

    # 1. Transcribe (Whisper > fallback)
    if quality_tier in ("pro", "studio"):
        tx_result = transcribe.transcribe(source_video)
        if "error" in tx_result:
            transcript_text = fallback_transcript
            artifacts["stages"].append({"stage": "transcribe", "method": "fallback",
                                         "note": tx_result["error"]})
        else:
            transcript_text = tx_result["full_text"]
            artifacts["stages"].append({"stage": "transcribe", "method": "whisper",
                                         "lang": tx_result.get("language"),
                                         "captions": len(tx_result["captions"])})
    else:
        transcript_text = fallback_transcript
        artifacts["stages"].append({"stage": "transcribe", "method": "skipped"})

    if not transcript_text:
        transcript_text = source_video.stem.replace("-", " ").replace("_", " ")

    # 2. Pick best segment + generate hook
    segment = find_best_segment(transcript_text, target_seconds=int(target_duration))
    hook = generate_hook(segment["text"], niche)
    seo = seo_pack(segment["text"], niche, hook)
    cfg = get_channel_config()
    artifacts["stages"].append({"stage": "segment", "duration": segment["estimated_seconds"]})

    # 3. Core transform (trim + vertical + mirror + captions + intro/outro)
    stage_a = WORK_DIR / f"{output_slug}_core.mp4"
    core = transformer.transform(
        source_video=source_video,
        output_slug=f"{output_slug}_core",
        trim_start=segment["start_word"] / 2.5,
        trim_duration=segment["estimated_seconds"],
        caption_text=segment["text"],
        hook_text=hook,
        outro_text=f"Follow {cfg['channel_handle']}",
        mirror=mirror,
    )
    if "error" in core:
        artifacts["errors"].append({"stage": "core", **core})
        return artifacts
    core_path = Path(core["output_path"])

    current = core_path

    # 4. Pro tier — color grade + audio master + thumbnail
    if quality_tier in ("pro", "studio"):
        # Color grade
        graded = WORK_DIR / f"{output_slug}_graded.mp4"
        preset = color_preset or {"motivational": "cinematic",
                                   "comedy": "vivid",
                                   "wellness": "teal_orange"}.get(niche, "cinematic")
        cg = effects.color_grade(current, graded, preset=preset)
        if "error" not in cg:
            current = graded
            artifacts["stages"].append({"stage": "color_grade", "preset": preset})
        else:
            artifacts["errors"].append({"stage": "color_grade", **cg})

        # Audio master
        mastered = WORK_DIR / f"{output_slug}_mastered.mp4"
        am = audio.master_audio(current, mastered)
        if "error" not in am:
            current = mastered
            artifacts["stages"].append({"stage": "audio_master", "lufs": -16})
        else:
            artifacts["errors"].append({"stage": "audio_master", **am})

        # Thumbnail
        thumb_result = thumbnail.generate(current, hook, cfg["channel_handle"])
        if "error" not in thumb_result:
            artifacts["thumbnail_path"] = thumb_result["output_path"]
            artifacts["stages"].append({"stage": "thumbnail"})
        else:
            artifacts["errors"].append({"stage": "thumbnail", **thumb_result})

    # 5. Studio tier — stabilization + vignette/grain + variants + multi-aspect
    if quality_tier == "studio":
        # Stabilization
        stabilized = WORK_DIR / f"{output_slug}_stab.mp4"
        st = effects.stabilize(current, stabilized)
        if "error" not in st:
            current = stabilized
            artifacts["stages"].append({"stage": "stabilize"})
        else:
            artifacts["errors"].append({"stage": "stabilize", **st})

        # Cinematic vignette + grain
        vg_out = WORK_DIR / f"{output_slug}_cinematic.mp4"
        vg = effects.vignette_grain(current, vg_out)
        if "error" not in vg:
            current = vg_out
            artifacts["stages"].append({"stage": "vignette_grain"})

        # Multi-aspect exports
        artifacts["aspects"] = {}
        for asp in ("9:16", "1:1", "16:9"):
            asp_out = OUTPUT_DIR / f"{output_slug}_{asp.replace(':', 'x')}.mp4"
            er = effects.export_aspect(current, asp_out, aspect=asp)
            if "error" not in er:
                artifacts["aspects"][asp] = str(asp_out)

        # A/B/C variants metadata
        vlist = variants.make_variants(segment["text"], niche, count=3)
        artifacts["variants"] = vlist
        artifacts["stages"].append({"stage": "variants", "count": len(vlist)})

    # 6. Finalize primary 9:16
    final = OUTPUT_DIR / f"{output_slug}.mp4"
    shutil.copy(current, final)
    artifacts["final_path"] = str(final)
    artifacts["hook"] = hook
    artifacts["title"] = seo["title"]
    artifacts["description"] = seo["description"]
    artifacts["hashtags"] = seo["hashtags"]
    artifacts["niche"] = niche
    artifacts["quality_tier"] = quality_tier

    # Cleanup work files
    for p in WORK_DIR.glob(f"{output_slug}_*.mp4"):
        try:
            p.unlink()
        except Exception:
            pass

    return artifacts
