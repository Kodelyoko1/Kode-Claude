"""ShortsForge health: per-segment briefs + Substack digest cadence."""
from __future__ import annotations
import json, os, tempfile
from datetime import datetime
from pathlib import Path

DATA_DIR    = Path(__file__).parent.parent / "data"
TRANSCRIPTS = DATA_DIR / "sf_transcripts"
BRIEFS      = DATA_DIR / "sf_briefs"
NEWSLETTERS = DATA_DIR / "sf_newsletters"
BRIEF_LOG   = DATA_DIR / "sf_brief_log.json"
LOG_MAX     = int(os.environ.get("SF_LOG_MAX", "300"))
VALID = {"success", "too_short", "no_niche_detected", "build_failed"}


def _now(): return datetime.now().isoformat()
def _load(p, d):
    if not p.exists(): return d
    try: return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError): return d
def _save(p, d):
    p.parent.mkdir(exist_ok=True)
    fd, t = tempfile.mkstemp(prefix=f".{p.name}.", suffix=".tmp", dir=p.parent)
    try:
        with os.fdopen(fd, "w") as f: json.dump(d, f, indent=2)
        os.replace(t, p)
    except Exception:
        try: os.unlink(t)
        except OSError: pass
        raise


def record_brief(slug, outcome, niche="", detail=""):
    if not slug: return
    log = _load(BRIEF_LOG, [])
    if not isinstance(log, list): log = []
    log.append({"ts": _now(), "slug": slug, "outcome": outcome, "niche": niche, "detail": detail})
    if len(log) > LOG_MAX: log = log[-LOG_MAX:]
    _save(BRIEF_LOG, log)


def recent_briefs(limit=50):
    log = _load(BRIEF_LOG, [])
    return log[-limit:][::-1] if isinstance(log, list) else []


def brief_outcome_summary():
    log = _load(BRIEF_LOG, [])
    if not isinstance(log, list) or not log:
        return {"total": 0, **{oc: 0 for oc in VALID}, "by_niche": {}}
    counts = {oc: 0 for oc in VALID}
    by_niche = {}
    for r in log:
        if r.get("outcome") in counts: counts[r.get("outcome")] += 1
        if r.get("outcome") == "success":
            n = r.get("niche", "?")
            by_niche[n] = by_niche.get(n, 0) + 1
    return {"total": len(log), **counts, "by_niche": by_niche}


def probe_inputs():
    n_tr = len(list(TRANSCRIPTS.glob("*.txt"))) if TRANSCRIPTS.exists() else 0
    n_br = len(list(BRIEFS.glob("*.md"))) if BRIEFS.exists() else 0
    n_nl = len(list(NEWSLETTERS.glob("*"))) if NEWSLETTERS.exists() else 0
    newest_tr = None
    if TRANSCRIPTS.exists():
        files = list(TRANSCRIPTS.glob("*.txt"))
        if files:
            m = max(f.stat().st_mtime for f in files)
            newest_tr = (datetime.now() - datetime.fromtimestamp(m)).days
    newest_nl = None
    if NEWSLETTERS.exists():
        files = list(NEWSLETTERS.glob("*"))
        if files:
            m = max(f.stat().st_mtime for f in files)
            newest_nl = (datetime.now() - datetime.fromtimestamp(m)).days
    return {"ok": n_tr > 0, "transcripts": n_tr, "briefs": n_br, "newsletters": n_nl,
            "transcripts_newest_age": newest_tr, "newsletters_newest_age": newest_nl}
