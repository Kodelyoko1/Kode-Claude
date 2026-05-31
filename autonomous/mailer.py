"""
Shared autonomous mailer wrapper around Gmail SMTP.
Adds throttling, error capture, and per-agent send logs.
"""
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from email_template import send_branded_email

DATA_DIR = Path(__file__).parent.parent / "data"
SEND_LOG = DATA_DIR / "agent_send_log.json"
BOUNCE_LOG = DATA_DIR / "bounce_log.json"

# Prefixes / patterns we never want to send cold outreach to (role accounts,
# scraped site contacts, distribution lists). These nearly always bounce or
# get flagged as spam.
BAD_LOCAL_PARTS = {
    "info", "support", "help", "contact", "admin", "webmaster", "postmaster",
    "noreply", "no-reply", "donotreply", "privacy", "legal", "press", "abuse",
    "sales", "marketing", "hr", "jobs", "careers", "billing", "accounts",
    "newsletter", "subscribe", "unsubscribe", "feedback", "office",
}


def _load():
    if SEND_LOG.exists():
        try:
            with open(SEND_LOG) as f:
                return json.load(f)
        except Exception:
            return []
    return []


def _bounced_addresses() -> set:
    """Set of email addresses we've previously seen bounce. Cached per process."""
    global _BOUNCED_CACHE
    try:
        return _BOUNCED_CACHE
    except NameError:
        pass
    addrs = set()
    if BOUNCE_LOG.exists():
        try:
            with open(BOUNCE_LOG) as f:
                for entry in json.load(f):
                    a = (entry.get("address") or "").strip().lower()
                    if a:
                        addrs.add(a)
        except Exception:
            pass
    _BOUNCED_CACHE = addrs
    return addrs


def _is_role_address(email: str) -> bool:
    """Reject role/system addresses that should never get cold outreach."""
    local = email.split("@", 1)[0].lower() if "@" in email else email.lower()
    return local in BAD_LOCAL_PARTS


def _save(data):
    DATA_DIR.mkdir(exist_ok=True)
    with open(SEND_LOG, "w") as f:
        json.dump(data, f, indent=2)


def _to_html(body: str) -> str:
    """Convert plain text body to HTML paragraphs (matches followup_agent style)."""
    out = []
    for line in body.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        if line.startswith(("•", "-", "*")):
            out.append(
                f'<p style="margin:4px 0;padding-left:12px;">'
                f'<span style="color:#c9a84c;font-weight:bold;">&#10003;</span>&nbsp;'
                f'{line.lstrip("•-* ").strip()}</p>'
            )
        else:
            out.append(f'<p style="margin:0 0 14px 0;">{line}</p>')
    return "\n".join(out)


def send(
    agent_key: str,
    to_email: str,
    subject: str,
    body: str,
    purpose: str = "outreach",
    attachments: list = None,
) -> dict:
    """Send a branded email and log result. purpose ∈ {outreach, fulfillment, billing, notification}."""
    if not to_email:
        return {"status": "skipped", "reason": "no_email"}

    addr_lc = to_email.strip().lower()
    # Skip outbound to role accounts (info@, support@, noreply@, etc.) and
    # any address we've previously seen bounce. Fulfillment/billing/notification
    # may still go to the owner so they're not filtered.
    if purpose == "outreach":
        if _is_role_address(addr_lc):
            return {"status": "skipped", "reason": "role_address"}
        if addr_lc in _bounced_addresses():
            return {"status": "skipped", "reason": "previously_bounced"}

    result = send_branded_email(
        to_email=to_email,
        subject=subject,
        body_text=body,
        body_html_inner=_to_html(body),
        attachments=attachments or [],
    )

    log = _load()
    log.append({
        "agent":   agent_key,
        "to":      to_email,
        "subject": subject,
        "purpose": purpose,
        "status":  result.get("status", "unknown"),
        "error":   result.get("error", ""),
        "ts":      datetime.now().isoformat(),
    })
    if len(log) > 5000:
        log = log[-5000:]
    _save(log)
    return result


def recent_sends(agent_key: str = "", limit: int = 25) -> list:
    log = _load()
    if agent_key:
        log = [r for r in log if r.get("agent") == agent_key]
    return log[-limit:][::-1]
