"""
Thin external integrations: Twilio Lookup (phone validation), Slack push, simple CRM forward.

Each function is intentionally request-level only — no SDK pulls. Keeping the dep
surface small means the cron path stays fast and the FastAPI workers don't pay
SDK import cost on every webhook.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

import requests

log = logging.getLogger("media_buyer.integrations")


# ─────────────────────────── Twilio Lookup ───────────────────────────
def twilio_lookup(phone_e164: str, *, fetch_carrier: bool = True) -> dict[str, Any]:
    """Validate + enrich a phone number via Twilio Lookup v2.

    Returns {"valid": bool, "carrier": "...", "line_type": "mobile|landline|voip|...",
              "country_code": "US", "raw": <full Twilio payload>}.
    Lookup costs ~$0.005 with carrier; we still call it because it catches
    typos and burner VoIP numbers before they enter the lead pipeline.
    """
    sid = os.environ["TWILIO_ACCOUNT_SID"]
    token = os.environ["TWILIO_AUTH_TOKEN"]
    params = {"Fields": "line_type_intelligence"} if fetch_carrier else {}

    r = requests.get(
        f"https://lookups.twilio.com/v2/PhoneNumbers/{phone_e164}",
        params=params, auth=(sid, token), timeout=10,
    )
    if r.status_code == 404:
        return {"valid": False, "reason": "not_a_phone_number"}
    r.raise_for_status()
    data = r.json()
    lti = data.get("line_type_intelligence") or {}
    return {
        "valid": bool(data.get("valid")),
        "country_code": data.get("country_code"),
        "carrier": lti.get("carrier_name"),
        "line_type": lti.get("type"),
        "raw": data,
    }


# ─────────────────────────── Slack push ───────────────────────────
def slack_post(webhook_url: str | None, text: str, blocks: list | None = None) -> bool:
    """POST to an Incoming Webhook. Returns True on success. Never raises."""
    if not webhook_url:
        log.info("Slack post skipped (no webhook configured): %s", text[:120])
        return False
    body: dict[str, Any] = {"text": text}
    if blocks:
        body["blocks"] = blocks
    try:
        r = requests.post(webhook_url, json=body, timeout=10)
        return r.status_code < 400
    except requests.RequestException as e:
        log.warning("Slack post failed: %s", e)
        return False


# ─────────────────────────── Lightweight CRM forward ───────────────────────────
def crm_push(lead: dict) -> bool:
    """Forward a scored lead to whatever CRM is configured.

    Pluggable via MB_CRM_WEBHOOK_URL (a Zapier/Make/n8n inbound webhook works fine).
    For lead-gen Real Estate this also auto-appends to data/leads.json so the
    existing followup_agent picks it up on its next cycle.
    """
    crm_url = os.getenv("MB_CRM_WEBHOOK_URL")
    forwarded = False
    if crm_url:
        try:
            r = requests.post(crm_url, json=lead, timeout=10)
            forwarded = r.status_code < 400
        except requests.RequestException as e:
            log.warning("CRM webhook failed: %s", e)

    # Drop into the shared leads bucket so the followup_agent inherits the lead.
    try:
        from autonomous import storage
        leads = storage.load("leads.json", [])
        leads.append(lead)
        storage.save("leads.json", leads)
    except Exception as e:
        log.warning("Local leads.json append failed: %s", e)

    return forwarded
