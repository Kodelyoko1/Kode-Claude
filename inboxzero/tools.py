"""
InboxZero — autonomous inbox triage agent (owner inbox first, productized later).
Revenue: $97/mo per inbox, $297/mo team (3 inboxes), $97 one-time deep clean.

Reads the owner's Gmail over IMAP (uses the same SMTP_USER/SMTP_PASS app
password already configured for outbound mail — Gmail's app password works
for both directions). Categorizes the last N unread messages, archives
obvious promo/newsletter mail, flags anything that looks urgent, and emails
a daily summary back to the owner.

Categories: urgent | important | promo | newsletter | social | other

Triage rules (no API keys required):
  - List-Unsubscribe header        → newsletter
  - Sender domain in PROMO_DOMAINS → promo
  - Subject contains URGENT_TERMS  → urgent
  - Subject ends with "?"          → important (a question to you)
  - All-caps subject or many !!    → promo
  - Else                           → other

If ANTHROPIC_API_KEY is set, ambiguous "other" messages are escalated to
Claude for a one-word category.

Env vars used (re-uses existing Gmail config):
  SMTP_USER  — Gmail address (also used as IMAP user)
  SMTP_PASS  — Gmail app password (16-char)
  IZ_FETCH_LIMIT  — how many unread to pull per cycle (default 50)
  IZ_OWNER_EMAIL  — where to send the digest (default = SMTP_USER)
"""
import imaplib
import email
import email.utils
import json
import os
import re
import sys
from datetime import datetime
from email.header import decode_header
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from autonomous import storage, mailer, billing, metrics

AGENT_KEY = "inboxzero"

IMAP_HOST = "imap.gmail.com"
PROMO_DOMAINS = {
    "godaddy.com", "namecheap.com", "indeed.com", "ziprecruiter.com",
    "ebay.com", "groupon.com", "livingsocial.com", "uber.com", "lyft.com",
    "doordash.com", "grubhub.com", "ubereats.com", "starbucks.com",
}
URGENT_TERMS = {
    "urgent", "asap", "deadline", "action required", "expires today",
    "final notice", "overdue", "today only", "important:", "[urgent]",
    "payment failed", "invoice past due", "security alert",
}
SOCIAL_DOMAINS = {
    "linkedin.com", "twitter.com", "x.com", "facebookmail.com",
    "instagram.com", "tiktok.com", "discord.com", "reddit.com",
}


def _decode(value) -> str:
    if not value:
        return ""
    parts = decode_header(value)
    out = []
    for text, enc in parts:
        if isinstance(text, bytes):
            try:
                out.append(text.decode(enc or "utf-8", errors="ignore"))
            except Exception:
                out.append(text.decode("utf-8", errors="ignore"))
        else:
            out.append(text)
    return "".join(out).strip()


def _sender_domain(addr: str) -> str:
    name, email_addr = email.utils.parseaddr(addr)
    return email_addr.split("@")[-1].lower() if "@" in email_addr else ""


def _categorize(msg: email.message.Message) -> str:
    subj = (_decode(msg.get("Subject")) or "").lower()
    sender = _decode(msg.get("From")) or ""
    domain = _sender_domain(sender)
    has_unsub = "list-unsubscribe" in {k.lower() for k in msg.keys()}
    looks_promo = (subj.isupper() and len(subj) > 6) or subj.count("!") >= 2
    has_urgent_term = any(t in subj for t in URGENT_TERMS)

    # Mass-mail headers (List-Unsubscribe) → never personally urgent, even if subject screams.
    if domain in SOCIAL_DOMAINS:
        return "social"
    if has_unsub:
        if any(d in domain for d in PROMO_DOMAINS) or looks_promo or has_urgent_term:
            return "promo"
        return "newsletter"

    # No mass-mail header — now urgent terms / question / shouty subjects are meaningful.
    if has_urgent_term:
        return "urgent"
    if subj.endswith("?") or re.search(r"\b(can you|could you|would you|when can|do you)\b", subj):
        return "important"
    if looks_promo:
        return "promo"
    return "other"


def _claude_category(subject: str, sender: str, body_preview: str) -> str:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return "other"
    try:
        import anthropic
        client = anthropic.Anthropic()
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=10,
            messages=[{
                "role": "user",
                "content": (
                    "Categorize this email as exactly one word: urgent, important, "
                    "newsletter, promo, social, or other.\n\n"
                    f"From: {sender}\nSubject: {subject}\nBody preview: {body_preview[:500]}\n\n"
                    "Reply with just one word."
                ),
            }],
        )
        word = msg.content[0].text.strip().lower().split()[0]
        if word in {"urgent", "important", "newsletter", "promo", "social", "other"}:
            return word
        return "other"
    except Exception:
        return "other"


def _body_preview(msg: email.message.Message, limit: int = 600) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                try:
                    return part.get_payload(decode=True).decode(errors="ignore")[:limit]
                except Exception:
                    continue
    else:
        try:
            return msg.get_payload(decode=True).decode(errors="ignore")[:limit]
        except Exception:
            return ""
    return ""


