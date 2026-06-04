"""
Weekly signal-yield tracker for TrendScout.

TrendScout is owner-fed: the cycle reads bigrams out of every file in
data/ts_inputs/, keeps only candidates appearing in ≥2 sources, scores
them, and emits a weekly report — but only if at least 3 niches survive
the scoring threshold. Two silent failure modes the existing
run_full_cycle() doesn't surface:

  1. The owner stops dropping new feed snapshots. scan_signals() keeps
     parsing the stale files; the same niches keep ranking; the digest
     looks healthy but is showing subscribers the same trends every
     week. Nothing alerts.
  2. Only one input file present. The cross-source dedupe falls back to
     single-source mode (single Counter), so every popular bigram wins.
     Quality collapses; subscribers churn; the cycle still "runs."

This module tracks per-week yield so the diagnose check can flag silent
degradation as P1 and exposes probe_inputs() so the runner can show
input inventory + by-type breakdown without consuming a full cycle.

State file: data/ts_weekly_health.json — dict keyed by ISO week
("YYYY-WNN"):
  {
    "2026-W22": {
      "ts":               "2026-06-01T03:21:00",
      "sources":          4,
      "raw_signals":      812,
      "scored_niches":    10,
      "top_score":        17.5,
      "top_niche":        "minimalist journal",
      "sent":             42,
      "skipped":          false,
      "skip_reason":      ""
    }, ...
  }

Env:
  TS_ALERT_AFTER_SKIPS    default 2   — consecutive-skip threshold for P1 warn
  TS_MIN_SOURCES_HEALTHY  default 2   — below this, cross-source dedup is degraded
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

DATA_DIR    = Path(__file__).parent.parent / "data"
INPUT_DIR   = DATA_DIR / "ts_inputs"
HEALTH_FILE = DATA_DIR / "ts_weekly_health.json"

ALERT_AFTER_SKIPS    = int(os.environ.get("TS_ALERT_AFTER_SKIPS", "2"))
MIN_SOURCES_HEALTHY  = int(os.environ.get("TS_MIN_SOURCES_HEALTHY", "2"))

ACCEPTED_SUFFIXES = (".html", ".txt", ".md", ".csv")


def _now() -> str:
    return datetime.now().isoformat()


def _load() -> dict:
    if not HEALTH_FILE.exists():
        return {}
    try:
        d = json.loads(HEALTH_FILE.read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save(data: dict) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{HEALTH_FILE.name}.", suffix=".tmp", dir=DATA_DIR)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, HEALTH_FILE)
    except Exception:
        try: os.unlink(tmp)
        except OSError: pass
        raise


def record_week(
    week: str,
    sources: int,
    raw_signals: int,
    scored_niches: int,
    top_score: float = 0.0,
    top_niche: str = "",
    sent: int = 0,
    skipped: bool = False,
    skip_reason: str = "",
) -> None:
    """Persist the result of one fulfill_cycle attempt."""
    if not week:
        return
    health = _load()
    health[week] = {
        "ts":            _now(),
        "sources":       sources,
        "raw_signals":   raw_signals,
        "scored_niches": scored_niches,
        "top_score":     float(top_score) if top_score else 0.0,
        "top_niche":     top_niche or "",
        "sent":          sent,
        "skipped":       bool(skipped),
        "skip_reason":   skip_reason or "",
    }
    _save(health)


def _sorted_weeks() -> list[tuple[str, dict]]:
    """Return all weeks ordered newest first."""
    return sorted(_load().items(), key=lambda kv: kv[0], reverse=True)


def consecutive_skips() -> int:
    """How many of the most-recent weeks ended in skipped=True?"""
    n = 0
    for _, rec in _sorted_weeks():
        if rec.get("skipped"):
            n += 1
        else:
            break
    return n


def recent_weeks(limit: int = 8) -> list[dict]:
    return [{"week": w, **rec} for w, rec in _sorted_weeks()[:limit]]


def summary() -> dict:
    health = _load()
    n = len(health)
    if not n:
        return {"weeks": 0, "delivered": 0, "skipped": 0,
                "consecutive_skips": 0, "alert_threshold": ALERT_AFTER_SKIPS,
                "last_delivered": "", "total_sent": 0}
    skipped = sum(1 for r in health.values() if r.get("skipped"))
    last_delivered = ""
    for w, r in _sorted_weeks():
        if not r.get("skipped"):
            last_delivered = w
            break
    return {
        "weeks":             n,
        "delivered":         n - skipped,
        "skipped":           skipped,
        "consecutive_skips": consecutive_skips(),
        "alert_threshold":   ALERT_AFTER_SKIPS,
        "last_delivered":    last_delivered,
        "total_sent":        sum(r.get("sent", 0) for r in health.values()),
    }


def report_lines() -> list[str]:
    weeks = _sorted_weeks()
    if not weeks:
        return ["(no weeks tracked yet — run a cycle first)"]
    lines = [f"{'WEEK':<10s}  {'TS':<19s}  {'SRC':>3s}  {'SIG':>5s}  "
             f"{'NICH':>4s}  {'SENT':>5s}  {'STATUS':<14s}  TOP"]
    for w, r in weeks[:12]:
        status = "skip:" + r.get("skip_reason", "")[:8] if r.get("skipped") else "delivered"
        lines.append(
            f"{w:<10s}  {(r.get('ts') or '')[:19]:<19s}  "
            f"{r.get('sources',0):>3d}  {r.get('raw_signals',0):>5d}  "
            f"{r.get('scored_niches',0):>4d}  {r.get('sent',0):>5d}  "
            f"{status[:14]:<14s}  {r.get('top_niche','')[:24]}"
        )
    return lines


def probe_inputs() -> dict:
    """Count current ts_inputs/ files grouped by suffix.

    Returns {"ok": bool, "total": N, "accepted": N, "by_suffix": {...}, "newest_age_days": N|None}.
    """
    if not INPUT_DIR.exists():
        return {"ok": False, "error": "ts_inputs/ does not exist",
                "total": 0, "accepted": 0, "by_suffix": {}, "newest_age_days": None}
    files = list(INPUT_DIR.glob("*"))
    by_suffix: dict[str, int] = {}
    accepted_files = []
    for f in files:
        if not f.is_file():
            continue
        suf = f.suffix.lower()
        by_suffix[suf or "(none)"] = by_suffix.get(suf or "(none)", 0) + 1
        if suf in ACCEPTED_SUFFIXES:
            accepted_files.append(f)
    newest_age = None
    if accepted_files:
        newest_mtime = max(f.stat().st_mtime for f in accepted_files)
        newest_age = (datetime.now() - datetime.fromtimestamp(newest_mtime)).days
    return {
        "ok":              len(accepted_files) > 0,
        "total":           len([f for f in files if f.is_file()]),
        "accepted":        len(accepted_files),
        "by_suffix":       by_suffix,
        "newest_age_days": newest_age,
        "accepted_suffixes": list(ACCEPTED_SUFFIXES),
        "min_sources_healthy": MIN_SOURCES_HEALTHY,
    }


def _cli():
    import argparse
    p = argparse.ArgumentParser(description="TrendScout weekly signal-yield health")
    p.add_argument("--probe", action="store_true",
                   help="Count ts_inputs/ files by suffix + age")
    p.add_argument("--summary-json", action="store_true",
                   help="Emit machine-readable summary")
    args = p.parse_args()
    if args.probe:
        print(json.dumps(probe_inputs(), indent=2))
        return
    if args.summary_json:
        print(json.dumps({"summary": summary(), "recent": recent_weeks()}, indent=2))
        return
    for line in report_lines():
        print(line)
    s = summary()
    if s["weeks"]:
        print()
        print(f"  {s['delivered']} delivered / {s['skipped']} skipped  "
              f"streak: -{s['consecutive_skips']} (threshold {s['alert_threshold']})  "
              f"last delivered: {s['last_delivered'] or '—'}  "
              f"all-time sent: {s['total_sent']}")


if __name__ == "__main__":
    _cli()
