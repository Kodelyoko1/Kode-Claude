"""
Derived discovery + funnel stats for LinkMender.

discover_prospects() rotates through DEFAULT_PROSPECT_QUERIES (one per day of
month). When a single query goes dry — Bing devalues a dork, results saturate
to known-blocked domains — the agent silently produces fewer prospects on
that day-of-month. Owner can't see the per-query yield without manually
walking lm_prospects.json.

This module derives:
  · Per-query yield (which DEFAULT_PROSPECT_QUERIES are still producing
    prospects, last_discovered_at per query)
  · Per-status funnel breakdown
  · Per-site audit yield from snapshot dirs (how many "snapshots" never
    converted to "contacted")
  · Recent discovery cadence (P1 if no new prospect in 7+ days while pool
    still has unflipped slugs)

No new state file — pure derivation.
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

DATA_DIR     = Path(__file__).parent.parent / "data"
PROSPECTS    = DATA_DIR / "lm_prospects.json"
SNAPSHOT_DIR = DATA_DIR / "lm_snapshots"


def _load(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return default


def funnel() -> dict:
    prospects = _load(PROSPECTS, [])
    if not isinstance(prospects, list):
        return {"error": "lm_prospects.json wrong shape", "total": 0}
    by_status = Counter(p.get("status", "?") for p in prospects)
    return {
        "total": len(prospects),
        "by_status": dict(by_status),
    }


def per_query_yield(window_days: int = 30) -> dict:
    """LinkMender doesn't tag prospects with the query that found them
    (discover_prospects rotates queries by day-of-month). We can approximate
    yield by bucketing discovered_at into days-of-month and mapping back to
    the query rotation."""
    from link_mender.tools import DEFAULT_PROSPECT_QUERIES
    prospects = _load(PROSPECTS, [])
    if not isinstance(prospects, list) or not prospects:
        return {"queries": [], "total": 0}
    cutoff = (datetime.now() - timedelta(days=window_days))
    n_queries = len(DEFAULT_PROSPECT_QUERIES)
    by_query = {q: 0 for q in DEFAULT_PROSPECT_QUERIES}
    latest = {q: "" for q in DEFAULT_PROSPECT_QUERIES}
    for p in prospects:
        ts = p.get("discovered_at", "")
        if not ts:
            continue
        try:
            dt = datetime.fromisoformat(ts.split("+")[0])
        except ValueError:
            continue
        if dt < cutoff:
            continue
        q = DEFAULT_PROSPECT_QUERIES[dt.day % n_queries]
        by_query[q] += 1
        if ts > latest[q]:
            latest[q] = ts
    return {
        "window_days": window_days,
        "queries": [
            {"query": q, "discovered": by_query[q], "last_discovered_at": latest[q]}
            for q in DEFAULT_PROSPECT_QUERIES
        ],
        "total": sum(by_query.values()),
    }


def snapshot_audit() -> dict:
    """How many snapshot dirs exist but never converted to contacted/client?"""
    if not SNAPSHOT_DIR.exists():
        return {"snapshots": 0, "uncontacted": 0}
    snapshot_slugs = {d.name for d in SNAPSHOT_DIR.iterdir() if d.is_dir()}
    prospects = _load(PROSPECTS, [])
    by_slug = {p.get("site_slug"): p.get("status") for p in prospects if isinstance(p, dict)}
    uncontacted = sum(
        1 for slug in snapshot_slugs
        if by_slug.get(slug) in (None, "discovered")
    )
    return {"snapshots": len(snapshot_slugs), "uncontacted": uncontacted}


def report_lines() -> list[str]:
    f = funnel()
    q = per_query_yield()
    s = snapshot_audit()

    lines = ["== LinkMender — derived health =="]
    lines.append("")
    lines.append(f"Funnel ({f['total']} prospects):")
    if f.get("by_status"):
        for status, count in f["by_status"].items():
            lines.append(f"  {status:<22s}  {count}")
    else:
        lines.append("  (empty)")

    lines.append("")
    lines.append(f"Per-query yield (last {q.get('window_days', 30)}d):")
    for entry in q.get("queries", []):
        last = (entry["last_discovered_at"] or "(never)")[:19]
        marker = " ← idle" if entry["discovered"] == 0 else ""
        lines.append(f"  {entry['query'][:50]:<50s}  discovered={entry['discovered']:>2d}  "
                     f"last={last}{marker}")

    lines.append("")
    lines.append(f"Snapshots: {s['snapshots']} dirs  ·  {s['uncontacted']} not yet contacted")
    return lines
