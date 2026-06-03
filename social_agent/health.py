"""
Per-platform health derivation for social_agent.

Unlike the lead-scraper agents (propscout/hudscout/dropship_scout) which need
a side-table because they scan ephemeral upstream sources, social_agent already
has a complete dispatch history in data/social_posts.json — every dispatch
writes a record with per-platform results. This module *derives* health from
that log instead of introducing a parallel file.

Health per platform (last 90 days of dispatches):
  · total_attempts
  · last_success_at
  · consecutive_failures      — # of failed/skipped since the most recent success
  · last_failure_reason       — error/reason text from the most recent failure
  · last_status               — posted | dry_run | failed | skipped | (none)

Env:
  SA_ALERT_AFTER_FAILS  default 3  — consecutive_failures threshold for P1
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path

DATA_DIR  = Path(__file__).parent.parent / "data"
LOG_FILE  = DATA_DIR / "social_posts.json"

ALERT_AFTER_FAILS = int(os.environ.get("SA_ALERT_AFTER_FAILS", "3"))

# Statuses that count as "not a successful real post" — used to count consecutive
# failures. dry_run isn't a failure (owner-initiated test), so it's excluded.
FAIL_STATUSES    = {"failed", "skipped"}
SUCCESS_STATUSES = {"posted"}


def _load() -> list:
    if not LOG_FILE.exists():
        return []
    try:
        d = json.loads(LOG_FILE.read_text())
        return d if isinstance(d, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def derive_health(window_days: int = 90) -> dict:
    """Walk the dispatch log newest-to-oldest and accumulate per-platform stats.

    Returns dict keyed by platform name with:
      {total_attempts, last_success_at, consecutive_failures,
       last_failure_reason, last_status}
    """
    log = _load()
    if not log:
        return {}

    cutoff = (datetime.now() - timedelta(days=window_days)).isoformat()
    # Iterate newest first so consecutive_failures is "from now back to last success".
    out: dict = {}
    for entry in reversed(log):
        if entry.get("dispatched_at", "") < cutoff:
            continue
        if entry.get("dry_run"):
            # Skip dry_run entries entirely — they don't represent real attempts.
            continue
        for r in entry.get("results", []):
            plat = r.get("platform", "?")
            status = r.get("status", "?")
            slot = out.setdefault(plat, {
                "total_attempts":       0,
                "last_success_at":      "",
                "consecutive_failures": 0,
                "last_failure_reason":  "",
                "last_status":          "",
                "_seen_success":        False,
            })
            slot["total_attempts"] += 1
            if not slot["last_status"]:
                slot["last_status"] = status
            if status in SUCCESS_STATUSES:
                if not slot["_seen_success"]:
                    slot["last_success_at"] = entry.get("dispatched_at", "")
                    slot["_seen_success"] = True
            elif status in FAIL_STATUSES and not slot["_seen_success"]:
                slot["consecutive_failures"] += 1
                if not slot["last_failure_reason"]:
                    slot["last_failure_reason"] = (
                        r.get("error") or r.get("reason") or ""
                    )[:120]
    # Drop the internal _seen_success flag
    for v in out.values():
        v.pop("_seen_success", None)
    return out


def unhealthy_platforms(threshold: int = None) -> list[dict]:
    """Platforms with consecutive_failures >= threshold."""
    threshold = ALERT_AFTER_FAILS if threshold is None else threshold
    h = derive_health()
    return sorted(
        ({"platform": p, **v} for p, v in h.items()
         if v.get("consecutive_failures", 0) >= threshold),
        key=lambda r: -r.get("consecutive_failures", 0),
    )


def summary() -> dict:
    h = derive_health()
    if not h:
        return {"platforms_with_attempts": 0, "healthy": 0, "warning": 0,
                "alert_threshold": ALERT_AFTER_FAILS}
    healthy = sum(1 for v in h.values() if v.get("consecutive_failures", 0) < ALERT_AFTER_FAILS)
    return {
        "platforms_with_attempts": len(h),
        "healthy": healthy,
        "warning": len(h) - healthy,
        "alert_threshold": ALERT_AFTER_FAILS,
    }


def report_lines() -> list[str]:
    h = derive_health()
    if not h:
        return ["(no real dispatches yet — only dry-runs in the log, or log is empty)"]
    lines = [f"{'PLATFORM':<12s}  {'STATUS':<8s}  {'ATTEMPTS':>8s}  {'STREAK':>6s}  {'LAST SUCCESS':<19s}"]
    for plat, v in sorted(h.items()):
        cf = v.get("consecutive_failures", 0)
        streak = f"-{cf}" if cf else "ok"
        last_succ = (v.get("last_success_at") or "(never)")[:19]
        last_st = v.get("last_status", "?")
        lines.append(
            f"{plat:<12s}  {last_st:<8s}  {v.get('total_attempts',0):>8d}  "
            f"{streak:>6s}  {last_succ:<19s}"
            + (f"  err: {v['last_failure_reason'][:40]}" if v.get("last_failure_reason") else "")
        )
    return lines
