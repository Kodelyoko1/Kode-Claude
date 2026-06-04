"""
CarouselForge health: per-carousel outcomes + Pillow/font probes.

Two input sources (deduped by slug, with cr_inputs winning when both exist):
  · data/cr_inputs/<slug>.json   — owner-dropped manifests
  · data/sn_outputs/<slug>.md    — auto-ingest from ShowNotes Key Takeaways

Silent failure modes:
  · Pillow missing → module-level import in tools.py would error on
    import; the whole agent is a hard no-op
  · All bundled font candidates absent → falls back to Pillow's tiny
    default font; output is rendered but looks broken
  · Spec malformed JSON in cr_inputs/ → build_queue counts as failure
    but no detail surfaces
  · Spec has no slides → "no slides provided" error; same gap
  · monthly_99 advertises 4/mo with no enforcement
  · sn_outputs/<slug>.carousel.skip files accumulating silently means
    auto-ingest stops working for those shownotes
  · mail_failed silent retry-loop
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

DATA_DIR     = Path(__file__).parent.parent / "data"
INPUTS_DIR   = DATA_DIR / "cr_inputs"
OUTPUTS_DIR  = DATA_DIR / "cr_outputs"
SN_OUTPUTS   = DATA_DIR / "sn_outputs"
FILE_LOG     = DATA_DIR / "cr_carousel_log.json"
DELIVERY_LOG = DATA_DIR / "cr_delivery_outcomes.json"

LOG_MAX = int(os.environ.get("CR_LOG_MAX", "300"))

VALID_OUTCOMES = {"success", "spec_invalid", "no_slides", "build_failed"}


def _now() -> str:
    return datetime.now().isoformat()


def _load(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return default


def _save(path: Path, data) -> None:
    path.parent.mkdir(exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except Exception:
        try: os.unlink(tmp)
        except OSError: pass
        raise


def _append_capped(path: Path, entry: dict) -> None:
    log = _load(path, [])
    if not isinstance(log, list):
        log = []
    log.append(entry)
    if len(log) > LOG_MAX:
        log = log[-LOG_MAX:]
    _save(path, log)


def _month_key(ts: str = "") -> str:
    return ts[:7] if ts else datetime.now().strftime("%Y-%m")


# ─────────────────────────── Carousel outcomes ───────────────────────────

def record_carousel(slug: str, outcome: str, source: str = "",
                    platform: str = "", slide_count: int = 0, detail: str = "") -> None:
    if not slug:
        return
    _append_capped(FILE_LOG, {
        "ts": _now(), "slug": slug, "outcome": outcome,
        "source": source or "", "platform": platform or "",
        "slide_count": int(slide_count), "detail": detail or "",
    })


def recent_carousels(limit: int = 50) -> list[dict]:
    log = _load(FILE_LOG, [])
    return log[-limit:][::-1] if isinstance(log, list) else []


def carousel_outcome_summary() -> dict:
    log = _load(FILE_LOG, [])
    if not isinstance(log, list) or not log:
        return {"total": 0, **{oc: 0 for oc in VALID_OUTCOMES},
                "by_source": {}, "by_platform": {}}
    counts = {oc: 0 for oc in VALID_OUTCOMES}
    by_source: dict[str, int] = {}
    by_platform: dict[str, int] = {}
    for r in log:
        oc = r.get("outcome", "")
        if oc in counts:
            counts[oc] += 1
        s = r.get("source", "?")
        by_source[s] = by_source.get(s, 0) + 1
        if oc == "success":
            p = r.get("platform", "?")
            by_platform[p] = by_platform.get(p, 0) + 1
    return {"total": len(log), **counts,
            "by_source": by_source, "by_platform": by_platform}


# ─────────────────────────── Delivery outcomes ───────────────────────────

def record_delivery(email: str, outcome: str, slugs: int = 0, detail: str = "") -> None:
    if not email:
        return
    _append_capped(DELIVERY_LOG, {
        "ts": _now(), "email": email.lower(), "outcome": outcome,
        "slugs": int(slugs), "detail": detail or "",
    })


def stuck_mail_failed(min_attempts: int = 3) -> list[dict]:
    log = _load(DELIVERY_LOG, [])
    if not isinstance(log, list):
        return []
    by_email: dict[str, dict] = {}
    for r in log:
        if r.get("outcome") != "mail_failed":
            continue
        e = r.get("email", "")
        if not e:
            continue
        rec = by_email.setdefault(e, {"attempts": 0, "last_ts": "", "last_detail": ""})
        rec["attempts"] += 1
        rec["last_ts"] = r.get("ts", "")
        rec["last_detail"] = r.get("detail", "")
    return sorted(
        [{"email": e, **rec} for e, rec in by_email.items() if rec["attempts"] >= min_attempts],
        key=lambda r: -r["attempts"],
    )


def monthly_deliveries_per_email(month: str = "") -> dict[str, int]:
    month = month or _month_key()
    log = _load(DELIVERY_LOG, [])
    if not isinstance(log, list):
        return {}
    out: dict[str, int] = {}
    for r in log:
        if r.get("outcome") != "success":
            continue
        if _month_key(r.get("ts", "")) != month:
            continue
        e = r.get("email", "")
        if not e:
            continue
        out[e] = out.get(e, 0) + max(int(r.get("slugs", 1)), 1)
    return out


# ─────────────────────────── Probes ───────────────────────────

def probe_pillow() -> dict:
    try:
        import PIL
        return {"ok": True, "version": PIL.__version__}
    except ImportError as e:
        return {"ok": False, "error": f"Pillow not importable: {e}"}


def probe_fonts() -> dict:
    """Check whether at least one bundled font candidate resolves on disk."""
    from carouselforge.tools import FONT_CANDIDATES_BOLD, FONT_CANDIDATES_REG
    bold_found = [p for p in FONT_CANDIDATES_BOLD if Path(p).exists()]
    reg_found  = [p for p in FONT_CANDIDATES_REG if Path(p).exists()]
    return {
        "ok":            bool(bold_found) and bool(reg_found),
        "bold_found":    bold_found,
        "bold_missing":  [p for p in FONT_CANDIDATES_BOLD if not Path(p).exists()],
        "regular_found": reg_found,
        "regular_missing": [p for p in FONT_CANDIDATES_REG if not Path(p).exists()],
    }


def probe_inputs() -> dict:
    """Triangulate both sources + ShowNotes skip-marker count.

    Returns {
      "ok": bool,
      "cr_inputs":       N,
      "sn_outputs":      N,
      "sn_skip_markers": N,
      "cr_outputs":      N,
      "candidates":      N  — unique-by-slug across both sources (post-skip),
    }
    """
    cr_files = []
    if INPUTS_DIR.exists():
        cr_files = [f for f in INPUTS_DIR.glob("*.json") if f.is_file()]
    sn_files = []
    sn_skips = []
    if SN_OUTPUTS.exists():
        sn_files = [f for f in SN_OUTPUTS.glob("*.md") if f.is_file()]
        sn_skips = [f for f in SN_OUTPUTS.glob("*.carousel.skip") if f.is_file()]
    cr_outputs = 0
    if OUTPUTS_DIR.exists():
        cr_outputs = sum(1 for d in OUTPUTS_DIR.iterdir() if d.is_dir())
    skip_slugs = {f.name.replace(".carousel.skip", "") for f in sn_skips}
    candidates = {f.stem for f in cr_files} | (
        {f.stem for f in sn_files} - skip_slugs
    )
    return {
        "ok":              (len(cr_files) + len(sn_files)) > 0,
        "cr_inputs":       len(cr_files),
        "sn_outputs":      len(sn_files),
        "sn_skip_markers": len(sn_skips),
        "cr_outputs":      cr_outputs,
        "candidates":      len(candidates),
    }


def _cli():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--probe",   action="store_true")
    p.add_argument("--pillow",  action="store_true")
    p.add_argument("--fonts",   action="store_true")
    p.add_argument("--files",   type=int, default=0)
    p.add_argument("--usage",   action="store_true")
    args = p.parse_args()
    if args.probe:
        print(json.dumps(probe_inputs(), indent=2)); return
    if args.pillow:
        print(json.dumps(probe_pillow(), indent=2)); return
    if args.fonts:
        print(json.dumps(probe_fonts(), indent=2)); return
    if args.files:
        for r in recent_carousels(args.files):
            print(f"  {r['ts'][:19]}  {r['outcome']:<14s}  src={r['source']:<11s}  {r['slug']}")
        s = carousel_outcome_summary()
        print(f"\n  total={s['total']}  success={s['success']}  spec_invalid={s['spec_invalid']}  "
              f"no_slides={s['no_slides']}  build_failed={s['build_failed']}")
        return
    if args.usage:
        u = monthly_deliveries_per_email()
        for e, n in sorted(u.items(), key=lambda kv: -kv[1]):
            print(f"  {e}  {n}")
        return


if __name__ == "__main__":
    _cli()
