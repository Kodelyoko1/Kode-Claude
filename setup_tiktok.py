"""
TikTok OAuth setup — run once to get your access token.

Steps:
  1. python3 setup_tiktok.py
  2. Open the printed URL on your phone / browser
  3. Log in with your TikTok account and tap Authorize
  4. TikTok redirects you — copy the FULL URL from your browser's address bar
  5. Paste that URL (or just the `code=...` value) here when prompted
  6. Token is saved to .env automatically

Requires: TIKTOK_CLIENT_KEY and TIKTOK_CLIENT_SECRET in .env
"""

import hashlib
import os
import re
import secrets
import urllib.parse
from pathlib import Path

ENV_FILE = Path(".env")

# TikTok OAuth endpoints
AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"

# Required scope for video posting
SCOPE = "video.publish,video.upload"

# Redirect URI — TikTok requires one; user copies the redirect URL manually
REDIRECT_URI = "https://www.tiktok.com/"


def _load_env(key: str) -> str:
    val = os.getenv(key, "")
    if val:
        return val
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def _save_token(token: str, refresh: str = "") -> None:
    if not ENV_FILE.exists():
        ENV_FILE.write_text("")
    content = ENV_FILE.read_text()
    lines = content.splitlines()

    def _upsert(lines: list, key: str, value: str) -> list:
        for i, line in enumerate(lines):
            if line.startswith(f"{key}=") or line.startswith(f"# {key}="):
                lines[i] = f"{key}={value}"
                return lines
        lines.append(f"{key}={value}")
        return lines

    lines = _upsert(lines, "TIKTOK_ACCESS_TOKEN", token)
    if refresh:
        lines = _upsert(lines, "TIKTOK_REFRESH_TOKEN", refresh)
    ENV_FILE.write_text("\n".join(lines) + "\n")
    print(f"\n  Saved to {ENV_FILE}")


def _exchange_code(client_key: str, client_secret: str, code: str, verifier: str) -> dict:
    try:
        import requests
    except ImportError:
        return {"error": "pip install requests"}

    data = {
        "client_key": client_key,
        "client_secret": client_secret,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": REDIRECT_URI,
        "code_verifier": verifier,
    }
    r = requests.post(TOKEN_URL, data=data, timeout=30)
    try:
        return r.json()
    except Exception:
        return {"error": f"HTTP {r.status_code}: {r.text[:300]}"}


def main() -> None:
    client_key = _load_env("TIKTOK_CLIENT_KEY")
    client_secret = _load_env("TIKTOK_CLIENT_SECRET")

    if not client_key or not client_secret:
        print("ERROR: Set TIKTOK_CLIENT_KEY and TIKTOK_CLIENT_SECRET in .env first.")
        return

    # PKCE
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = (
        __import__("base64")
        .urlsafe_b64encode(digest)
        .rstrip(b"=")
        .decode()
    )

    state = secrets.token_hex(8)
    params = {
        "client_key": client_key,
        "scope": SCOPE,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    url = AUTH_URL + "?" + urllib.parse.urlencode(params)

    print("\n" + "=" * 60)
    print("  TikTok Authorization")
    print("=" * 60)
    print("\nStep 1 — Open this URL on your phone or computer:\n")
    print(f"  {url}\n")
    print("Step 2 — Log in with your TikTok account and tap Authorize.\n")
    print("Step 3 — TikTok will redirect you. Copy the FULL URL from")
    print("         your browser address bar and paste it below.\n")
    print("         (The URL starts with  https://www.tiktok.com/?code=...)\n")
    print("=" * 60)

    raw = input("Paste the full redirect URL (or just the code= value): ").strip()

    # Extract code from URL or raw value
    code = raw
    if "code=" in raw:
        match = re.search(r"[?&]code=([^&]+)", raw)
        if match:
            code = urllib.parse.unquote(match.group(1))

    if not code:
        print("No code found — aborting.")
        return

    print(f"\n  Exchanging code for access token…")
    resp = _exchange_code(client_key, client_secret, code, verifier)

    if "error" in resp:
        print(f"\n  ERROR: {resp['error']}")
        desc = resp.get("error_description", "")
        if desc:
            print(f"  {desc}")
        return

    access_token = resp.get("data", {}).get("access_token") or resp.get("access_token")
    refresh_token = resp.get("data", {}).get("refresh_token") or resp.get("refresh_token", "")

    if not access_token:
        print(f"\n  ERROR: No access_token in response: {resp}")
        return

    _save_token(access_token, refresh_token)

    print("\n  ✓ TikTok authorized!")
    print("  Your videos will now post automatically with --tiktok.")
    print("\n  Run:  python3 run_videoeditor_auto.py --youtube URL --tiktok")


if __name__ == "__main__":
    main()
