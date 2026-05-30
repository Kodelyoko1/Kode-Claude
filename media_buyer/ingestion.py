"""
Dual-model webhook ingestion (FastAPI).

Routes:
- GET  /webhooks/meta/leadgen        -> Meta subscription challenge
- POST /webhooks/meta/leadgen        -> Lead arrives; enrich + score + push within 60s
- POST /webhooks/shopify/orders      -> Order placed; record revenue + forward CAPI
- POST /webhooks/meta/capi           -> Pixel-side forwarder (e.g. for server-side tracking)
- GET  /healthz                      -> liveness probe

Lead Gen → Twilio Lookup → Claude scoring → Slack + CRM. Heavy I/O runs in
BackgroundTasks so we ACK Meta in <2s (Meta retries if we 5xx or take >20s).

E-Com → record revenue, send a deduplicated CAPI event, fan out a fulfillment
notification when the SKU crosses the configured winning-product threshold.
"""
from __future__ import annotations

import logging
import os
import time
import uuid
from datetime import datetime
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException, Header, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from . import meta_api, scoring, integrations
from .config import PROFILES, profile_for

log = logging.getLogger("media_buyer.ingestion")

app = FastAPI(title="Media Buyer Webhooks", version="1.0")


# ─────────────────────────── Health ───────────────────────────
@app.get("/healthz")
def healthz() -> dict[str, Any]:
    return {"ok": True, "ts": datetime.utcnow().isoformat() + "Z"}


# ─────────────────────────── Meta Lead-Ads subscription challenge ───────────────────────────
@app.get("/webhooks/meta/leadgen")
def meta_subscription_challenge(
    hub_mode: str = "", hub_challenge: str = "", hub_verify_token: str = "",
):
    """Meta hits this once when you subscribe a webhook. Echo the challenge."""
    expected = os.getenv("META_WEBHOOK_VERIFY_TOKEN", "")
    if hub_mode == "subscribe" and hub_verify_token == expected:
        return PlainTextResponse(content=hub_challenge)
    raise HTTPException(status_code=403, detail="bad verify token")


# ─────────────────────────── Meta Lead-Ads ingest ───────────────────────────
@app.post("/webhooks/meta/leadgen")
async def meta_leadgen(request: Request, background: BackgroundTasks,
                       x_hub_signature_256: str | None = Header(default=None)):
    """Receive a leadgen event; do the heavy work in a background task."""
    body = await request.body()
    if not meta_api.verify_meta_signature(body, x_hub_signature_256):
        raise HTTPException(status_code=403, detail="bad signature")
    payload = await request.json()

    # Meta sends {"object": "page", "entry": [{"changes": [{"value": {"leadgen_id": "...", "form_id": "...", ...}}]}]}
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            if change.get("field") != "leadgen":
                continue
            v = change.get("value", {})
            leadgen_id = v.get("leadgen_id")
            if not leadgen_id:
                continue
            background.add_task(_process_leadgen, leadgen_id, v.get("form_id"), v.get("page_id"))

    # ACK fast — Meta retries on 5xx or >20s.
    return JSONResponse({"received": True})


def _process_leadgen(leadgen_id: str, form_id: str | None, page_id: str | None) -> None:
    """The 60-second-budget path: fetch lead → enrich → score → forward."""
    started = time.time()
    try:
        lead = meta_api._request("GET", f"/{leadgen_id}",  # noqa: SLF001 — internal helper is fine here
                                 params={"fields": "id,created_time,ad_id,form_id,field_data"})
    except Exception as e:
        log.exception("Failed to fetch lead %s: %s", leadgen_id, e)
        return

    # field_data is a list of {name, values[]}; flatten to dict
    answers = {f["name"]: (f.get("values") or [""])[0] for f in lead.get("field_data", [])}
    phone = answers.get("phone_number") or answers.get("phone")

    enrichment: dict[str, Any] = {}
    if phone:
        try:
            enrichment["phone_lookup"] = integrations.twilio_lookup(phone)
        except Exception as e:
            log.warning("Twilio lookup failed for %s: %s", phone, e)

    # Discard obvious garbage early (invalid number, VoIP burner)
    pl = enrichment.get("phone_lookup", {})
    if pl and not pl.get("valid"):
        log.info("Discarding lead %s — invalid phone", leadgen_id)
        return
    if pl.get("line_type") == "nonFixedVoip":
        log.info("Discarding lead %s — VoIP burner number", leadgen_id)
        return

    score = scoring.score_lead({**answers, "ad_id": lead.get("ad_id"), "form_id": form_id})

    final_lead = {
        "id": leadgen_id,
        "source": "meta_leadgen",
        "received_at": datetime.utcnow().isoformat() + "Z",
        "ad_id": lead.get("ad_id"),
        "form_id": form_id,
        "page_id": page_id,
        "answers": answers,
        "enrichment": enrichment,
        "score": score,
    }
    integrations.crm_push(final_lead)

    profile = profile_for(kind="lead_gen")
    tier = score.get("tier", "?")
    if profile.alert_slack_webhook and tier in ("Hot", "Warm"):
        integrations.slack_post(
            profile.alert_slack_webhook,
            f":fire: New *{tier}* lead — {answers.get('full_name') or answers.get('first_name', 'unknown')} "
            f"({phone or 'no phone'}) — {score.get('reason', '')}",
        )

    log.info("Lead %s processed in %.1fs tier=%s", leadgen_id, time.time() - started, tier)


