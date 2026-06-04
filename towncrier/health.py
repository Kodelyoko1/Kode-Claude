"""
Per-city event-yield + snapshot-input tracker for TownCrier.

TownCrier is owner-fed: the cycle reads HTML snapshots dropped into
data/tc_snapshots/ (one per source per city) and emits a digest only if
≥5 events parse out. Two silent failure modes the existing run_full_cycle()
doesn't surface:

  1. The owner stops adding snapshots for a city (vacation, source dies,
     fetch script broken). collect_events() returns []; build_digest()
     returns {"skipped": True, "reason": "insufficient_events"}; the
     subscriber list for that city silently goes dark. No alert.
  2. Snapshots are present but the page layout drifted and our heuristic
     (date_match + length window) extracts nothing useful. Same outcome,
     same silence.

This module tracks per-city run history so the diagnose check can flag
silent degradation as P1, plus a probe_snapshots() helper the runner uses
to count current input inventory without running a full cycle.

State file: data/tc_city_health.json — dict keyed by city slug:
  {
    "portland-me": {
      "last_run":           "2026-06-03T03:21:00",
      "last_event_count":   23,
      "last_sent":          18,
      "last_skipped":       false,
      "last_skip_reason":   "",
      "last_nonzero_at":    "2026-06-03T03:21:00",
      "consecutive_skips":  0,
      "total_runs":         42,
      "total_events":       814,
      "total_sent":         612
    }, ...
  }

Env:
  TC_ALERT_AFTER_SKIPS    default 2   — consecutive-skip threshold for P1 warn
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

DATA_DIR     = Path(__file__).parent.parent / "data"
SNAPSHOT_DIR = DATA_DIR / "tc_snapshots"
HEALTH_FILE  = DATA_DIR / "tc_city_health.json"

ALERT_AFTER_SKIPS = int(os.environ.get("TC_ALERT_AFTER_SKIPS", "2"))


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


def _slug(city: str) -> str:
    return (city or "").strip().lower().replace(" ", "-")


def record_city(
    city: str,
    event_count: int,
    sent: int = 0,
    skipped: bool = False,
    skip_reason: str = "",
) -> None:
    """Update the per-city record after build_digest() completes."""
    s = _slug(city)
    if not s:
        return
    health = _load()
    rec = health.get(s, {
        "last_run":           "",
        "last_event_count":   0,
        "last_sent":          0,
        "last_skipped":       False,
        "last_skip_reason":   "",
        "last_nonzero_at":    "",
        "consecutive_skips":  0,
        "total_runs":         0,
        "total_events":       0,
        "total_sent":         0,
    })
    rec["last_run"]         = _now()
    rec["last_event_count"] = event_count
    rec["last_sent"]        = sent
    rec["last_skipped"]     = bool(skipped)
    rec["last_skip_reason"] = skip_reason or ""
    rec["total_runs"]      += 1
    rec["total_events"]    += max(event_count, 0)
    rec["total_sent"]      += max(sent, 0)
    if skipped:
        rec["consecutive_skips"] = rec.get("consecutive_skips", 0) + 1
    else:
        rec["consecutive_skips"] = 0
        rec["last_nonzero_at"]   = _now()
    health[s] = rec
    _save(health)


def unhealthy_cities(threshold: int = None) -> list[dict]:
    threshold = ALERT_AFTER_SKIPS if threshold is None else threshold
    health = _load()
    out = []
    for city, rec in health.items():
        if rec.get("consecutive_skips", 0) >= threshold:
            out.append({"city": city, **rec})
    return sorted(out, key=lambda r: -r.get("consecutive_skips", 0))


def summary() -> dict:
    health = _load()
    n = len(health)
    if not n:
        return {"cities": 0, "healthy": 0, "warning": 0,
                "total_events_all_time": 0, "total_sent_all_time": 0,
                "alert_threshold": ALERT_AFTER_SKIPS}
    healthy = sum(1 for r in health.values()
                  if r.get("consecutive_skips", 0) < ALERT_AFTER_SKIPS)
    return {
        "cities":                n,
        "healthy":               healthy,
        "warning":               n - healthy,
        "total_events_all_time": sum(r.get("total_events", 0) for r in health.values()),
        "total_sent_all_time":   sum(r.get("total_sent", 0)   for r in health.values()),
        "alert_threshold":       ALERT_AFTER_SKIPS,
    }


def report_lines() -> list[str]:
    health = _load()
    if not health:
        return ["(no cities tracked yet — run a cycle first)"]
    lines = [f"{'CITY':<20s}  {'LAST RUN':<19s}  {'EVENTS':>6s}  {'SENT':>5s}  {'STREAK':>6s}  {'TOTAL':>6s}"]
    for city, r in sorted(health.items()):
        cs = r.get("consecutive_skips", 0)
        streak = f"-{cs}" if cs else "ok"
        lines.append(
            f"{city[:20]:<20s}  {(r.get('last_run') or '')[:19]:<19s}  "
            f"{r.get('last_event_count',0):>6d}  {r.get('last_sent',0):>5d}  "
            f"{streak:>6s}  {r.get('total_events',0):>6d}"
            + (f"  skip: {r.get('last_skip_reason','')[:30]}"
               if r.get("last_skipped") and r.get("last_skip_reason") else "")
        )
    return lines


def probe_snapshots() -> dict:
    """Count current snapshot inputs grouped by city prefix.

    Snapshot files are named <city-slug>_<source>.html per the existing
    SNAPSHOT_DIR.glob(f"{city}_*.html") pattern in tools.collect_events().
    Returns {"ok": bool, "total": N, "by_city": {city: count}}.
    """
    if not SNAPSHOT_DIR.exists():
        return {"ok": False, "error": "tc_snapshots/ does not exist",
                "total": 0, "by_city": {}}
    files = list(SNAPSHOT_DIR.glob("*.html"))
    by_city: dict[str, int] = {}
    for f in files:
        # Match the collect_events glob: everything before the first "_" is city
        stem = f.stem
        city = stem.split("_", 1)[0] if "_" in stem else stem
        by_city[city] = by_city.get(city, 0) + 1
    return {"ok": len(files) > 0, "total": len(files), "by_city": by_city}


def _cli():
    import argparse
    p = argparse.ArgumentParser(description="TownCrier per-city event-yield health")
    p.add_argument("--probe", action="store_true",
                   help="Count current snapshot inputs per city")
    p.add_argument("--summary-json", action="store_true",
                   help="Emit machine-readable summary")
    args = p.parse_args()
    if args.probe:
        print(json.dumps(probe_snapshots(), indent=2))
        return
    if args.summary_json:
        print(json.dumps({"summary": summary(), "unhealthy": unhealthy_cities()}, indent=2))
        return
    for line in report_lines():
        print(line)
    s = summary()
    if s["cities"]:
        print()
        print(f"  {s['healthy']} healthy / {s['warning']} warning  "
              f"(threshold ≥{s['alert_threshold']} consecutive skips)  "
              f"all-time events: {s['total_events_all_time']}  sent: {s['total_sent_all_time']}")


if __name__ == "__main__":
    _cli()
