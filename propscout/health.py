"""
Per-cell grid-health tracker for PropScout.

PROSPECT_GRID has 9 (city, record_type) cells driven by Socrata/Carto endpoints.
When an open-data contract changes (column rename, API moved, dataset deprecated),
the affected cell silently returns 0 forever — the daily digest still mails a
"0 found" row and nothing alerts. This module tracks per-cell history so the
diagnose check can flag cells with N consecutive zeros as P1.

State file: data/ps_cell_health.json — a dict keyed by "<city>::<record_type>":
  {
    "philadelphia::tax_delinquent": {
      "last_run":           "2026-06-03T03:21:00",
      "last_run_count":     12,
      "last_nonzero_at":    "2026-06-03T03:21:00",
      "last_nonzero_count": 12,
      "consecutive_zeros":  0,
      "total_runs":         42,
      "total_found":        503,
      "last_error":         ""
    },
    ...
  }

Env vars:
  PS_ALERT_AFTER_ZEROS    default 3   — consecutive-zero threshold for P1 warn
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Iterable

DATA_DIR    = Path(__file__).parent.parent / "data"
HEALTH_FILE = DATA_DIR / "ps_cell_health.json"

ALERT_AFTER_ZEROS = int(os.environ.get("PS_ALERT_AFTER_ZEROS", "3"))


def _now() -> str:
    return datetime.now().isoformat()


def _key(city: str, record_type: str) -> str:
    return f"{city.lower()}::{record_type}"


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


def record_cell(city: str, record_type: str, count: int, error: str = "") -> None:
    """Update the per-cell record after a grid pass."""
    health = _load()
    k = _key(city, record_type)
    rec = health.get(k, {
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
    health[k] = rec
    _save(health)


def unhealthy_cells(threshold: int = None) -> list[dict]:
    """Cells with consecutive_zeros >= threshold (default PS_ALERT_AFTER_ZEROS)."""
    threshold = ALERT_AFTER_ZEROS if threshold is None else threshold
    health = _load()
    out = []
    for k, rec in health.items():
        if rec.get("consecutive_zeros", 0) >= threshold:
            city, rt = k.split("::", 1)
            out.append({
                "city": city, "record_type": rt,
                **rec,
            })
    return sorted(out, key=lambda r: -r.get("consecutive_zeros", 0))


def summary() -> dict:
    health = _load()
    total = len(health)
    if not total:
        return {"cells": 0, "healthy": 0, "warning": 0, "total_found_all_time": 0}
    healthy = sum(1 for r in health.values() if r.get("consecutive_zeros", 0) < ALERT_AFTER_ZEROS)
    warning = total - healthy
    total_found = sum(r.get("total_found", 0) for r in health.values())
    return {
        "cells":                total,
        "healthy":              healthy,
        "warning":              warning,
        "alert_threshold":      ALERT_AFTER_ZEROS,
        "total_found_all_time": total_found,
    }


def report_lines() -> list[str]:
    """Owner-readable, line-oriented per-cell health report."""
    health = _load()
    if not health:
        return ["(no cells tracked yet — run a cycle first)"]
    lines = [f"{'CELL':<32s}  {'LAST RUN':<19s}  {'FOUND':>5s}  {'STREAK':>6s}  {'TOTAL':>6s}"]
    for k, r in sorted(health.items()):
        cz = r.get("consecutive_zeros", 0)
        streak = f"-{cz}" if cz else "ok"
        last_run = (r.get("last_run") or "")[:19]
        lines.append(
            f"{k:<32s}  {last_run:<19s}  "
            f"{r.get('last_run_count',0):>5d}  {streak:>6s}  {r.get('total_found',0):>6d}"
            + (f"  err: {r.get('last_error','')[:40]}" if r.get("last_error") else "")
        )
    return lines


def _cli():
    import argparse
    p = argparse.ArgumentParser(description="PropScout grid-cell health")
    p.add_argument("--summary-json", action="store_true",
                    help="Emit machine-readable summary instead of table")
    args = p.parse_args()
    if args.summary_json:
        print(json.dumps({
            "summary": summary(),
            "unhealthy": unhealthy_cells(),
        }, indent=2))
        return
    for line in report_lines():
        print(line)
    s = summary()
    if s["cells"]:
        print()
        print(f"  {s['healthy']} healthy / {s['warning']} warning  "
              f"(threshold ≥{s['alert_threshold']} consecutive zeros)  "
              f"total prospects found all-time: {s['total_found_all_time']}")


if __name__ == "__main__":
    _cli()
