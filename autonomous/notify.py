"""
Shared notifier for owner-facing event pings.

Used for things like "a wholesale deal just closed" — the kind of event the
owner wants to know about within seconds, not in the next daily digest.

Delivery channels are tried in order, first one that's configured wins:
1. iMessage via AppleScript bridge — requires IMESSAGE_RELAY_URL, IMESSAGE_SECRET,
   IMESSAGE_TO. The relay is a small HTTP listener on a Mac that calls
   osascript → Messages.app. Setup: see mac/README.md.
2. Twilio SMS — requires TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER
3. Email-to-SMS gateway — requires NOTIFY_SMS_GATEWAY (e.g. "2073854041@vtext.com").
   Free, but only works if you know the carrier; common ones:
     Verizon  → @vtext.com           T-Mobile → @tmomail.net
     AT&T     → @txt.att.net          Sprint   → @messaging.sprintpcs.com
     Boost    → @sms.myboostmobile.com
4. Plain email to OWNER_EMAIL (always available — Gmail SMTP).

All channels can be disabled by setting NOTIFY_DISABLED=1 (useful for tests).
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import requests

from autonomous import mailer

DEFAULT_OWNER_PHONE = "+12073854041"   # 207-385-4041, Wholesale Omniverse line


def _digits(s: str) -> str:
    return "".join(c for c in (s or "") if c.isdigit())


def _to_e164(phone: str) -> str:
    d = _digits(phone)
    if len(d) == 11 and d.startswith("1"):
        return f"+{d}"
    if len(d) == 10:
        return f"+1{d}"
    return phone if phone.startswith("+") else ""


def _imessage_send(message: str) -> dict:
    """POST to the Mac-side AppleScript relay. Returns standard {status,...}."""
    url    = os.environ.get("IMESSAGE_RELAY_URL", "")
    secret = os.environ.get("IMESSAGE_SECRET", "")
    to     = os.environ.get("IMESSAGE_TO", "")
    if not (url and to):
        return {"status": "skipped", "reason": "imessage_not_configured"}
    try:
        r = requests.post(
            url.rstrip("/") + "/send",
            json={"to": to, "message": message[:2000]},
            headers={"X-Auth": secret} if secret else {},
            timeout=10,
        )
        if r.status_code == 200:
            return {"status": "sent", "channel": "imessage", **r.json()}
        return {"status": "failed", "channel": "imessage",
                "code": r.status_code, "error": r.text[:200]}
    except requests.RequestException as e:
        return {"status": "failed", "channel": "imessage", "error": str(e)[:200]}


def _twilio_send(to_e164: str, message: str) -> dict:
    sid   = os.environ.get("TWILIO_ACCOUNT_SID", "")
    token = os.environ.get("TWILIO_AUTH_TOKEN", "")
    from_ = os.environ.get("TWILIO_FROM_NUMBER", "")
    if not (sid and token and from_):
        return {"status": "skipped", "reason": "twilio_not_configured"}
    try:
        r = requests.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
            data={"To": to_e164, "From": from_, "Body": message[:1600]},
            auth=(sid, token),
            timeout=10,
        )
        if 200 <= r.status_code < 300:
            return {"status": "sent", "channel": "twilio",
                    "sid": r.json().get("sid", "")}
        return {"status": "failed", "channel": "twilio",
                "code": r.status_code, "error": r.text[:200]}
    except requests.RequestException as e:
        return {"status": "failed", "channel": "twilio", "error": str(e)[:200]}


def _gateway_send(message: str) -> dict:
    """Email-to-SMS gateway. Sends a plain email; carrier delivers as SMS."""
    addr = os.environ.get("NOTIFY_SMS_GATEWAY", "")
    if not addr:
        return {"status": "skipped", "reason": "no_sms_gateway"}
    # Gateway messages must be plain — no HTML, no subject formatting.
    r = mailer.send("notify", addr, "", message[:160], purpose="notification")
    return {"channel": "email_gateway", **r}


def _email_fallback(subject: str, body: str) -> dict:
    addr = os.environ.get("OWNER_EMAIL",
            os.environ.get("SMTP_USER", ""))
    if not addr:
        return {"status": "skipped", "reason": "no_owner_email"}
    r = mailer.send("notify", addr, subject, body, purpose="notification")
    return {"channel": "email", **r}


def _log(event: str, message: str, result: dict) -> None:
    """Persist every notification attempt so we can audit later."""
    log_file = Path(__file__).parent.parent / "data" / "notify_log.json"
    log_file.parent.mkdir(exist_ok=True)
    try:
        log = json.loads(log_file.read_text()) if log_file.exists() else []
    except json.JSONDecodeError:
        log = []
    log.append({
        "at":      datetime.now().isoformat(),
        "event":   event,
        "message": message[:300],
        "result":  result,
    })
    log_file.write_text(json.dumps(log[-500:], indent=2))


def send_sms(message: str, *, subject: str = "", event: str = "alert",
             to_phone: str = "") -> dict:
    """Send a short alert. Returns the first channel that succeeded.

    The fallback chain is: Twilio → email-to-SMS gateway → owner email."""
    if os.environ.get("NOTIFY_DISABLED") == "1":
        return {"status": "skipped", "reason": "notify_disabled"}

    to_e164 = _to_e164(to_phone or os.environ.get(
        "OWNER_PHONE", DEFAULT_OWNER_PHONE))

    # 1. iMessage via AppleScript bridge (real blue bubbles)
    r = _imessage_send(message)
    if r.get("status") == "sent":
        _log(event, message, r)
        return r

    # 2. Twilio SMS
    if to_e164:
        r = _twilio_send(to_e164, message)
        if r.get("status") == "sent":
            _log(event, message, r)
            return r

    # 3. Email-to-SMS gateway
    r = _gateway_send(message)
    if r.get("status") == "sent":
        _log(event, message, r)
        return r

    # 4. Plain email fallback
    subj = subject or f"[Wholesale Omniverse] {event}"
    r = _email_fallback(subj, message)
    _log(event, message, r)
    return r


def notify_deal_closed(contract: dict, buyer_name: str,
                       assignment_fee: float, lifetime_closed: int = 0) -> dict:
    """Convenience wrapper called from tools.assign_contract on close."""
    addr = contract.get("address", "(unknown address)")
    cid  = contract.get("contract_id", "")
    msg = (
        f"🎉 DEAL CLOSED — ${assignment_fee:,.0f}\n"
        f"Property: {addr}\n"
        f"Buyer: {buyer_name}\n"
        f"Contract: {cid}\n"
        + (f"(Total closed: {lifetime_closed})" if lifetime_closed else "")
    )
    return send_sms(msg, subject=f"💰 Deal closed — ${assignment_fee:,.0f} ({addr})",
                    event="deal_closed")
