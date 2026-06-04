"""
Per-niche yield tracker for NicheLens.

NicheLens runs one cycle that fans out across every niche with at least
one active subscriber. For each niche it reads HTML snapshots out of
data/nl_snapshots/<niche>/, parses items, scores them, and emails a free
or paid newsletter. Several silent failure modes:

  1. The per-niche snapshot directory doesn't exist or is empty.
     build_newsletter() returns "" and the subscribers for that niche
     get nothing. No alert.
  2. bs4 is missing — parse_items() returns []; same outcome, but every
     niche silently goes dark at once.
  3. The niche is configured (in nl_niche_configs.json) but no
     subscriber has signed up — the snapshots are read every cycle for
     nothing.

This module tracks per-niche history so the diagnose check can flag
silent degradation as P1 and exposes probe_snapshots() so the runner can
show per-niche input counts without running a full cycle.

State file: data/nl_niche_health.json — dict keyed by niche slug:
  {
    "indie-board-games": {
      "last_run":           "2026-06-03T03:21:00",
      "last_items":         18,
      "last_free_sent":     12,
      "last_paid_sent":     3,
      "last_skipped":       false,
      "last_skip_reason":   "",
      "last_nonzero_at":    "2026-06-03T03:21:00",
      "consecutive_skips":  0,
      "total_runs":         28,
      "total_items":        443,
      "total_sent":         298
    }, ...
  }

Env:
  NL_ALERT_AFTER_SKIPS    default 2   — consecutive-skip threshold for P1 warn
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

DATA_DIR    = Path(__file__).parent.parent / "data"
SNAP_DIR    = DATA_DIR / "nl_snapshots"
HEALTH_FILE = DATA_DIR / "nl_niche_health.json"

ALERT_AFTER_SKIPS = int(os.environ.get("NL_ALERT_AFTER_SKIPS", "2"))


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


def _slug(niche: str) -> str:
    return (niche or "").strip().lower().replace(" ", "-")


def record_niche(
    niche: str,
    items: int,
    free_sent: int = 0,
    paid_sent: int = 0,
    skipped: bool = False,
    skip_reason: str = "",
) -> None:
    s = _slug(niche)
    if not s:
        return
    health = _load()
    rec = health.get(s, {
        "last_run":           "",
        "last_items":         0,
        "last_free_sent":     0,
        "last_paid_sent":     0,
        "last_skipped":       False,
        "last_skip_reason":   "",
        "last_nonzero_at":    "",
        "consecutive_skips":  0,
        "total_runs":         0,
        "total_items":        0,
        "total_sent":         0,
    })
    sent = max(free_sent, 0) + max(paid_sent, 0)
    rec["last_run"]         = _now()
    rec["last_items"]       = items
    rec["last_free_sent"]   = free_sent
    rec["last_paid_sent"]   = paid_sent
    rec["last_skipped"]     = bool(skipped)
    rec["last_skip_reason"] = skip_reason or ""
    rec["total_runs"]      += 1
    rec["total_items"]     += max(items, 0)
    rec["total_sent"]      += sent
    if skipped:
        rec["consecutive_skips"] = rec.get("consecutive_skips", 0) + 1
    else:
        rec["consecutive_skips"] = 0
        rec["last_nonzero_at"]   = _now()
    health[s] = rec
    _save(health)


def unhealthy_niches(threshold: int = None) -> list[dict]:
    threshold = ALERT_AFTER_SKIPS if threshold is None else threshold
    health = _load()
    out = []
    for niche, rec in health.items():
        if rec.get("consecutive_skips", 0) >= threshold:
            out.append({"niche": niche, **rec})
    return sorted(out, key=lambda r: -r.get("consecutive_skips", 0))


def summary() -> dict:
    health = _load()
    n = len(health)
    if not n:
        return {"niches": 0, "healthy": 0, "warning": 0,
                "total_items_all_time": 0, "total_sent_all_time": 0,
                "alert_threshold": ALERT_AFTER_SKIPS}
    healthy = sum(1 for r in health.values()
                  if r.get("consecutive_skips", 0) < ALERT_AFTER_SKIPS)
    return {
        "niches":               n,
        "healthy":              healthy,
        "warning":              n - healthy,
        "total_items_all_time": sum(r.get("total_items", 0) for r in health.values()),
        "total_sent_all_time":  sum(r.get("total_sent", 0)  for r in health.values()),
        "alert_threshold":      ALERT_AFTER_SKIPS,
    }


def report_lines() -> list[str]:
    health = _load()
    if not health:
        return ["(no niches tracked yet — run a cycle first)"]
    lines = [f"{'NICHE':<24s}  {'LAST RUN':<19s}  {'ITEMS':>5s}  "
             f"{'FREE':>4s}  {'PAID':>4s}  {'STREAK':>6s}  {'TOTAL':>6s}"]
    for niche, r in sorted(health.items()):
        cs = r.get("consecutive_skips", 0)
        streak = f"-{cs}" if cs else "ok"
        lines.append(
            f"{niche[:24]:<24s}  {(r.get('last_run') or '')[:19]:<19s}  "
            f"{r.get('last_items',0):>5d}  "
            f"{r.get('last_free_sent',0):>4d}  {r.get('last_paid_sent',0):>4d}  "
            f"{streak:>6s}  {r.get('total_items',0):>6d}"
            + (f"  skip: {r.get('last_skip_reason','')[:24]}"
               if r.get("last_skipped") and r.get("last_skip_reason") else "")
        )
    return lines


def probe_snapshots() -> dict:
    """Count snapshot files per niche subdirectory.

    Returns {"ok": bool, "total": N, "by_niche": {niche: count},
             "missing": [], "newest_age_days": N|None}.
    """
    if not SNAP_DIR.exists():
        return {"ok": False, "error": "nl_snapshots/ does not exist",
                "total": 0, "by_niche": {}, "missing": [], "newest_age_days": None}
    by_niche: dict[str, int] = {}
    newest_mtime = 0
    total = 0
    for sub in SNAP_DIR.iterdir():
        if not sub.is_dir():
            continue
        files = list(sub.glob("*.html"))
        by_niche[sub.name] = len(files)
        total += len(files)
        for f in files:
            m = f.stat().st_mtime
            if m > newest_mtime:
                newest_mtime = m
    newest_age = None
    if newest_mtime:
        newest_age = (datetime.now() - datetime.fromtimestamp(newest_mtime)).days
    return {
        "ok":              total > 0,
        "total":           total,
        "by_niche":        by_niche,
        "newest_age_days": newest_age,
    }


def _cli():
    import argparse
    p = argparse.ArgumentParser(description="NicheLens per-niche yield health")
    p.add_argument("--probe", action="store_true",
                   help="Count snapshot files per niche subdirectory")
    p.add_argument("--summary-json", action="store_true",
                   help="Emit machine-readable summary")
    args = p.parse_args()
    if args.probe:
        print(json.dumps(probe_snapshots(), indent=2))
        return
    if args.summary_json:
        print(json.dumps({"summary": summary(), "unhealthy": unhealthy_niches()}, indent=2))
        return
    for line in report_lines():
        print(line)
    s = summary()
    if s["niches"]:
        print()
        print(f"  {s['healthy']} healthy / {s['warning']} warning  "
              f"(threshold ≥{s['alert_threshold']} consecutive skips)  "
              f"all-time items: {s['total_items_all_time']}  sent: {s['total_sent_all_time']}")


if __name__ == "__main__":
    _cli()
