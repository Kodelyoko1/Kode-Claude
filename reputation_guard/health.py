"""
Per-business yield tracker for ReputationGuard.

ReputationGuard is owner-fed: each business gets one HTML snapshot at
data/rg_snapshots/<biz_slug>.html (Google or Yelp page export). The
cycle parses it, flags ≤3-star or negative-lexicon reviews, drafts
replies for active clients, and emails the digest.

Silent failure modes the existing run_full_cycle doesn't surface:

  1. Owner stops refreshing snapshots for an active-client business →
     fulfill_cycle still sees the same reviews every cycle, drafts the
     same replies, and the client wonders why they keep getting
     duplicates. No alert.
  2. A snapshot has no recognizable review blocks (Google/Yelp tweak
     the markup) → parse_snapshot returns []; is_negative finds
     nothing to flag; fulfill_cycle continues silently with 0 drafts.
  3. An active client has no snapshot file at all → fulfill_cycle's
     `if not snap.exists(): continue` skips them with zero indication.

This module tracks per-business cycle history so the diagnose check can
flag silent degradation as P1, plus probe_snapshots() so the runner
can show inventory + per-business newest age without consuming a cycle.

State file: data/rg_business_health.json — dict keyed by business slug:
  {
    "joes-pizza-portland": {
      "last_run":           "2026-06-04T03:21:00",
      "last_reviews":       12,
      "last_negatives":     4,
      "last_drafts_sent":   1,
      "last_skipped":       false,
      "last_skip_reason":   "",
      "last_nonzero_at":    "2026-06-04T03:21:00",
      "consecutive_skips":  0,
      "total_runs":         28,
      "total_negatives":    74,
      "total_drafts_sent":  21
    }, ...
  }

Env:
  RG_ALERT_AFTER_SKIPS    default 2   — consecutive-skip threshold for P1 warn
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

DATA_DIR     = Path(__file__).parent.parent / "data"
SNAPSHOT_DIR = DATA_DIR / "rg_snapshots"
HEALTH_FILE  = DATA_DIR / "rg_business_health.json"

ALERT_AFTER_SKIPS = int(os.environ.get("RG_ALERT_AFTER_SKIPS", "2"))


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


def record_business(
    business_slug: str,
    reviews: int,
    negatives: int,
    drafts_sent: int = 0,
    skipped: bool = False,
    skip_reason: str = "",
) -> None:
    """Persist one fulfill_cycle attempt for a single business."""
    s = (business_slug or "").strip().lower()
    if not s:
        return
    health = _load()
    rec = health.get(s, {
        "last_run":          "",
        "last_reviews":      0,
        "last_negatives":    0,
        "last_drafts_sent":  0,
        "last_skipped":      False,
        "last_skip_reason":  "",
        "last_nonzero_at":   "",
        "consecutive_skips": 0,
        "total_runs":        0,
        "total_negatives":   0,
        "total_drafts_sent": 0,
    })
    rec["last_run"]         = _now()
    rec["last_reviews"]     = reviews
    rec["last_negatives"]   = negatives
    rec["last_drafts_sent"] = drafts_sent
    rec["last_skipped"]     = bool(skipped)
    rec["last_skip_reason"] = skip_reason or ""
    rec["total_runs"]      += 1
    rec["total_negatives"]  += max(negatives, 0)
    rec["total_drafts_sent"] += max(drafts_sent, 0)
    if skipped:
        rec["consecutive_skips"] = rec.get("consecutive_skips", 0) + 1
    else:
        rec["consecutive_skips"] = 0
        rec["last_nonzero_at"]   = _now()
    health[s] = rec
    _save(health)


def unhealthy_businesses(threshold: int = None) -> list[dict]:
    threshold = ALERT_AFTER_SKIPS if threshold is None else threshold
    health = _load()
    out = []
    for biz, rec in health.items():
        if rec.get("consecutive_skips", 0) >= threshold:
            out.append({"business_slug": biz, **rec})
    return sorted(out, key=lambda r: -r.get("consecutive_skips", 0))


def summary() -> dict:
    health = _load()
    n = len(health)
    if not n:
        return {"businesses": 0, "healthy": 0, "warning": 0,
                "total_negatives_all_time": 0, "total_drafts_all_time": 0,
                "alert_threshold": ALERT_AFTER_SKIPS}
    healthy = sum(1 for r in health.values()
                  if r.get("consecutive_skips", 0) < ALERT_AFTER_SKIPS)
    return {
        "businesses":               n,
        "healthy":                  healthy,
        "warning":                  n - healthy,
        "total_negatives_all_time": sum(r.get("total_negatives", 0) for r in health.values()),
        "total_drafts_all_time":    sum(r.get("total_drafts_sent", 0) for r in health.values()),
        "alert_threshold":          ALERT_AFTER_SKIPS,
    }


def report_lines() -> list[str]:
    health = _load()
    if not health:
        return ["(no businesses tracked yet — run a cycle first)"]
    lines = [f"{'BUSINESS':<28s}  {'LAST RUN':<19s}  {'REV':>4s}  "
             f"{'NEG':>4s}  {'DRAFT':>5s}  {'STREAK':>6s}  {'TOTAL':>6s}"]
    for biz, r in sorted(health.items()):
        cs = r.get("consecutive_skips", 0)
        streak = f"-{cs}" if cs else "ok"
        lines.append(
            f"{biz[:28]:<28s}  {(r.get('last_run') or '')[:19]:<19s}  "
            f"{r.get('last_reviews',0):>4d}  {r.get('last_negatives',0):>4d}  "
            f"{r.get('last_drafts_sent',0):>5d}  {streak:>6s}  "
            f"{r.get('total_drafts_sent',0):>6d}"
            + (f"  skip: {r.get('last_skip_reason','')[:24]}"
               if r.get("last_skipped") and r.get("last_skip_reason") else "")
        )
    return lines


def probe_snapshots() -> dict:
    """Count snapshot files + per-business newest mtime.

    Returns {"ok": bool, "total": N, "by_business": {slug: {"age_days": N}}, "newest_age_days": N|None}.
    """
    if not SNAPSHOT_DIR.exists():
        return {"ok": False, "error": "rg_snapshots/ does not exist",
                "total": 0, "by_business": {}, "newest_age_days": None}
    files = [f for f in SNAPSHOT_DIR.glob("*.html") if f.is_file()]
    by_business: dict[str, dict] = {}
    newest_mtime = 0
    for f in files:
        slug = f.stem
        age = (datetime.now() - datetime.fromtimestamp(f.stat().st_mtime)).days
        by_business[slug] = {"age_days": age}
        m = f.stat().st_mtime
        if m > newest_mtime:
            newest_mtime = m
    newest_age = None
    if newest_mtime:
        newest_age = (datetime.now() - datetime.fromtimestamp(newest_mtime)).days
    return {
        "ok":              len(files) > 0,
        "total":           len(files),
        "by_business":     by_business,
        "newest_age_days": newest_age,
    }


def _cli():
    import argparse
    p = argparse.ArgumentParser(description="ReputationGuard per-business yield health")
    p.add_argument("--probe",        action="store_true",
                   help="Count snapshot files + per-business newest age")
    p.add_argument("--summary-json", action="store_true")
    args = p.parse_args()
    if args.probe:
        print(json.dumps(probe_snapshots(), indent=2))
        return
    if args.summary_json:
        print(json.dumps({"summary": summary(),
                          "unhealthy": unhealthy_businesses()}, indent=2))
        return
    for line in report_lines():
        print(line)
    s = summary()
    if s["businesses"]:
        print()
        print(f"  {s['healthy']} healthy / {s['warning']} warning  "
              f"(threshold ≥{s['alert_threshold']} consecutive skips)  "
              f"all-time negatives: {s['total_negatives_all_time']}  "
              f"drafts: {s['total_drafts_all_time']}")


if __name__ == "__main__":
    _cli()
