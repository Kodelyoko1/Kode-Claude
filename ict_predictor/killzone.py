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


# ---------------------------------------------------------------------------
# Market session (separate concern from killzones)
# ---------------------------------------------------------------------------
# Spot gold and FX trade continuously from Sunday evening to Friday evening,
# then stop. A killzone check alone does not catch this: 13:00 UTC on a
# Saturday still "is" the NY AM window by clock arithmetic, so without this
# the agent will analyse Friday's stale closing bars all weekend and emit
# entries against a market that cannot fill them.
#
# Boundaries are the widely-used retail convention (approximately New York
# 17:00 close / open, expressed in UTC). Brokers differ by an hour or two
# around the edges and holidays are not modelled, so this is deliberately
# conservative: it errs toward calling the market closed near the boundary
# rather than issuing an order that would sit unfilled.
MARKET_CLOSE_DOW = 4      # Friday
MARKET_CLOSE_HOUR = 21    # 21:00 UTC Friday
MARKET_OPEN_DOW = 6       # Sunday
MARKET_OPEN_HOUR = 22     # 22:00 UTC Sunday


def market_is_open(now: Optional[datetime] = None) -> bool:
    """True when the spot gold / FX market is trading."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    dow, hour = now.weekday(), now.hour
    if dow == 5:                                     # all Saturday
        return False
    if dow == MARKET_CLOSE_DOW and hour >= MARKET_CLOSE_HOUR:
        return False
    if dow == MARKET_OPEN_DOW and hour < MARKET_OPEN_HOUR:
        return False
    return True


def market_status(now: Optional[datetime] = None) -> str:
    """Human-readable reason, for NO TRADE reports."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if market_is_open(now):
        return "open"
    from datetime import timedelta
    nxt = now
    for _ in range(24 * 8):
        nxt += timedelta(hours=1)
        if market_is_open(nxt):
            return (f"closed for the weekend — reopens "
                    f"{nxt:%a %Y-%m-%d %H:00 UTC}")
    return "closed"
