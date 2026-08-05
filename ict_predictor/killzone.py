"""
Killzone / session-timing rules for the ICT Gold & Crude Prediction Agent.

Windows are pinned to fixed UTC hours (matching the EST anchoring given in
the strategy spec: EST = UTC-5, so these windows are correct as written
regardless of US daylight-saving — during EDT the wall-clock EST labels
below run an hour "late" relative to New York local time, which is the
same trade-off the spec's own EST/UTC table makes).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

# (start_hour_utc, end_hour_utc) — end exclusive
LONDON_KILLZONE = (7, 10)
NY_AM_KILLZONE = (12, 15)

KILLZONES = {
    "London Killzone": LONDON_KILLZONE,
    "NY AM Killzone": NY_AM_KILLZONE,
}

# Which assets the spec treats as "ideal" for each killzone.
ASSET_KILLZONE_FIT = {
    "London Killzone": {"GC"},
    "NY AM Killzone": {"GC", "CL"},
}


def _in_window(hour: int, window: tuple[int, int]) -> bool:
    start, end = window
    return start <= hour < end


def current_killzone(now: Optional[datetime] = None) -> Optional[str]:
    """Return the active killzone name, or None if outside all killzones."""
    now = now or datetime.now(timezone.utc)
    hour = now.astimezone(timezone.utc).hour
    for name, window in KILLZONES.items():
        if _in_window(hour, window):
            return name
    return None


def asset_active_in_killzone(asset: str, killzone_name: Optional[str]) -> bool:
    """Is `asset` (GC/CL) eligible to be evaluated in the given killzone?"""
    if not killzone_name:
        return False
    return asset in ASSET_KILLZONE_FIT.get(killzone_name, set())


def est_label(now: Optional[datetime] = None) -> str:
    """Fixed UTC-5 EST clock label for display (matches the spec's own table)."""
    now = now or datetime.now(timezone.utc)
    from datetime import timedelta
    est = now.astimezone(timezone.utc) - timedelta(hours=5)
    return est.strftime("%H:%M EST")


def next_killzone_open(now: Optional[datetime] = None) -> str:
    """Human-readable note on when the next killzone opens, for NO-TRADE reports."""
    now = now or datetime.now(timezone.utc)
    hour = now.astimezone(timezone.utc).hour
    opens = sorted(w[0] for w in KILLZONES.values())
    for h in opens:
        if hour < h:
            return f"{h:02d}:00 UTC"
    return f"{opens[0]:02d}:00 UTC (next day)"