# ─────────────────────────── Shopify order ingest ───────────────────────────
@app.post("/webhooks/shopify/orders")
async def shopify_order(request: Request, background: BackgroundTasks,
                        x_shopify_hmac_sha256: str | None = Header(default=None)):
    body = await request.body()
    if not meta_api.verify_shopify_signature(body, x_shopify_hmac_sha256):
        raise HTTPException(status_code=403, detail="bad signature")
    order = await request.json()

    background.add_task(_process_order, order)
    return JSONResponse({"received": True})


def _process_order(order: dict) -> None:
    """Persist order revenue, send CAPI Purchase event (deduplicated), fan out fulfillment."""
    from autonomous import storage
    profile = profile_for(kind="ecom")

    order_id = str(order.get("id"))
    total = float(order.get("total_price") or 0)
    currency = order.get("currency", "USD")
    customer = order.get("customer") or {}
    email = (customer.get("email") or "").lower()
    phone = (customer.get("phone") or "").replace(" ", "").replace("-", "")

    # event_id deduplicates against any browser-side Pixel fbq() Purchase event
    # that already fired for the same order.
    event_id = f"shopify_order_{order_id}"

    user_data: dict[str, Any] = {}
    if email:
        user_data["em"] = email
    if phone:
        user_data["ph"] = phone
    user_data["client_ip_address"] = order.get("browser_ip")
    user_data["client_user_agent"] = (order.get("client_details") or {}).get("user_agent")

    custom_data = {
        "currency": currency,
        "value": total,
        "content_ids": [str(li.get("product_id")) for li in order.get("line_items", [])],
        "content_type": "product",
    }

    try:
        result = meta_api.send_capi_event(
            pixel_id=profile.pixel_id,
            event_name="Purchase",
            event_time=int(time.time()),
            user_data=user_data,
            custom_data=custom_data,
            event_source_url=order.get("landing_site"),
            event_id=event_id,
        )
        log.info("CAPI Purchase forwarded for order %s: %s", order_id, result)
    except Exception as e:
        log.exception("CAPI send failed for order %s: %s", order_id, e)

    # Persist for our own analytics — keyed by order id to dedupe.
    orders = storage.load("media_buyer/orders.json", {})
    orders[order_id] = {
        "order_id": order_id,
        "total": total,
        "currency": currency,
        "received_at": datetime.utcnow().isoformat() + "Z",
        "line_items": [{"product_id": li.get("product_id"),
                        "title": li.get("title"),
                        "qty": li.get("quantity")} for li in order.get("line_items", [])],
        "event_id": event_id,
    }
    storage.save("media_buyer/orders.json", orders)

    # Winning-product alert: any SKU crossing the configured day's order threshold
    _maybe_alert_winning_product(order, profile)


def _maybe_alert_winning_product(order: dict, profile) -> None:
    """If a single product crosses MB_WINNING_DAILY_THRESHOLD orders today, ping ops."""
    threshold = int(os.getenv("MB_WINNING_DAILY_THRESHOLD", "25"))
    from autonomous import storage
    today = datetime.utcnow().date().isoformat()
    counts = storage.load(f"media_buyer/product_orders_{today}.json", {})

    new_winners = []
    for li in order.get("line_items", []):
        pid = str(li.get("product_id"))
        qty = int(li.get("quantity") or 1)
        counts[pid] = counts.get(pid, 0) + qty
        if counts[pid] == threshold:
            new_winners.append({"product_id": pid, "title": li.get("title"), "count": counts[pid]})
    storage.save(f"media_buyer/product_orders_{today}.json", counts)

    if new_winners and profile.alert_slack_webhook:
        for w in new_winners:
            integrations.slack_post(
                profile.alert_slack_webhook,
                f":rocket: Winning product detected — *{w['title']}* (id {w['product_id']}) "
                f"hit {w['count']} orders today. Restock + scale check.",
            )


# ─────────────────────────── CAPI passthrough (browser-side fallback) ───────────────────────────
@app.post("/webhooks/meta/capi")
async def capi_passthrough(request: Request):
    """Accept a CAPI-shaped event from a first-party source and forward to Meta.

    Useful when a non-Shopify checkout (e.g. custom landing page) needs to send
    server-side conversions. The caller MUST include event_id matching the
    Pixel-side event for deduplication.
    """
    payload = await request.json()
    required = {"pixel_id", "event_name", "user_data", "custom_data", "event_id"}
    missing = required - set(payload)
    if missing:
        raise HTTPException(status_code=400, detail=f"missing: {sorted(missing)}")
    result = meta_api.send_capi_event(
        pixel_id=payload["pixel_id"],
        event_name=payload["event_name"],
        event_time=int(payload.get("event_time") or time.time()),
        user_data=payload["user_data"],
        custom_data=payload["custom_data"],
        event_source_url=payload.get("event_source_url"),
        event_id=payload["event_id"],
    )
    return {"ok": True, "result": result}
