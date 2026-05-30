"""
Meta token storage + refresh.

Two token shapes the agent uses:
- System User access token (preferred): non-expiring, scoped to one Business
  Manager + ad account. Stored in env as META_ACCESS_TOKEN. No refresh needed.
- User OAuth token (fallback for early dev): 60-day long-lived. We persist these
  to data/media_buyer/tokens.json and refresh proactively at T-7d.

Webhook subscriptions and CAPI calls use the System User token; we never need
to ship a refresh code path for it. The user-token refresh exists for completeness
when running this in dev against a personal account before BM/System-User setup.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, asdict
from pathlib import Path

import requests

from .config import DATA_DIR

_TOKEN_FILE = DATA_DIR / "tokens.json"
_GRAPH_VERSION = os.getenv("MB_GRAPH_VERSION", "v19.0")
_REFRESH_WINDOW_SECS = 7 * 24 * 3600  # refresh if <7 days remain on a user token


@dataclass
class StoredToken:
    kind: str            # "system_user" | "user_long_lived"
    access_token: str
    expires_at: int = 0  # unix seconds; 0 = non-expiring (system user)
    scopes: str = ""


def _read_all() -> dict[str, dict]:
    if not _TOKEN_FILE.exists():
        return {}
    try:
        return json.loads(_TOKEN_FILE.read_text())
    except json.JSONDecodeError:
        return {}


def _write_all(state: dict[str, dict]) -> None:
    _TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    _TOKEN_FILE.write_text(json.dumps(state, indent=2))


def save(key: str, tok: StoredToken) -> None:
    state = _read_all()
    state[key] = asdict(tok)
    _write_all(state)


def load(key: str) -> StoredToken | None:
    raw = _read_all().get(key)
    if not raw:
        return None
    return StoredToken(**raw)


def refresh_long_lived_user_token(short_lived_token: str) -> StoredToken:
    """Exchange a short-lived user token for a 60-day long-lived one."""
    app_id = os.environ["META_APP_ID"]
    app_secret = os.environ["META_APP_SECRET"]
    r = requests.get(
        f"https://graph.facebook.com/{_GRAPH_VERSION}/oauth/access_token",
        params={
            "grant_type": "fb_exchange_token",
            "client_id": app_id,
            "client_secret": app_secret,
            "fb_exchange_token": short_lived_token,
        },
        timeout=15,
    )
    r.raise_for_status()
    data = r.json()
    return StoredToken(
        kind="user_long_lived",
        access_token=data["access_token"],
        expires_at=int(time.time()) + int(data.get("expires_in", 60 * 24 * 3600)),
        scopes=data.get("scope", ""),
    )


def get_active_token() -> str:
    """Return whichever token is configured. System User takes precedence.

    Refreshes the long-lived user token if it's within the refresh window.
    """
    sys_tok = os.getenv("META_ACCESS_TOKEN")
    if sys_tok:
        return sys_tok

    stored = load("primary_user")
    if not stored:
        raise RuntimeError(
            "No META_ACCESS_TOKEN env var and no stored user token. "
            "Set up a System User token in Business Manager and export META_ACCESS_TOKEN."
        )
    remaining = stored.expires_at - int(time.time())
    if 0 < remaining < _REFRESH_WINDOW_SECS:
        # Long-lived user tokens are extended by re-exchanging the existing one.
        try:
            renewed = refresh_long_lived_user_token(stored.access_token)
            save("primary_user", renewed)
            stored = renewed
        except requests.HTTPError:
            # Don't crash the caller — the existing token is still valid for now.
            pass
    return stored.access_token
