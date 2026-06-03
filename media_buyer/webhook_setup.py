"""
Webhook registration automation — the last mile between "campaign is live" and
"leads land in data/leads.json".

What Meta needs (two layers, both scriptable):
  1. App-level webhook config — tells Meta where to POST when a leadgen event
     happens for ANY page that uses this app:
       POST /{app_id}/subscriptions
       body: object=page, callback_url, fields=leadgen, verify_token, include_values
       auth: app access token = "{app_id}|{app_secret}"
  2. Page-level subscription — tells Meta that THIS specific page wants leadgen
     events forwarded to the app:
       POST /{page_id}/subscribed_apps
       body: subscribed_fields=leadgen
       auth: page-scoped token

We also verify the FastAPI ingestion server's challenge endpoint actually
echoes Meta's hub.challenge before registering — Meta refuses the subscription
otherwise.

This module DOES NOT respect DRY_RUN. Webhook registration is config, not spend,
and the owner is explicitly running `--register-webhook` to set it up.
Subscriptions are listed before any mutation so the owner sees the existing
state.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from typing import Optional
from urllib.parse import urljoin

import requests

from . import meta_api, token_store
from .config import PROFILES

log = logging.getLogger("media_buyer.webhook_setup")


# ─────────────────────────── Helpers ───────────────────────────

def _app_access_token() -> str:
    """Build the {app_id}|{app_secret} token Meta accepts for app-level config."""
    app_id = os.environ.get("META_APP_ID", "")
    secret = os.environ.get("META_APP_SECRET", "")
    if not (app_id and secret):
        raise RuntimeError("META_APP_ID + META_APP_SECRET required for webhook setup")
    return f"{app_id}|{secret}"


def _leadgen_callback_path() -> str:
    return "/webhooks/meta/leadgen"


def _join_url(base: str, path: str) -> str:
    """Normalize the public URL + callback path into a single Meta-facing URL.

    Accept both "https://x.com" and "https://x.com/webhooks/meta/leadgen" as inputs
    so the owner can paste either form into MB_WEBHOOK_PUBLIC_URL."""
    base = (base or "").rstrip("/")
    if not base:
        raise RuntimeError("MB_WEBHOOK_PUBLIC_URL must be set (https://<host>)")
    if base.endswith(path):
        return base
    return base + path


# ─────────────────────────── Read-side: state inspection ───────────────────────────

def list_app_subscriptions() -> list[dict]:
    """GET /{app_id}/subscriptions — what's the app currently subscribed to?"""
    app_id = os.environ["META_APP_ID"]
    r = requests.get(
        f"https://graph.facebook.com/{meta_api.GRAPH_VERSION}/{app_id}/subscriptions",
        params={"access_token": _app_access_token()}, timeout=15,
    )
    r.raise_for_status()
    return r.json().get("data", [])


def list_page_subscriptions(page_id: str) -> list[dict]:
    """GET /{page_id}/subscribed_apps — which apps does this page forward events to?"""
    page = meta_api._request("GET", f"/{page_id}", params={"fields": "access_token"})
    page_token = page.get("access_token")
    if not page_token:
        raise RuntimeError("Page-scoped token unavailable — need pages_manage_metadata scope")
    r = requests.get(
        f"https://graph.facebook.com/{meta_api.GRAPH_VERSION}/{page_id}/subscribed_apps",
        params={"access_token": page_token}, timeout=15,
    )
    r.raise_for_status()
    return r.json().get("data", [])


# ─────────────────────────── Local server test ───────────────────────────

def test_challenge_endpoint(public_url: str, verify_token: str) -> dict:
    """Hit GET {public_url}/webhooks/meta/leadgen with a fake Meta challenge.

    Meta's spec: GET ?hub.mode=subscribe&hub.challenge=<nonce>&hub.verify_token=<token>
    expects the server to echo the nonce in the response body.

    Returns {ok, status_code, expected, actual, error?}.
    """
    target = _join_url(public_url, _leadgen_callback_path())
    nonce = "wo_heal_check_42"
    try:
        r = requests.get(
            target,
            params={
                "hub.mode": "subscribe",
                "hub.challenge": nonce,
                "hub.verify_token": verify_token,
            },
            timeout=10,
        )
    except requests.RequestException as e:
        return {"ok": False, "error": f"could not reach {target}: {e}"}

    body = r.text.strip()
    ok = r.status_code == 200 and body == nonce
    return {
        "ok": ok,
        "status_code": r.status_code,
        "url": target,
        "expected": nonce,
        "actual": body[:200],
        "error": None if ok else (
            "verify_token mismatch (server returned 403) — set META_WEBHOOK_VERIFY_TOKEN "
            "on the server to match what's in your .env"
            if r.status_code == 403 else
            f"unexpected response: HTTP {r.status_code} body={body[:120]!r}"
        ),
    }


