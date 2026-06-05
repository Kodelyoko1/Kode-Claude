"""GutenbergVoice health: per-script outcomes + delivery log."""
from __future__ import annotations
import json, os, tempfile
from datetime import datetime
from pathlib import Path

DATA_DIR    = Path(__file__).parent.parent / "data"
INPUTS_DIR  = DATA_DIR / "gv_inputs"
OUTPUTS_DIR = DATA_DIR / "gv_outputs"
FILE_LOG    = DATA_DIR / "gv_script_log.json"
DELIVERY    = DATA_DIR / "gv_delivery_outcomes.json"
LOG_MAX     = int(os.environ.get("GV_LOG_MAX", "300"))
VALID = {'success', 'build_failed', 'no_text', 'spec_invalid'}


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
def _append(p, entry):
    log = _load(p, [])
    if not isinstance(log, list): log = []
    log.append(entry)
    if len(log) > LOG_MAX: log = log[-LOG_MAX:]
    _save(p, log)


def record_script(slug, outcome, detail="", **extra):
    if not slug: return
    _append(FILE_LOG, {"ts": _now(), "slug": slug, "outcome": outcome,
                       "detail": detail, **extra})


def record_delivery(email, outcome, slugs=0, detail=""):
    if not email: return
    _append(DELIVERY, {"ts": _now(), "email": email.lower(), "outcome": outcome,
                       "slugs": int(slugs), "detail": detail})


def recent_scripts(limit=50):
    log = _load(FILE_LOG, [])
    return log[-limit:][::-1] if isinstance(log, list) else []


def script_outcome_summary():
    log = _load(FILE_LOG, [])
    if not isinstance(log, list) or not log:
        return {"total": 0, **{oc: 0 for oc in VALID}}
    counts = {oc: 0 for oc in VALID}
    for r in log:
        if r.get("outcome") in counts: counts[r.get("outcome")] += 1
    return {"total": len(log), **counts}


def stuck_mail_failed(min_attempts=3):
    log = _load(DELIVERY, [])
    if not isinstance(log, list): return []
    by = {}
    for r in log:
        if r.get("outcome") != "mail_failed": continue
        e = r.get("email", "")
        if not e: continue
        rec = by.setdefault(e, {"attempts": 0, "last_ts": "", "last_detail": ""})
        rec["attempts"] += 1; rec["last_ts"] = r.get("ts", ""); rec["last_detail"] = r.get("detail", "")
    return sorted([{"email": e, **rec} for e, rec in by.items() if rec["attempts"] >= min_attempts],
                  key=lambda r: -r["attempts"])


def probe_inputs():
    n_in = len(list(INPUTS_DIR.glob("*.json"))) if INPUTS_DIR.exists() else 0
    n_out = 0
    if OUTPUTS_DIR.exists():
        n_out = sum(1 for x in OUTPUTS_DIR.iterdir() if x.is_dir() or x.suffix == ".md")
    newest = None
    if INPUTS_DIR.exists():
        files = list(INPUTS_DIR.glob("*.json"))
        if files:
            m = max(f.stat().st_mtime for f in files)
            newest = (datetime.now() - datetime.fromtimestamp(m)).days
    return {"ok": n_in > 0, "gv_inputs": n_in, "gv_outputs": n_out,
            "newest_age_days": newest}
