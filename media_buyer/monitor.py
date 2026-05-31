"""
Performance Analytics Engine — pulls Meta Insights at campaign/adset/ad levels,
derives the metrics the controller acts on, and persists a daily snapshot for
trend analysis (moving averages, frequency drift, etc.).
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, asdict
from datetime import datetime, UTC
from typing import Any

from . import meta_api
from .config import DATA_DIR, ProfileKind, profile_for

log = logging.getLogger("media_buyer.monitor")

SNAPSHOT_FILE = DATA_DIR / "insights_history.jsonl"


@dataclass
class Metrics:
    """Computed metrics for one object (campaign / adset / ad) at one snapshot."""
    level: str               # "campaign" | "adset" | "ad"
    object_id: str
    object_name: str
    spend: float
    impressions: int
    frequency: float
    # Universal top-funnel
    hook_rate: float         # 3-sec views / impressions
    hold_rate: float         # thruplays / impressions
    # Lead-gen
    leads: int
    cpl: float | None
    form_completion_rate: float | None
    # E-com
    purchases: int
    revenue: float
    roas: float | None
    cpp: float | None        # cost per purchase
    aov: float | None        # average order value


# ─────────────────────────── Action accessors ───────────────────────────
def _action_count(row: dict, action_type: str) -> int:
    """Find a specific action_type in Insights' `actions` array."""
    for a in row.get("actions", []) or []:
        if a.get("action_type") == action_type:
            try:
                return int(float(a.get("value", 0)))
            except (TypeError, ValueError):
                return 0
    return 0


def _action_value(row: dict, action_type: str) -> float:
    for a in row.get("action_values", []) or []:
        if a.get("action_type") == action_type:
            try:
                return float(a.get("value", 0))
            except (TypeError, ValueError):
                return 0.0
    return 0.0


def _video_metric(row: dict, key: str) -> int:
    """Sum across all action breakdowns for video_*_watched_actions arrays."""
    arr = row.get(key) or []
    total = 0
    for a in arr:
        try:
            total += int(float(a.get("value", 0)))
        except (TypeError, ValueError):
            continue
    return total


def compute_metrics(row: dict, level: str) -> Metrics:
    """Derive a Metrics from one Insights row.

    Lead-gen action_type: "lead" or "onsite_conversion.lead_grouped".
    E-com purchase action_type: "purchase" or "offsite_conversion.fb_pixel_purchase".
    """
    impressions = int(row.get("impressions") or 0)
    spend = float(row.get("spend") or 0)
    frequency = float(row.get("frequency") or 0)

    # 3-sec views come from actions[].video_view (Meta deprecated the dedicated
    # video_3_sec_watched_actions field). Thruplays still have a dedicated array.
    three_sec = _action_count(row, "video_view")
    thruplays = _video_metric(row, "video_thruplay_watched_actions")
    hook_rate = (three_sec / impressions) if impressions else 0.0
    hold_rate = (thruplays / impressions) if impressions else 0.0

    leads = _action_count(row, "lead") or _action_count(row, "onsite_conversion.lead_grouped")
    cpl = (spend / leads) if leads else None

    # Form completion rate = leads / link_clicks (best available proxy without
    # the Instant Form analytics endpoint).
    link_clicks = _action_count(row, "link_click")
    fcr = (leads / link_clicks) if link_clicks else None

    purchases = _action_count(row, "purchase") or _action_count(row, "offsite_conversion.fb_pixel_purchase")
    revenue = _action_value(row, "purchase") or _action_value(row, "offsite_conversion.fb_pixel_purchase")
    roas = (revenue / spend) if spend else None
    cpp = (spend / purchases) if purchases else None
    aov = (revenue / purchases) if purchases else None

    return Metrics(
        level=level,
        object_id=row.get(f"{level}_id") or row.get("ad_id") or "",
        object_name=row.get(f"{level}_name") or row.get("ad_name") or "",
        spend=spend,
        impressions=impressions,
        frequency=frequency,
        hook_rate=hook_rate,
        hold_rate=hold_rate,
        leads=leads,
        cpl=cpl,
        form_completion_rate=fcr,
        purchases=purchases,
        revenue=revenue,
        roas=roas,
        cpp=cpp,
        aov=aov,
    )


# ─────────────────────────── Daily sweep ───────────────────────────
def daily_sweep(kind: ProfileKind, date_preset: str = "last_3d") -> dict[str, list[Metrics]]:
    """Pull insights at all three levels and persist a snapshot.

    Returns {"campaigns": [...], "adsets": [...], "ads": [...]}.
    Levels are fetched independently so we can use them directly (Meta doesn't
    aggregate child metrics into parent rows when computing things like CPL).
    """
    profile = profile_for(kind=kind)
    out: dict[str, list[Metrics]] = {"campaigns": [], "adsets": [], "ads": []}

    for level, plural in (("campaign", "campaigns"), ("adset", "adsets"), ("ad", "ads")):
        rows = meta_api.get_insights(level=level, object_id=profile.ad_account_id,
                                     date_preset=date_preset)
        out[plural] = [compute_metrics(r, level) for r in rows]

    _persist_snapshot(kind, out)
    return out


def _persist_snapshot(kind: ProfileKind, sweep: dict[str, list[Metrics]]) -> None:
    SNAPSHOT_FILE.parent.mkdir(parents=True, exist_ok=True)
    rec = {
        "captured_at": datetime.now(UTC).isoformat(),
        "profile_kind": kind,
        "campaigns": [asdict(m) for m in sweep["campaigns"]],
        "adsets":    [asdict(m) for m in sweep["adsets"]],
        "ads":       [asdict(m) for m in sweep["ads"]],
    }
    with SNAPSHOT_FILE.open("a") as f:
        f.write(__import__("json").dumps(rec) + "\n")


def history_for(object_id: str, *, days: int = 7) -> list[dict]:
    """Pull one snapshot-per-day for the last `days` days for one object id.

    Multiple sweeps per day collapse to the latest one for that day, so manual
    re-runs don't double-count today in moving averages downstream.
    """
    if not SNAPSHOT_FILE.exists():
        return []
    import json
    from datetime import timezone, timedelta
    cutoff = datetime.now(timezone.utc).date() - timedelta(days=days - 1)
    by_day: dict[str, dict] = {}  # date_str -> latest snapshot for that day
    with SNAPSHOT_FILE.open() as f:
        for line in f:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            try:
                d = datetime.fromisoformat(rec["captured_at"].rstrip("Z")).date()
            except (ValueError, KeyError):
                continue
            if d < cutoff:
                continue
            for level_key in ("campaigns", "adsets", "ads"):
                for m in rec.get(level_key, []):
                    if m.get("object_id") == object_id:
                        by_day[d.isoformat()] = {"captured_at": rec["captured_at"], **m}
    return [by_day[k] for k in sorted(by_day)]


def moving_avg(history: list[dict], field: str, n: int = 3) -> float | None:
    """Last-N moving average for a numeric field; None if not enough samples."""
    vals = [h.get(field) for h in history if isinstance(h.get(field), (int, float))]
    if len(vals) < n:
        return None
    return sum(vals[-n:]) / n
