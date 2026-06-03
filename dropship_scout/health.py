"""
Per-source health tracker for DropshipScout.

The agent scrapes 6 sources every cycle:
  · tiktok_live       — TikTok Creative Center JSON-in-HTML
  · amazon_beauty
  · amazon_home-kitchen
  · amazon_toys
  · amazon_fashion
  · amazon_pet-supplies

Two silent degradation modes that the existing run_full_cycle() hides:

  1. TikTok Creative Center returns nothing (SPA layout change, anti-bot block).
     get_tiktok_hashtags() falls back to the curated evergreen list and the
     digest still has data — looks healthy but the live feed is dark. The
     public lead-magnet page silently loses its rank labels.
  2. A single Amazon category returns []. Other categories still populate, so
     aggregate "amazon_products" stays >0 and nothing alerts.

This module records per-source history so diagnose.py can flag silent decay
and the owner can see exactly which source needs attention.

State file: data/ds_source_health.json — dict keyed by source name:
  {
    "tiktok_live": {
      "last_run":           "2026-06-03T03:21:00",
      "last_run_count":     12,
      "last_nonzero_at":    "2026-06-02T03:21:00",
      "last_nonzero_count": 8,
      "consecutive_zeros":  1,
      "total_runs":         42,
      "total_found":        217,
      "last_error":         ""
    }, ...
  }

Env:
  DS_ALERT_AFTER_ZEROS  default 3
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

DATA_DIR     = Path(__file__).parent.parent / "data"
HEALTH_FILE  = DATA_DIR / "ds_source_health.json"

ALERT_AFTER_ZEROS = int(os.environ.get("DS_ALERT_AFTER_ZEROS", "3"))

# Mirrors AMAZON_BESTSELLER_CATEGORIES in tools.py — keep in sync.
KNOWN_SOURCES = [
    "tiktok_live",
    "amazon_beauty",
    "amazon_home-kitchen",
    "amazon_toys",
    "amazon_fashion",
    "amazon_pet-supplies",
]


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


def record_source(source: str, count: int, error: str = "") -> None:
    """Update the per-source record after a scrape."""
    source = (source or "").strip()
    if not source:
        return
    health = _load()
    rec = health.get(source, {
        "last_run":           "",
        "last_run_count":     0,
        "last_nonzero_at":    "",
        "last_nonzero_count": 0,
        "consecutive_zeros":  0,
        "total_runs":         0,
        "total_found":        0,
        "last_error":         "",
    })
    rec["last_run"]       = _now()
    rec["last_run_count"] = count
    rec["total_runs"]    += 1
    rec["total_found"]   += max(count, 0)
    rec["last_error"]     = error or ""
    if count > 0:
        rec["last_nonzero_at"]    = _now()
        rec["last_nonzero_count"] = count
        rec["consecutive_zeros"]  = 0
    else:
        rec["consecutive_zeros"] = rec.get("consecutive_zeros", 0) + 1
    health[source] = rec
    _save(health)


def unhealthy_sources(threshold: int = None) -> list[dict]:
    threshold = ALERT_AFTER_ZEROS if threshold is None else threshold
    health = _load()
    out = []
    for name, rec in health.items():
        if rec.get("consecutive_zeros", 0) >= threshold:
            out.append({"source": name, **rec})
    return sorted(out, key=lambda r: -r.get("consecutive_zeros", 0))


def summary() -> dict:
    health = _load()
    n = len(health)
    if not n:
        return {"sources": 0, "healthy": 0, "warning": 0,
                "total_found_all_time": 0, "alert_threshold": ALERT_AFTER_ZEROS}
    healthy = sum(1 for r in health.values() if r.get("consecutive_zeros", 0) < ALERT_AFTER_ZEROS)
    return {
        "sources":              n,
        "healthy":              healthy,
        "warning":              n - healthy,
        "total_found_all_time": sum(r.get("total_found", 0) for r in health.values()),
        "alert_threshold":      ALERT_AFTER_ZEROS,
    }


def report_lines() -> list[str]:
    health = _load()
    if not health:
        return ["(no sources tracked yet — run a cycle first)"]
    lines = [f"{'SOURCE':<22s}  {'LAST RUN':<19s}  {'FOUND':>5s}  {'STREAK':>6s}  {'TOTAL':>6s}"]
    for name, r in sorted(health.items()):
        cz = r.get("consecutive_zeros", 0)
        streak = f"-{cz}" if cz else "ok"
        lines.append(
            f"{name:<22s}  {(r.get('last_run') or '')[:19]:<19s}  "
            f"{r.get('last_run_count',0):>5d}  {streak:>6s}  {r.get('total_found',0):>6d}"
            + (f"  err: {r.get('last_error','')[:40]}" if r.get("last_error") else "")
        )
    return lines


def _cli():
    import argparse
    p = argparse.ArgumentParser(description="DropshipScout per-source health")
    p.add_argument("--summary-json", action="store_true")
    args = p.parse_args()
    if args.summary_json:
        print(json.dumps({"summary": summary(), "unhealthy": unhealthy_sources()}, indent=2))
        return
    for line in report_lines():
        print(line)
    s = summary()
    if s["sources"]:
        print()
        print(f"  {s['healthy']} healthy / {s['warning']} warning  "
              f"(threshold ≥{s['alert_threshold']} consecutive zeros)  "
              f"all-time found: {s['total_found_all_time']}")


if __name__ == "__main__":
    _cli()
