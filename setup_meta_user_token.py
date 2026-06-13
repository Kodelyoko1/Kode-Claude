#!/usr/bin/env python3
"""
Swap the Business Manager System User token for a User OAuth token.

Why: System User tokens come from Business Manager and don't carry a
user's App-role. When `ads_management` is at Standard Access (the default
for Live-mode apps without App Review), Meta blocks adcreatives create
because the System User isn't an Admin/Developer/Tester on the App. A
User OAuth token from one of the App's Admin users sidesteps the check
entirely.

Workflow:
  1. Open https://developers.facebook.com/tools/explorer/?app=1696940914559912
  2. Top-right dropdown: select "Omni Sales" as the App.
  3. Click "Generate Access Token" → log in if needed → approve scopes:
       ads_management
       pages_manage_ads
       pages_read_engagement
       pages_show_list
       business_management
       ads_read
  4. Copy the short-lived token (starts with "EAA…").
  5. Run:
       python3 setup_meta_user_token.py <short_token>
     This exchanges it for a 60-day long-lived token, stores it in
     data/meta_tokens.json under key "primary_user", and prints next steps.
  6. Comment out META_ACCESS_TOKEN in .env (or delete the line).
     token_store.get_active_token() automatically falls back to the
     stored user token when the env var is unset.
"""
from __future__ import annotations
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

# Load .env so META_APP_ID + META_APP_SECRET are available for the exchange
if Path(".env").exists():
    for line in Path(".env").read_text().splitlines():
        if line.strip().startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    short = sys.argv[1].strip()
    if not short.startswith("EAA"):
        print(f"That doesn't look like a Meta token (expected EAA… prefix). Got: {short[:20]}…")
        return 2

    if not (os.environ.get("META_APP_ID") and os.environ.get("META_APP_SECRET")):
        print("META_APP_ID + META_APP_SECRET must be set in .env to do the exchange.")
        return 1

    from media_buyer.token_store import refresh_long_lived_user_token, save

    print("Exchanging short-lived token for 60-day long-lived…")
    renewed = refresh_long_lived_user_token(short)
    save("primary_user", renewed)

    days_left = (renewed.expires_at - int(time.time())) / 86400.0
    print()
    print(f"  Stored as 'primary_user'.")
    print(f"  Expires in:  {days_left:.0f} days")
    print(f"  Scopes:      {renewed.scopes or '(unknown)'}")
    print()
    print("Next steps:")
    print("  1. Comment out (or delete) the META_ACCESS_TOKEN line in .env so")
    print("     token_store falls back to the stored user token.")
    print("  2. Re-run:  python3 run_fbads_auto.py --launch --live --max 1")
    print("  3. Smoke test should now go end-to-end.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
