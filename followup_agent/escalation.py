"""
Distress-tag escalation — interrupt the slow drip for leads that need immediate
owner attention.

Wholesalers close the most deals on the highest-motivation sellers: foreclosure,
vacant/abandoned, probate, tax-delinquent, code violations. Putting those leads
through a 6-day email drip + 60-day SMS sequence wastes the most valuable
window — sometimes a few days between "I need out NOW" and "I signed with
someone else."

This module:
  1. Scans data/leads.json once per agent cycle for distress-tagged leads
     that haven't been escalated yet.
  2. Marks them with `escalated_at` so we don't ping the owner twice for
     the same lead.
  3. Emails the owner a digest of new hot leads with everything they need
     to call: name, phone, address, motivation tag, source.
  4. Optionally fires an SMS to the owner's phone (FOLLOWUP_OWNER_SMS_PHONE)
     for the very hottest tier (foreclosure / pre-foreclosure).
"""
from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

DATA_DIR        = Path(__file__).parent.parent / "data"
LEADS_FILE      = DATA_DIR / "leads.json"
ESCALATION_LOG  = DATA_DIR / "escalation_log.json"

log = logging.getLogger("followup_agent.escalation")


# Three priority tiers — the one we send via SMS, the one we email, the rest.
TIER_INSTANT_CALL = {           # owner SMS + email, mark "call_today"
    "foreclosure", "pre_foreclosure", "pre-foreclosure",
}
TIER_OWNER_EMAIL = {            # owner email digest only
    "tax_delinquent", "tax-delinquent",
    "vacant", "vacant_abandoned",
    "probate", "inherited", "estate",
    "divorce", "bankruptcy",
    "code_violations", "code-violations",
}
ALL_DISTRESS = TIER_INSTANT_CALL | TIER_OWNER_EMAIL


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return default


