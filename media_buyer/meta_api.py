"""
Meta Graph API client — Insights reads + budget/ad mutations + CAPI + webhook
signature verification. One thin layer over requests so the rest of the agent
never touches HTTP directly.

Rate-limit handling: Meta returns 4-XX with X-Business-Use-Case-Usage and
X-Ad-Account-Usage headers carrying call-count percentages. When any bucket
crosses 95% we sleep for the suggested cool-down before the next call. On 429
or 5xx we exponential-backoff and retry up to MAX_RETRIES.

Every mutating method checks `config.DRY_RUN` and returns a dict describing what
WOULD have happened instead of making the call. The controller is the only code
that should call mutators; it's structured so a single DRY_RUN flip is the only
thing standing between simulation and live spend.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
from typing import Any, Iterable

import requests

from .config import DRY_RUN, MAX_ABSOLUTE_DAILY_BUDGET_USD
from . import token_store

log = logging.getLogger("media_buyer.meta_api")

GRAPH_VERSION = os.getenv("MB_GRAPH_VERSION", "v19.0")
BASE = f"https://graph.facebook.com/{GRAPH_VERSION}"
MAX_RETRIES = 5
RATE_LIMIT_PAUSE_SECS = 90  # how long to back off when a usage bucket is near max


# ─────────────────────────── Low-level request ───────────────────────────
def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"Accept": "application/json"})
    return s


_SESSION = _session()


def _parse_usage_headers(resp: requests.Response) -> int:
    """If any rate-limit bucket is above 95%, return seconds to sleep; else 0.

    Meta's three usage headers have inconsistent shapes:
    - x-business-use-case-usage: {"act_<id>": [{call_count, total_cputime, ...}, ...]}
    - x-app-usage:               {"call_count": N, "total_cputime": N, "total_time": N}
    - x-ad-account-usage:        {"acc_id_util_pct": N, "ads_api_access_tier": "..."}
    Walk anything we can find, ignore anything that doesn't look like a numeric bucket.
    """
    def walk(node: Any):
        """Yield (key, value) for every numeric leaf in a nested dict/list."""
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(v, (int, float)):
                    yield k, v
                else:
                    yield from walk(v)
        elif isinstance(node, list):
            for item in node:
                yield from walk(item)

    interesting_keys = {"call_count", "total_cputime", "total_time", "acc_id_util_pct"}
    for hdr in ("x-business-use-case-usage", "x-ad-account-usage", "x-app-usage"):
        raw = resp.headers.get(hdr)
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        for k, v in walk(payload):
            if k in interesting_keys and v >= 95:
                log.warning("Meta rate-limit bucket %s near max (%s=%s)", hdr, k, v)
                return RATE_LIMIT_PAUSE_SECS
    return 0


def _request(method: str, path: str, *, params: dict | None = None,
             data: dict | None = None, json_body: dict | None = None) -> dict:
    """One HTTP call with retry/backoff. Adds the access token automatically."""
    params = dict(params or {})
    params.setdefault("access_token", token_store.get_active_token())
    url = f"{BASE}{path}"

    delay = 1.0
    last_exc: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = _SESSION.request(method, url, params=params, data=data, json=json_body, timeout=30)
        except requests.RequestException as e:
            # Network/timeout — these are worth retrying.
            last_exc = e
            log.warning("Meta network error on %s: %s (attempt %d/%d)", path, e, attempt, MAX_RETRIES)
            time.sleep(delay)
            delay = min(delay * 2, 60)
            continue

        cooldown = _parse_usage_headers(resp)

        if resp.status_code in (429, 500, 502, 503, 504):
            wait = max(delay, cooldown)
            log.warning("Meta %s on %s — sleeping %.1fs (attempt %d/%d)",
                        resp.status_code, path, wait, attempt, MAX_RETRIES)
            time.sleep(wait)
            delay = min(delay * 2, 60)
            continue

        if 400 <= resp.status_code < 500:
            # 4xx is the caller's problem (bad params, missing scope, etc) — surface Meta's
            # actual error message instead of swallowing it behind a generic HTTPError.
            try:
                err = resp.json().get("error", {})
                msg = err.get("message", resp.text[:200])
                err_type = err.get("type", "")
                err_code = err.get("code", "")
                err_sub = err.get("error_subcode", "")
            except (ValueError, AttributeError):
                msg, err_type, err_code, err_sub = resp.text[:200], "", "", ""
            raise RuntimeError(
                f"Meta API {method} {path} -> HTTP {resp.status_code} "
                f"[{err_type} code={err_code} subcode={err_sub}]: {msg}"
            )

        if cooldown:
            # Successful response but we're warned we're close — pause before next call.
            time.sleep(cooldown)

        return resp.json() if resp.content else {}

    raise RuntimeError(f"Meta API {method} {path} failed after {MAX_RETRIES} retries: {last_exc}")


# ─────────────────────────── Read: Insights + tree walks ───────────────────────────
INSIGHT_FIELDS_BASE = [
    "campaign_id", "campaign_name", "adset_id", "adset_name", "ad_id", "ad_name",
    "impressions", "spend", "clicks", "frequency", "reach",
    "actions", "action_values",
    # 3-sec views (hook rate input) lives in actions[].video_view; the dedicated
    # video_3_sec_watched_actions field was deprecated in v19+ and now 400s.
    "video_thruplay_watched_actions",
]


def get_insights(level: str, object_id: str, *, date_preset: str = "last_7d",
                 extra_fields: list[str] | None = None, breakdowns: list[str] | None = None,
                 limit: int = 500) -> list[dict]:
    """Pull insights at the given level for a campaign/adset/ad/account.

    `level` is one of "account", "campaign", "adset", "ad".
    `object_id` for account-level lookups is "act_<id>".
    """
    fields = ",".join(INSIGHT_FIELDS_BASE + (extra_fields or []))
    params: dict[str, Any] = {
        "level": level,
        "fields": fields,
        "date_preset": date_preset,
        "limit": limit,
    }
    if breakdowns:
        params["breakdowns"] = ",".join(breakdowns)

    out: list[dict] = []
    path = f"/{object_id}/insights"
    while True:
        payload = _request("GET", path, params=params)
        out.extend(payload.get("data", []))
        nxt = payload.get("paging", {}).get("next")
        if not nxt:
            break
        # `next` is an absolute URL with cursor params; switch to that path+query.
        # Keep using the same access token via _request, but pass the cursor.
        from urllib.parse import urlparse, parse_qs
        u = urlparse(nxt)
        path = u.path.replace(f"/{GRAPH_VERSION}", "", 1)
        params = {k: v[0] for k, v in parse_qs(u.query).items()}
    return out


def list_campaigns(ad_account_id: str) -> list[dict]:
    return _request("GET", f"/{ad_account_id}/campaigns",
                    params={"fields": "id,name,status,objective,daily_budget,effective_status", "limit": 500}
                    ).get("data", [])


def list_adsets(campaign_id: str) -> list[dict]:
    return _request("GET", f"/{campaign_id}/adsets",
                    params={"fields": "id,name,status,daily_budget,targeting,optimization_goal,effective_status",
                            "limit": 500}).get("data", [])


def list_adsets_for_account(ad_account_id: str) -> list[dict]:
    """All adsets in an ad account — flatter than walking campaigns."""
    return _request("GET", f"/{ad_account_id}/adsets",
                    params={"fields": "id,name,status,campaign_id,daily_budget,optimization_goal,effective_status",
                            "limit": 500}).get("data", [])


def list_ads(adset_id: str) -> list[dict]:
    return _request("GET", f"/{adset_id}/ads",
                    params={"fields": "id,name,status,creative,effective_status", "limit": 500}
                    ).get("data", [])


# ─────────────────────────── Write: budget + pause + creative ───────────────────────────
def _guard_budget(new_daily_budget_cents: int) -> int:
    cap_cents = int(MAX_ABSOLUTE_DAILY_BUDGET_USD * 100)
    if new_daily_budget_cents > cap_cents:
        log.warning("Capping requested daily budget %d cents -> %d cents (MB_MAX_DAILY_BUDGET_USD)",
                    new_daily_budget_cents, cap_cents)
        return cap_cents
    return new_daily_budget_cents


def update_adset_daily_budget(adset_id: str, new_daily_budget_cents: int) -> dict:
    """Set the daily budget on an ad set. Money values are in account-currency cents."""
    capped = _guard_budget(new_daily_budget_cents)
    if DRY_RUN:
        return {"dry_run": True, "adset_id": adset_id, "would_set_daily_budget_cents": capped}
    return _request("POST", f"/{adset_id}", data={"daily_budget": capped})


def pause_object(object_id: str, kind: str) -> dict:
    """Pause an ad / adset / campaign by id. `kind` is informational only."""
    if DRY_RUN:
        return {"dry_run": True, "kind": kind, "object_id": object_id, "would_set_status": "PAUSED"}
    return _request("POST", f"/{object_id}", data={"status": "PAUSED"})


def create_ad_creative(ad_account_id: str, page_id: str, *,
                       message: str, headline: str, link_url: str,
                       image_hash: str, call_to_action: str = "LEARN_MORE") -> dict:
    """Mint a new ad creative (one of three for a refresh batch)."""
    object_story_spec = {
        "page_id": page_id,
        "link_data": {
            "message": message,
            "name": headline,
            "link": link_url,
            "image_hash": image_hash,
            "call_to_action": {"type": call_to_action, "value": {"link": link_url}},
        },
    }
    if DRY_RUN:
        return {"dry_run": True, "ad_account_id": ad_account_id,
                "would_create_creative_with": object_story_spec}
    return _request("POST", f"/{ad_account_id}/adcreatives",
                    data={"object_story_spec": json.dumps(object_story_spec)})


# ─────────────────────────── Generic create_* (objective-agnostic) ───────────────────────────
#
# media_buyer/launcher.py has lead-gen-specific create_campaign/adset/ad helpers
# wired hard to OUTCOME_LEADS + HOUSING special-ad-category + LEAD_GENERATION
# optimization. fbads needs MESSAGES (Messenger CTA) and TRAFFIC (link clicks)
# objectives — different optimization goals, destination types, and CTAs. These
# generic helpers take the relevant fields as args so the same code path serves
# both fbads objectives and any future ones.


def create_campaign_generic(ad_account_id: str, *, name: str, objective: str,
                            status: str = "PAUSED",
                            special_ad_categories: list[str] | None = None,
                            buying_type: str = "AUCTION") -> dict:
    """Create a campaign with any objective. Returns {"id": "..."} or DRY shape.

    objective: a Meta OUTCOME_* identifier — for fbads:
      MESSAGES → OUTCOME_ENGAGEMENT
      TRAFFIC  → OUTCOME_TRAFFIC
    """
    body: dict[str, Any] = {
        "name": name,
        "objective": objective,
        "status": status,
        "buying_type": buying_type,
        "special_ad_categories": json.dumps(special_ad_categories or []),
    }
    if DRY_RUN:
        return {"dry_run": True, "ad_account_id": ad_account_id,
                "would_create_campaign": body}
    return _request("POST", f"/{ad_account_id}/campaigns", data=body)


def create_adset_generic(ad_account_id: str, *, name: str, campaign_id: str,
                         daily_budget_cents: int,
                         optimization_goal: str,
                         billing_event: str = "IMPRESSIONS",
                         destination_type: str | None = None,
                         targeting: dict | None = None,
                         promoted_object: dict | None = None,
                         status: str = "PAUSED",
                         bid_strategy: str = "LOWEST_COST_WITHOUT_CAP") -> dict:
    """Create an adset under any campaign. Caller supplies targeting + goal.

    For fbads:
      MESSAGES: optimization_goal=CONVERSATIONS,  destination_type=MESSENGER,
                promoted_object={"page_id": "...", "custom_event_type":
                "MESSAGING_CONVERSATION_STARTED_7D"} (optional)
      TRAFFIC:  optimization_goal=LINK_CLICKS,    destination_type=WEBSITE,
                promoted_object not needed
    """
    body: dict[str, Any] = {
        "name": name,
        "campaign_id": campaign_id,
        "daily_budget": daily_budget_cents,
        "billing_event": billing_event,
        "optimization_goal": optimization_goal,
        "status": status,
        "bid_strategy": bid_strategy,
        "targeting": json.dumps(targeting or {"geo_locations": {"countries": ["US"]}}),
    }
    if destination_type:
        body["destination_type"] = destination_type
    if promoted_object:
        body["promoted_object"] = json.dumps(promoted_object)
    if DRY_RUN:
        return {"dry_run": True, "ad_account_id": ad_account_id,
                "would_create_adset": body}
    return _request("POST", f"/{ad_account_id}/adsets", data=body)


def create_link_creative(ad_account_id: str, page_id: str, *,
                         message: str, headline: str, link_url: str,
                         image_hash: str, description: str = "",
                         call_to_action: str = "LEARN_MORE") -> dict:
    """Generic link-data creative. CTA type drives behavior:
      LEARN_MORE / SHOP_NOW / SIGN_UP / DOWNLOAD  → website-bound TRAFFIC ads
      MESSAGE_PAGE                                → opens Messenger thread
    """
    link_data: dict[str, Any] = {
        "message": message,
        "name": headline,
        "link": link_url,
        "image_hash": image_hash,
        "call_to_action": {"type": call_to_action, "value": {"link": link_url}},
    }
    if description:
        link_data["description"] = description
    object_story_spec = {"page_id": page_id, "link_data": link_data}
    if DRY_RUN:
        return {"dry_run": True, "ad_account_id": ad_account_id,
                "would_create_creative": object_story_spec}
    return _request("POST", f"/{ad_account_id}/adcreatives",
                    data={"object_story_spec": json.dumps(object_story_spec)})


def create_ad_generic(ad_account_id: str, *, name: str, adset_id: str,
                      creative_id: str, status: str = "PAUSED") -> dict:
    """Create an ad pairing an adset with a creative."""
    body = {
        "name": name,
        "adset_id": adset_id,
        "creative": json.dumps({"creative_id": creative_id}),
        "status": status,
    }
    if DRY_RUN:
        return {"dry_run": True, "ad_account_id": ad_account_id,
                "would_create_ad": body}
    return _request("POST", f"/{ad_account_id}/ads", data=body)


def upload_image_from_path(ad_account_id: str, image_path: str) -> dict:
    """Upload a local image file as an ad asset. Returns {"image_hash": "..."}
    or DRY shape. Meta de-dupes identical bytes → cheap to call repeatedly."""
    if DRY_RUN:
        return {"dry_run": True, "image_path": image_path,
                "image_hash": "DRY_RUN_HASH"}
    with open(image_path, "rb") as fh:
        files = {"file": (os.path.basename(image_path), fh.read(),
                          "image/jpeg" if image_path.lower().endswith((".jpg", ".jpeg"))
                          else "image/png")}
    token = token_store.get_active_token()
    r = _SESSION.post(
        f"{BASE}/{ad_account_id}/adimages",
        params={"access_token": token},
        files=files, timeout=60,
    )
    r.raise_for_status()
    payload = r.json()
    images = payload.get("images", {})
    first = next(iter(images.values()), None)
    if not first or not first.get("hash"):
        raise RuntimeError(f"adimages upload returned no hash: {payload}")
    return {"image_hash": first["hash"], "url": first.get("url", "")}


# ─────────────────────────── CAPI: server-side conversion send ───────────────────────────
def send_capi_event(pixel_id: str, event_name: str, event_time: int,
                    user_data: dict, custom_data: dict, *,
                    event_source_url: str | None = None,
                    event_id: str | None = None,
                    action_source: str = "website") -> dict:
    """Forward a conversion to Meta's Conversions API.

    Hash PII fields (email, phone, fn, ln, etc.) per Meta's spec before sending.
    `event_id` should match the browser-side Pixel fbq() event_id so Meta
    de-duplicates the pair.
    """
    hashed = {k: _sha256(v) if k in _PII_FIELDS and v else v for k, v in user_data.items()}
    event: dict[str, Any] = {
        "event_name": event_name,
        "event_time": event_time,
        "action_source": action_source,
        "user_data": hashed,
        "custom_data": custom_data,
    }
    if event_source_url:
        event["event_source_url"] = event_source_url
    if event_id:
        event["event_id"] = event_id

    if DRY_RUN:
        return {"dry_run": True, "pixel_id": pixel_id, "would_send_event": event}
    return _request("POST", f"/{pixel_id}/events",
                    data={"data": json.dumps([event])})


_PII_FIELDS = {"em", "ph", "fn", "ln", "ge", "db", "ct", "st", "zp", "country"}


def _sha256(v: str) -> str:
    return hashlib.sha256(v.strip().lower().encode("utf-8")).hexdigest()


# ─────────────────────────── Webhook signature verification ───────────────────────────
def verify_meta_signature(payload: bytes, signature_header: str | None) -> bool:
    """Verify Meta's X-Hub-Signature-256 header against the raw request body."""
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    secret = os.environ["META_APP_SECRET"].encode("utf-8")
    expected = "sha256=" + hmac.new(secret, payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header)


def verify_shopify_signature(payload: bytes, signature_header: str | None) -> bool:
    """Verify Shopify's X-Shopify-Hmac-Sha256 header (base64'd) against the body."""
    import base64
    if not signature_header:
        return False
    secret = os.environ["SHOPIFY_WEBHOOK_SECRET"].encode("utf-8")
    expected = base64.b64encode(hmac.new(secret, payload, hashlib.sha256).digest()).decode()
    return hmac.compare_digest(expected, signature_header)