# ─────────────────────────── Write-side: subscribe ───────────────────────────

def subscribe_app_to_leadgen(public_url: str, verify_token: str) -> dict:
    """Configure the app's leadgen webhook. Idempotent — Meta updates if it exists."""
    app_id = os.environ["META_APP_ID"]
    callback = _join_url(public_url, _leadgen_callback_path())
    body = {
        "object": "page",
        "callback_url": callback,
        "fields": "leadgen",
        "include_values": "true",
        "verify_token": verify_token,
        "access_token": _app_access_token(),
    }
    r = requests.post(
        f"https://graph.facebook.com/{meta_api.GRAPH_VERSION}/{app_id}/subscriptions",
        data=body, timeout=20,
    )
    if r.status_code >= 400:
        try:
            err = r.json().get("error", {})
            msg = err.get("message") or r.text[:300]
        except (ValueError, AttributeError):
            msg = r.text[:300]
        raise RuntimeError(f"subscribe_app failed: HTTP {r.status_code}: {msg}")
    return {"ok": True, "callback_url": callback, "response": r.json()}


def subscribe_page_to_app(page_id: str) -> dict:
    """Subscribe this specific page to forward leadgen events to the app."""
    page = meta_api._request("GET", f"/{page_id}", params={"fields": "access_token"})
    page_token = page.get("access_token")
    if not page_token:
        raise RuntimeError("Page-scoped token unavailable — need pages_manage_metadata scope")
    r = requests.post(
        f"https://graph.facebook.com/{meta_api.GRAPH_VERSION}/{page_id}/subscribed_apps",
        data={"subscribed_fields": "leadgen", "access_token": page_token},
        timeout=20,
    )
    if r.status_code >= 400:
        try:
            err = r.json().get("error", {})
            msg = err.get("message") or r.text[:300]
        except (ValueError, AttributeError):
            msg = r.text[:300]
        raise RuntimeError(f"subscribe_page failed: HTTP {r.status_code}: {msg}")
    return {"ok": True, "page_id": page_id, "response": r.json()}


# ─────────────────────────── End-to-end ───────────────────────────

def register_full(*, public_url: Optional[str] = None,
                  verify_token: Optional[str] = None,
                  page_id: Optional[str] = None,
                  skip_challenge_test: bool = False) -> dict:
    """One-shot: test challenge → app subscription → page subscription → verify.

    Reads MB_WEBHOOK_PUBLIC_URL / META_WEBHOOK_VERIFY_TOKEN / META_PAGE_ID from
    env when args aren't provided."""
    public_url   = public_url   or os.environ.get("MB_WEBHOOK_PUBLIC_URL", "")
    verify_token = verify_token or os.environ.get("META_WEBHOOK_VERIFY_TOKEN", "")
    page_id      = page_id      or PROFILES["lead_gen"].page_id

    if not public_url:
        raise RuntimeError("MB_WEBHOOK_PUBLIC_URL not set — deploy the server first")
    if not verify_token:
        raise RuntimeError("META_WEBHOOK_VERIFY_TOKEN not set")
    if not page_id:
        raise RuntimeError("META_PAGE_ID not set")

    audit: dict = {"public_url": public_url, "page_id": page_id, "steps": []}

    # 1. Show current state so the owner sees what we're about to change
    try:
        existing_app = list_app_subscriptions()
        audit["steps"].append({"step": "list_app_subscriptions_before",
                                "result": existing_app})
    except Exception as e:
        audit["steps"].append({"step": "list_app_subscriptions_before",
                                "error": str(e)})

    try:
        existing_page = list_page_subscriptions(page_id)
        audit["steps"].append({"step": "list_page_subscriptions_before",
                                "result": existing_page})
    except Exception as e:
        audit["steps"].append({"step": "list_page_subscriptions_before",
                                "error": str(e)})

    # 2. Test the challenge endpoint — Meta will reject the subscription otherwise
    if not skip_challenge_test:
        chal = test_challenge_endpoint(public_url, verify_token)
        audit["steps"].append({"step": "test_challenge_endpoint", "result": chal})
        if not chal["ok"]:
            audit["aborted"] = (
                "Challenge test failed — Meta will refuse the subscription. "
                f"Reason: {chal.get('error')}. Fix the server before retrying.")
            return audit

    # 3. App-level subscription
    app_sub = subscribe_app_to_leadgen(public_url, verify_token)
    audit["steps"].append({"step": "subscribe_app", "result": app_sub})

    # 4. Page-level subscription
    page_sub = subscribe_page_to_app(page_id)
    audit["steps"].append({"step": "subscribe_page", "result": page_sub})

    # 5. Verify both subscriptions look correct now
    try:
        audit["steps"].append({"step": "list_app_subscriptions_after",
                                "result": list_app_subscriptions()})
    except Exception as e:
        audit["steps"].append({"step": "list_app_subscriptions_after",
                                "error": str(e)})
    try:
        audit["steps"].append({"step": "list_page_subscriptions_after",
                                "result": list_page_subscriptions(page_id)})
    except Exception as e:
        audit["steps"].append({"step": "list_page_subscriptions_after",
                                "error": str(e)})

    audit["ok"] = True
    return audit


