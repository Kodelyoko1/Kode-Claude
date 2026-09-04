"""
Killzone / session-timing rules for the ICT Predictor (Gold, Crude, and FX).

ICT killzones are defined in NEW YORK local time (London 02:00-05:00 NY,
NY AM 07:00-10:00 NY) because they track when those desks are actually
active. New York observes daylight saving, so the equivalent UTC hours SHIFT
BY AN HOUR twice a year:

    winter (EST, UTC-5)   London 07:00-10:00 UTC   NY AM 12:00-15:00 UTC
    summer (EDT, UTC-4)   London 06:00-09:00 UTC   NY AM 11:00-14:00 UTC

This module previously hardcoded the winter numbers, which made every
summer session run an hour late - the agent sat out the first hour of the
real window and kept scanning for an hour after it closed.

Windows are now resolved through the America/New_York zone. Set
IP_KILLZONE_TZ=utc to restore the old fixed-UTC behaviour (the literal
reading of the original spec's EST/UTC table).

NOTE FOR WINDOWS: zoneinfo needs the `tzdata` package there - Linux and
macOS ship a system tz database, Windows does not. It is in requirements.txt.
If it is missing the module falls back to fixed UTC rather than crashing,
and killzone_mode() reports which is in effect.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import os

from ict_predictor import instruments

# Windows in NEW YORK local hours, end exclusive. These are the definition;
# the UTC equivalents are derived per-date so DST is handled automatically.
LONDON_KILLZONE_NY = (2, 5)
NY_AM_KILLZONE_NY = (7, 10)

# Fixed-UTC fallback / opt-out: the winter equivalents, i.e. the literal
# reading of the original spec's EST table.
LONDON_KILLZONE = (7, 10)
NY_AM_KILLZONE = (12, 15)

KILLZONES = {
    "London Killzone": LONDON_KILLZONE,
    "NY AM Killzone": NY_AM_KILLZONE,
}

_NY_ZONE = None
_TZ_MODE = "utc"
if os.getenv("IP_KILLZONE_TZ", "ny").strip().lower() != "utc":
    try:
        from zoneinfo import ZoneInfo
        _NY_ZONE = ZoneInfo("America/New_York")
        _TZ_MODE = "ny"
    except Exception:
        # Missing tzdata (typical on Windows without the pip package).
        _NY_ZONE, _TZ_MODE = None, "utc-fallback"


def killzone_mode() -> str:
    """'ny' (DST-aware), 'utc' (opted out), or 'utc-fallback' (tzdata missing)."""
    return _TZ_MODE


def _ny_hour(now: datetime) -> Optional[int]:
    """Hour of day in New York, or None when the zone is unavailable."""
    if _NY_ZONE is None:
        return None
    return now.astimezone(_NY_ZONE).hour


def killzone_windows_utc(now: Optional[datetime] = None) -> dict:
    """The UTC windows in effect for `now`'s date — for display/debugging."""
    now = now or datetime.now(timezone.utc)
    if _NY_ZONE is None:
        return dict(KILLZONES)
    off = int(-now.astimezone(_NY_ZONE).utcoffset().total_seconds() // 3600)
    return {
        "London Killzone": ((LONDON_KILLZONE_NY[0] + off) % 24,
                            (LONDON_KILLZONE_NY[1] + off) % 24),
        "NY AM Killzone": ((NY_AM_KILLZONE_NY[0] + off) % 24,
                           (NY_AM_KILLZONE_NY[1] + off) % 24),
    }

# Which assets are "ideal" for each killzone, derived from each instrument's
# `killzones` entry in ict_predictor.instruments (single source of truth —
# see that module to add a new instrument or change its session fit).
ASSET_KILLZONE_FIT: dict[str, set] = {}
for _asset, _meta in instruments.INSTRUMENTS.items():
    for _kz in _meta.get("killzones", ()):
        ASSET_KILLZONE_FIT.setdefault(_kz, set()).add(_asset)


def _in_window(hour: int, window: tuple[int, int]) -> bool:
    start, end = window
    return start <= hour < end


def current_killzone(now: Optional[datetime] = None) -> Optional[str]:
    """Return the active killzone name, or None if outside all killzones.

    Evaluated in New York local time when available, so the window tracks
    the trading desks rather than drifting an hour with daylight saving.
    """
    now = now or datetime.now(timezone.utc)
    ny = _ny_hour(now)
    if ny is not None:
        if _in_window(ny, LONDON_KILLZONE_NY):
            return "London Killzone"
        if _in_window(ny, NY_AM_KILLZONE_NY):
            return "NY AM Killzone"
        return None
    hour = now.astimezone(timezone.utc).hour
    for name, window in KILLZONES.items():
        if _in_window(hour, window):
            return name
    return None


def asset_active_in_killzone(asset: str, killzone_name: Optional[str]) -> bool:
    """Is `asset` eligible to be evaluated in the given killzone? Falls back
    to instruments.get()'s default (both killzones) for an asset that isn't
    in the registry, rather than silently excluding it."""
    if not killzone_name:
        return False
    if asset in ASSET_KILLZONE_FIT.get(killzone_name, set()):
        return True
    if asset not in instruments.INSTRUMENTS:
        return killzone_name in instruments.get(asset).get("killzones", set())
    return False


def est_label(now: Optional[datetime] = None) -> str:
    """Fixed UTC-5 EST clock label for display (matches the spec's own table)."""
    now = now or datetime.now(timezone.utc)
    from datetime import timedelta
    est = now.astimezone(timezone.utc) - timedelta(hours=5)
    return est.strftime("%H:%M EST")


def next_killzone_open(now: Optional[datetime] = None) -> str:
    """Human-readable note on when the next killzone opens, for NO-TRADE reports.
    Uses the windows actually in effect today, so it stays correct across DST."""
    now = now or datetime.now(timezone.utc)
    hour = now.astimezone(timezone.utc).hour
    opens = sorted(w[0] for w in killzone_windows_utc(now).values())
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