def _save(path: Path, data) -> None:
    path.parent.mkdir(exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except Exception:
        try: os.unlink(tmp)
        except OSError: pass
        raise


def _tier_for(motivation: str) -> Optional[str]:
    m = (motivation or "").strip().lower()
    if any(tag in m for tag in TIER_INSTANT_CALL):
        return "call_today"
    if any(tag in m for tag in TIER_OWNER_EMAIL):
        return "owner_email"
    return None


# ─────────────────────────── Owner notification ───────────────────────────

def _email_owner(subject: str, body: str) -> bool:
    """Send an email via the same SMTP path the other agents use."""
    try:
        from autonomous import mailer
    except Exception as e:
        log.warning("mailer import failed: %s", e)
        return False
    owner = (os.environ.get("FOLLOWUP_OWNER_EMAIL")
             or os.environ.get("SMTP_USER", ""))
    if not owner:
        return False
    r = mailer.send("followup", owner, subject, body, purpose="escalation")
    return r.get("status") == "sent"


def _sms_owner(body: str) -> bool:
    """Send an SMS to the owner for the highest tier."""
    phone = os.environ.get("FOLLOWUP_OWNER_SMS_PHONE", "")
    if not phone:
        return False
    # Reuse the sms.py sender (handles dry-run + Twilio creds).
    from .sms import _is_live, _twilio_send, normalize_e164
    norm = normalize_e164(phone)
    if not norm:
        return False
    if not _is_live():
        return False  # respect the same gate as outbound seller SMS
    return _twilio_send(norm, body).get("status") == "sent"


# ─────────────────────────── Main pass ───────────────────────────

def find_new_hot_leads() -> list[dict]:
    """Return distress-tagged leads that haven't been escalated yet."""
    leads = _load(LEADS_FILE, {})
    if not isinstance(leads, dict):
        return []
    new_hot = []
    for lid, lead in leads.items():
        if lead.get("escalated_at"):
            continue
        if lead.get("status") in ("dead", "cold", "assigned"):
            continue
        tier = _tier_for(lead.get("motivation", ""))
        if not tier:
            continue
        new_hot.append({
            "lead_id":    lid,
            "tier":       tier,
            "motivation": lead.get("motivation", ""),
            "name":       lead.get("seller_name", ""),
            "phone":      lead.get("seller_phone", ""),
            "email":      lead.get("seller_email", ""),
            "address":    lead.get("address", ""),
            "city":       lead.get("city", ""),
            "state":      lead.get("state", ""),
            "source":     lead.get("lead_source", ""),
            "created":    lead.get("created_at", ""),
        })
    # Sort by tier (call_today first), then by created (newest first)
    new_hot.sort(key=lambda x: (0 if x["tier"] == "call_today" else 1,
                                  x["created"]), reverse=False)
    new_hot.sort(key=lambda x: x["tier"] != "call_today")
    return new_hot


def _mark_escalated(lead_ids: list[str], digest_sent: bool, call_sms_sent: int) -> None:
    leads = _load(LEADS_FILE, {})
    ts = _now_iso()
    for lid in lead_ids:
        if lid in leads:
            leads[lid]["escalated_at"] = ts
            leads[lid]["updated_at"]   = ts
    _save(LEADS_FILE, leads)

    log_rec = _load(ESCALATION_LOG, [])
    if not isinstance(log_rec, list):
        log_rec = []
    log_rec.append({
        "ts":              ts,
        "count":           len(lead_ids),
        "lead_ids":        lead_ids,
        "digest_sent":     digest_sent,
        "call_sms_sent":   call_sms_sent,
    })
    _save(ESCALATION_LOG, log_rec)


def _format_digest(hot: list[dict]) -> str:
    """Owner-facing digest. Plain text, optimized for phone reading."""
    call_today = [h for h in hot if h["tier"] == "call_today"]
    rest       = [h for h in hot if h["tier"] != "call_today"]

    lines = [
        "Hot lead digest — distress-tagged sellers in your pipeline.",
        "",
    ]
    if call_today:
        lines.append(f"=== CALL TODAY ({len(call_today)}) ===")
        for h in call_today[:25]:
            lines.append(f"• {h['name'] or '(no name)'} — {h['phone'] or 'no phone'} — "
                          f"{h['address']}, {h['city']}, {h['state']}")
            lines.append(f"    motivation: {h['motivation']}")
            if h.get("source"):
                lines.append(f"    source: {h['source']}")
            lines.append("")
    if rest:
        lines.append(f"=== EMAIL/SMS PRIORITY ({len(rest)}) ===")
        for h in rest[:50]:
            lines.append(f"• {h['name'] or '(no name)'} — {h['phone'] or h.get('email','no contact')} — "
                          f"{h['address']}, {h['city']} — {h['motivation']}")
    lines.append("")
    lines.append(f"-- Wholesale Omniverse · {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    return "\n".join(lines)


def run_escalation() -> dict:
    """Find new hot leads, notify owner, mark them escalated. Returns summary."""
    hot = find_new_hot_leads()
    if not hot:
        return {"new_hot": 0, "digest_sent": False, "call_sms_sent": 0}

    call_today = [h for h in hot if h["tier"] == "call_today"]
    digest_sent = False
    sms_sent = 0

    # 1. Owner digest email (always for any hot)
    subject = f"[Followup] {len(hot)} hot lead{'s' if len(hot)!=1 else ''}"
    if call_today:
        subject = f"[Followup] {len(call_today)} CALL TODAY + {len(hot)-len(call_today)} hot"
    digest_sent = _email_owner(subject, _format_digest(hot))

    # 2. Owner SMS for the very hottest tier — capped at 5 to avoid SMS spam
    for h in call_today[:5]:
        body = (f"[Wholesale Omniverse] CALL TODAY: {h['name'] or 'seller'} {h['phone'] or '?'} — "
                f"{h['address']} {h['city']} — {h['motivation'][:60]}")
        if _sms_owner(body):
            sms_sent += 1

    _mark_escalated([h["lead_id"] for h in hot], digest_sent, sms_sent)

    return {
        "new_hot":       len(hot),
        "call_today":    len(call_today),
        "digest_sent":   digest_sent,
        "call_sms_sent": sms_sent,
    }


def main() -> int:
    import argparse
    p = argparse.ArgumentParser(description="Distress-tag escalation")
    p.add_argument("--summary", action="store_true",
                    help="Show pending hot leads without notifying")
    p.add_argument("--run", action="store_true",
                    help="Notify owner of any new hot leads and mark them escalated")
    args = p.parse_args()

    if args.summary or not args.run:
        hot = find_new_hot_leads()
        print(f"new hot leads pending escalation: {len(hot)}")
        print(f"  call_today: {sum(1 for h in hot if h['tier']=='call_today')}")
        for h in hot[:10]:
            print(f"  [{h['tier']:11s}] {h['lead_id']}  {h.get('name','?'):20s}  "
                  f"{h.get('phone','no-phone'):16s}  {h['motivation'][:50]}")
        return 0

    result = run_escalation()
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
