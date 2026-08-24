"""
Read-only PrizePicks projections feed.

Hits PrizePicks' own public JSON endpoint (the same one prizepicks.com's web
client calls) — same technique as hudscout/tools.py against the HUD JSON
endpoint: no auth, no key, just the site's own API. This is a DFS pick'em
product, not a sportsbook — Louisiana treats fantasy-sports contests as a
separate regulatory track from sports wagering, but the automation boundary
here is identical either way: PrizePicks has no vendor API for submitting an
entry into a personal account, and doing so via bot automation would violate
its terms of service. This module only ever reads publicly-quoted lines for
+EV analysis; see tools.py for why no auto-entry path exists.

Endpoint contract drifts occasionally (PrizePicks has changed field names and
added anti-bot headers before) — every parse step is defensive and a schema
change degrades to an empty list rather than crashing the cycle, matching
this repo's resilience convention (see HUDScout's note on `_normalize_property`).
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

import requests

PP_BASE = "https://api.prizepicks.com"

_SESSION = requests.Session()
_SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json",
})

# PrizePicks league display names this agent cares about (matched
# case-insensitively against the `league` included resource).
DEFAULT_LEAGUES = ["NFL", "CFB"]


def configured_leagues() -> list[str]:
    raw = os.environ.get("LQ_PRIZEPICKS_LEAGUES", ",".join(DEFAULT_LEAGUES))
    return [l.strip().upper() for l in raw.split(",") if l.strip()]


def fetch_projections(per_page: int = 250) -> dict:
    """Fetch all live projections and normalize into flat dicts.

    Returns {"ok": True, "projections": [...]} or {"ok": False, "error": "..."}.
    Never raises — an unreachable endpoint or a JSON:API shape change both
    degrade to an empty, clearly-labeled result.
    """
    try:
        resp = _SESSION.get(f"{PP_BASE}/projections", params={
            "per_page": per_page,
            "single_stat": "true",
        }, timeout=20)
        if resp.status_code != 200:
            return {"ok": False, "error": f"HTTP {resp.status_code}", "projections": []}
        payload = resp.json()
    except (requests.RequestException, ValueError) as exc:
        return {"ok": False, "error": str(exc), "projections": []}

    try:
        return {"ok": True, "projections": _normalize(payload)}
    except Exception as exc:
        # Endpoint shape drifted — surface it, don't crash the cycle.
        return {"ok": False, "error": f"parse failed: {exc}", "projections": []}


def _normalize(payload: dict) -> list[dict]:
    included = payload.get("included") or []
    players = {i["id"]: i.get("attributes", {}) for i in included if i.get("type") == "new_player"}
    leagues = {i["id"]: i.get("attributes", {}) for i in included if i.get("type") == "league"}
    wanted = set(configured_leagues())

    out = []
    for item in payload.get("data") or []:
        if item.get("type") != "projection":
            continue
        attrs = item.get("attributes", {}) or {}
        rels = item.get("relationships", {}) or {}

        league_id = ((rels.get("league") or {}).get("data") or {}).get("id")
        league_name = (leagues.get(league_id, {}) or {}).get("name", "")
        if wanted and league_name.upper() not in wanted:
            continue

        player_id = ((rels.get("new_player") or {}).get("data") or {}).get("id")
        player = players.get(player_id, {}) or {}

        line = attrs.get("line_score")
        if line is None:
            continue

        out.append({
            "player":       player.get("name", "Unknown"),
            "team":         player.get("team", ""),
            "position":     player.get("position", ""),
            "league":       league_name,
            "stat_type":    attrs.get("stat_type", ""),
            "line":         float(line),
            "start_time":   attrs.get("start_time", ""),
            "odds_type":    attrs.get("odds_type", "standard"),   # standard | demon | goblin
            "is_promo":     bool(attrs.get("is_promo", False)),
            "fetched_at":   datetime.now(timezone.utc).isoformat(),
        })
    return out
