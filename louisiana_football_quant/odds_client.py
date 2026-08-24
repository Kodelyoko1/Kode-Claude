"""
Read-only odds feed for the licensed Louisiana sportsbook consensus
(DraftKings, FanDuel, BetMGM) via The Odds API — https://the-odds-api.com/.

This module ONLY reads publicly-quoted lines. There is no vendor API for
submitting a wager into a personal DraftKings/FanDuel/BetMGM account — those
platforms explicitly prohibit automated/bot wagering in their terms of
service, and no legitimate "execution" endpoint exists for them. This agent
therefore never attempts to place bets on those books; see report.py and
tools.py for the signal-only output this feeds.

Requires ODDS_API_KEY (free tier available at the-odds-api.com). Every call
degrades gracefully (returns [] / {} with an "error" field) when the key is
missing, exhausted, or the API is unreachable — matches the rest of this
repo's resilience convention (self_healing classifies network failures,
callers must never assume a non-empty result).
"""
from __future__ import annotations

import os
from typing import Optional

import requests

ODDS_API_BASE = "https://api.the-odds-api.com/v4"

# The Odds API's sport keys for the two leagues this agent covers.
SPORT_KEYS = {
    "nfl": "americanfootball_nfl",
    "cfb": "americanfootball_ncaaf",
}

# Bookmaker keys The Odds API uses for the three books licensed to operate
# in Louisiana that this agent is scoped to (LQ_SPORTSBOOKS overrides).
DEFAULT_BOOKMAKERS = ["draftkings", "fanduel", "betmgm"]

_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": "WholesaleOmniverse-LAFootballQuant/1.0"})


def _api_key() -> str:
    return os.environ.get("ODDS_API_KEY", "").strip()


def configured_bookmakers() -> list[str]:
    raw = os.environ.get("LQ_SPORTSBOOKS", ",".join(DEFAULT_BOOKMAKERS))
    return [b.strip().lower() for b in raw.split(",") if b.strip()]


def configured_sports() -> list[str]:
    """LQ_SPORTS as a comma list of 'nfl'/'cfb' keys (default both)."""
    raw = os.environ.get("LQ_SPORTS", "nfl,cfb")
    return [s.strip().lower() for s in raw.split(",") if s.strip() in SPORT_KEYS]


def _get(path: str, params: dict) -> dict:
    """GET against The Odds API. Returns {"ok": True, "data": ...} or
    {"ok": False, "error": "..."} — never raises (self_healing still catches
    the rare unclassified exception, but callers can branch on "ok" directly
    without a try/except at every call site)."""
    key = _api_key()
    if not key:
        return {"ok": False, "error": "ODDS_API_KEY not set — sportsbook feed disabled"}
    q = {**params, "apiKey": key}
    try:
        resp = _SESSION.get(f"{ODDS_API_BASE}{path}", params=q, timeout=20)
        if resp.status_code == 401:
            return {"ok": False, "error": "ODDS_API_KEY rejected (401)"}
        if resp.status_code == 429:
            return {"ok": False, "error": "The Odds API quota exhausted (429)"}
        resp.raise_for_status()
        remaining = resp.headers.get("x-requests-remaining")
        return {"ok": True, "data": resp.json(), "requests_remaining": remaining}
    except requests.RequestException as exc:
        return {"ok": False, "error": f"network error: {exc}"}


def fetch_game_odds(
    league: str,
    markets: tuple[str, ...] = ("h2h", "spreads", "totals"),
    regions: str = "us",
    bookmakers: Optional[list[str]] = None,
) -> dict:
    """Fetch consensus odds for every upcoming event in a league, scoped to
    the configured sportsbooks. `league` is 'nfl' or 'cfb'."""
    sport_key = SPORT_KEYS.get(league)
    if not sport_key:
        return {"ok": False, "error": f"unknown league {league!r}"}
    books = bookmakers if bookmakers is not None else configured_bookmakers()
    return _get(f"/sports/{sport_key}/odds", {
        "regions": regions,
        "markets": ",".join(markets),
        "bookmakers": ",".join(books),
        "oddsFormat": "american",
    })


def list_upcoming_events(league: str, limit: int = 8) -> dict:
    """Lightweight event list (no odds) — used to pick a handful of events
    to expand into player-prop calls without burning the whole API quota."""
    sport_key = SPORT_KEYS.get(league)
    if not sport_key:
        return {"ok": False, "error": f"unknown league {league!r}"}
    result = _get(f"/sports/{sport_key}/events", {})
    if result.get("ok"):
        result["data"] = (result["data"] or [])[:limit]
    return result


def fetch_player_props(
    league: str,
    event_id: str,
    markets: tuple[str, ...],
    regions: str = "us",
    bookmakers: Optional[list[str]] = None,
) -> dict:
    """Best-effort per-event player-prop odds. The Odds API gates player
    props behind a higher plan tier than game lines — a 401/422 here just
    means "no comparable sportsbook line," not a hard failure, so callers
    should treat {"ok": False} as "skip this comparison," not as an error
    worth surfacing to the owner."""
    sport_key = SPORT_KEYS.get(league)
    if not sport_key:
        return {"ok": False, "error": f"unknown league {league!r}"}
    books = bookmakers if bookmakers is not None else configured_bookmakers()
    return _get(f"/sports/{sport_key}/events/{event_id}/odds", {
        "regions": regions,
        "markets": ",".join(markets),
        "bookmakers": ",".join(books),
        "oddsFormat": "american",
    })