# ─────────────────────────── How-to-deploy guide ───────────────────────────

DEPLOY_GUIDE = """\
The webhook server (run_media_buyer_server.py) needs a public URL Meta can POST to.
Three paths, pick whichever matches your stack:

1. CLOUDFLARED TUNNEL  (fastest for testing — no signup, no DNS)
   Install once:
     curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 \\
       -o /usr/local/bin/cloudflared && chmod +x /usr/local/bin/cloudflared
   Run the server + tunnel in two terminals:
     T1: uvicorn media_buyer.ingestion:app --host 0.0.0.0 --port 8000
     T2: cloudflared tunnel --url http://localhost:8000
   Tunnel prints a https://<random>.trycloudflare.com URL — paste that as
   MB_WEBHOOK_PUBLIC_URL in .env. (URL changes on each tunnel restart.)

2. RENDER / RAILWAY / FLY  (production)
   Deploy this repo as a web service. Start command:
     uvicorn media_buyer.ingestion:app --host 0.0.0.0 --port $PORT
   Required env vars on the host:
     META_APP_ID META_APP_SECRET META_ACCESS_TOKEN META_PAGE_ID
     META_AD_ACCOUNT_ID META_WEBHOOK_VERIFY_TOKEN ANTHROPIC_API_KEY
     (optional) TWILIO_ACCOUNT_SID TWILIO_AUTH_TOKEN MB_CRM_WEBHOOK_URL
   Use the deployed URL as MB_WEBHOOK_PUBLIC_URL.

3. NGROK  (similar to cloudflared but signup required for stable URLs)

After the server is reachable:
   python3 run_media_buyer_auto.py --test-webhook
   python3 run_media_buyer_auto.py --register-webhook
"""


def print_deploy_guide() -> None:
    print(DEPLOY_GUIDE)


# ─────────────────────────── CLI ───────────────────────────

def main() -> int:
    import argparse
    p = argparse.ArgumentParser(description="Media Buyer webhook setup")
    p.add_argument("--register", action="store_true",
                    help="Run the full registration flow")
    p.add_argument("--test", action="store_true",
                    help="Test the challenge endpoint only")
    p.add_argument("--list", action="store_true",
                    help="List current app + page subscriptions")
    p.add_argument("--guide", action="store_true",
                    help="Print the deploy guide for getting a public URL")
    args = p.parse_args()

    if args.guide or not (args.register or args.test or args.list):
        print_deploy_guide()
        return 0

    if args.list:
        try:
            print("App subscriptions:")
            print(json.dumps(list_app_subscriptions(), indent=2))
        except Exception as e:
            print(f"  error: {e}")
        try:
            print("\nPage subscriptions:")
            print(json.dumps(list_page_subscriptions(PROFILES["lead_gen"].page_id), indent=2))
        except Exception as e:
            print(f"  error: {e}")
        return 0

    if args.test:
        public_url = os.environ.get("MB_WEBHOOK_PUBLIC_URL", "")
        verify_token = os.environ.get("META_WEBHOOK_VERIFY_TOKEN", "")
        result = test_challenge_endpoint(public_url, verify_token)
        print(json.dumps(result, indent=2))
        return 0 if result["ok"] else 1

    if args.register:
        result = register_full()
        print(json.dumps(result, indent=2, default=str))
        return 0 if result.get("ok") else 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