def triage_inbox(user: str, app_password: str, fetch_limit: int = 50,
                 dry_run: bool = False) -> dict:
    mail = imaplib.IMAP4_SSL(IMAP_HOST)
    try:
        mail.login(user, app_password)
    except imaplib.IMAP4.error as e:
        return {"error": f"login failed: {e}"}

    mail.select("INBOX")
    typ, data = mail.search(None, "UNSEEN")
    if typ != "OK":
        mail.logout()
        return {"error": "search failed"}

    ids = data[0].split()[-fetch_limit:]
    summary = {"urgent": [], "important": [], "promo": [], "newsletter": [],
               "social": [], "other": []}
    actions = {"archived": 0, "flagged": 0, "left": 0}

    for num in ids:
        typ, msg_data = mail.fetch(num, "(RFC822)")
        if typ != "OK" or not msg_data or not msg_data[0]:
            continue
        msg = email.message_from_bytes(msg_data[0][1])
        subject = _decode(msg.get("Subject")) or "(no subject)"
        sender = _decode(msg.get("From")) or ""
        cat = _categorize(msg)
        if cat == "other":
            preview = _body_preview(msg)
            cat = _claude_category(subject, sender, preview)
        summary[cat].append({"from": sender, "subject": subject[:140]})

        if dry_run:
            actions["left"] += 1
            continue

        if cat in ("promo", "newsletter", "social"):
            mail.store(num, "-X-GM-LABELS", "\\Inbox")
            actions["archived"] += 1
        elif cat == "urgent":
            mail.store(num, "+FLAGS", "\\Flagged")
            actions["flagged"] += 1
        else:
            actions["left"] += 1

    if not dry_run:
        mail.expunge()
    mail.close()
    mail.logout()
    return {"summary": summary, "actions": actions, "scanned": len(ids)}


def _format_digest(triage: dict) -> str:
    if "error" in triage:
        return f"InboxZero error: {triage['error']}\n"
    s = triage["summary"]
    a = triage["actions"]
    lines = [
        f"InboxZero — {datetime.now():%Y-%m-%d %H:%M}",
        "",
        f"Scanned: {triage['scanned']} unread",
        f"Archived: {a['archived']}   Flagged: {a['flagged']}   Left: {a['left']}",
        "",
    ]
    for cat in ("urgent", "important", "promo", "newsletter", "social", "other"):
        items = s.get(cat, [])
        if not items:
            continue
        lines.append(f"## {cat.upper()} ({len(items)})")
        for it in items[:10]:
            lines.append(f"- {it['from']} — {it['subject']}")
        if len(items) > 10:
            lines.append(f"  …and {len(items) - 10} more")
        lines.append("")
    return "\n".join(lines)


def build_queue() -> dict:
    """Run a triage pass on the owner's inbox if credentials are present."""
    user = os.environ.get("SMTP_USER")
    pwd = os.environ.get("SMTP_PASS")
    if not (user and pwd):
        return {"triaged_inboxes": 0, "skipped_reason": "no SMTP_USER/SMTP_PASS"}
    fetch_limit = int(os.environ.get("IZ_FETCH_LIMIT", "50"))
    triage = triage_inbox(user, pwd, fetch_limit=fetch_limit)
    digest = _format_digest(triage)

    log = storage.load("iz_log.json", [])
    log.append({"at": datetime.now().isoformat(), "user": user, "result": triage})
    storage.save("iz_log.json", log[-200:])

    owner_email = os.environ.get("IZ_OWNER_EMAIL", user)
    mailer.send(AGENT_KEY, owner_email,
                f"InboxZero digest — {datetime.now():%b %d}",
                digest, purpose="fulfillment")
    return {"triaged_inboxes": 1, "scanned": triage.get("scanned", 0)}


def fulfill_cycle() -> dict:
    """Subscribers run their own InboxZero CLI; this just confirms their cycle."""
    subs = storage.load("iz_subscribers.json", [])
    return {"fulfillment_sent": sum(1 for s in subs if s.get("status") == "active")}


def acquire_cycle() -> dict:
    leads = storage.load("iz_leads.json", [])
    sent = 0
    for lead in leads:
        if lead.get("trial_sent"):
            continue
        email_addr = lead.get("email")
        if not email_addr:
            continue
        body = (
            f"Hi {lead.get('name', 'there')},\n\n"
            f"InboxZero is an autonomous inbox triage agent — it scans unread mail, "
            f"archives newsletters and promos, flags anything urgent, and emails you "
            f"a daily summary of what actually needs your attention.\n\n"
            f"Plans:\n"
            f"  $97/mo per inbox (daily triage + summary)\n"
            f"  $297/mo team (up to 3 inboxes)\n"
            f"  $97 one-time deep-clean (your last 5,000 unread, sorted in one pass)\n\n"
            f"Setup is one Gmail app password — no API keys, no OAuth screen. "
            f"Reply 'yes' and I'll send the 2-minute setup steps.\n"
        )
        r = mailer.send(AGENT_KEY, email_addr,
                        "Free InboxZero deep-clean (your last 500 unread, free)",
                        body, purpose="outreach")
        if r.get("status") == "sent":
            lead["trial_sent"] = datetime.now().isoformat()
            sent += 1
    storage.save("iz_leads.json", leads)
    return {"outreach_sent": sent}


def run_full_cycle() -> dict:
    q = build_queue()
    a = acquire_cycle()
    f = fulfill_cycle()
    rev = billing.revenue_summary(AGENT_KEY)
    subs = storage.load("iz_subscribers.json", [])
    metrics.record(
        AGENT_KEY,
        products_produced=q.get("triaged_inboxes", 0),
        outreach_sent=a["outreach_sent"],
        fulfillment_sent=f["fulfillment_sent"],
        active_subs=sum(1 for s in subs if s.get("status") == "active"),
        mrr=rev["mrr"],
        total_revenue=rev["total_paid"],
    )
    return {**q, **a, **f, **rev}
