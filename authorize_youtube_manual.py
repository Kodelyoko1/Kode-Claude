#!/usr/bin/env python3
"""
Manual YouTube OAuth — paste-the-URL flow. Works on any machine, no callback
server, no network setup needed.

Flow:
  1. Script prints a Google auth URL.
  2. You copy the URL into ANY browser, log in, click Allow.
  3. Browser redirects to a 'localhost' page that LOOKS broken.
  4. You copy the entire URL from the browser's address bar.
  5. You paste it back into this script.
  6. Script saves the refresh token. Done.
"""
import json
import sys
import urllib.parse
from pathlib import Path

DATA = Path(__file__).parent / "data"
CLIENT_SECRETS = DATA / "yt_client_secrets.json"
TOKEN_PATH = DATA / "yt_token.json"
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def main():
    try:
        from google_auth_oauthlib.flow import Flow
    except ImportError:
        print("Run: pip install --break-system-packages google-auth-oauthlib")
        sys.exit(1)

    if not CLIENT_SECRETS.exists():
        print(f"✗ Missing: {CLIENT_SECRETS}")
        sys.exit(1)

    # Use the Flow class directly with a fixed redirect URI we never bind to.
    # Google requires this URI to be registered in the OAuth client.
    # http://localhost is auto-allowed for Desktop apps.
    REDIRECT_URI = "http://localhost"

    flow = Flow.from_client_secrets_file(str(CLIENT_SECRETS), scopes=SCOPES,
                                          redirect_uri=REDIRECT_URI)

    auth_url, _ = flow.authorization_url(
        access_type="offline",
        prompt="consent",  # always get a refresh token
        include_granted_scopes="true",
    )

    print()
    print("=" * 72)
    print(" STEP 1 — Open this URL in ANY browser (your phone works too):")
    print("=" * 72)
    print()
    print(auth_url)
    print()
    print("=" * 72)
    print(" STEP 2 — Log in to your YouTube channel, click Continue → Allow.")
    print("=" * 72)
    print()
    print(" STEP 3 — Your browser will land on a page that says")
    print("          'This site can't be reached' or similar — that is fine.")
    print("          Look at the URL BAR in your browser. It will contain")
    print("          something like:")
    print()
    print("          http://localhost/?state=xxx&code=4/0AQSTgQH...&scope=...")
    print()
    print("=" * 72)
    print(" STEP 4 — Copy the ENTIRE URL from the browser address bar")
    print("          and paste it below.")
    print("=" * 72)
    print()

    redirect_url = input("Paste the URL here: ").strip()

    # Extract the authorization code from the redirect URL
    parsed = urllib.parse.urlparse(redirect_url)
    params = urllib.parse.parse_qs(parsed.query)
    code = params.get("code", [None])[0]
    if not code:
        print("✗ No 'code' found in that URL. Make sure you copied the full URL.")
        sys.exit(1)

    try:
        flow.fetch_token(code=code)
    except Exception as e:
        print(f"✗ Token exchange failed: {e}")
        sys.exit(1)

    creds = flow.credentials
    TOKEN_PATH.write_text(creds.to_json())

    print()
    print(f"✓ Token saved to {TOKEN_PATH}")
    print(f"  refresh_token present: {bool(creds.refresh_token)}")
    print()
    print("Now you can run:  ./vr 'YOUR_VIRAL_URL'")


if __name__ == "__main__":
    main()
