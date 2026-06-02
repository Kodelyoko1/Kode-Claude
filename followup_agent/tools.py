"""
Seller Follow-Up Sequence Agent tools.
Runs a 6-touch automated email sequence on every lead in the pipeline.
No API keys required — only SMTP.
"""
import json
import os
import sys
import smtplib
import datetime
import tempfile
from pathlib import Path
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
sys.path.insert(0, str(Path(__file__).parent.parent))
from email_template import send_branded_email

DATA_DIR   = Path(__file__).parent.parent / "data"
LEADS_FILE = DATA_DIR / "leads.json"
EMAIL_LOG  = DATA_DIR / "email_log.json"

COMPANY_NAME  = "Wholesale Omniverse LLC"
COMPANY_EMAIL = "info@wholesaleomniverse.com"
SENDER_PHONE  = "207-385-4041"
SENDER_NAME   = "Tyreese Lumiere"

# Days after last contact to send the next touch
FOLLOWUP_SCHEDULE = {
    0: 3,   # Stage 0 (initial sent) → Stage 1 after 3 days
    1: 4,   # Stage 1 → Stage 2 after 4 more days  (day 7 total)
    2: 7,   # Stage 2 → Stage 3 after 7 more days  (day 14 total)
    3: 7,   # Stage 3 → Stage 4 after 7 more days  (day 21 total)
    4: 9,   # Stage 4 → Stage 5 after 9 more days  (day 30 total)
    5: 30,  # Stage 5 → Stage 6 after 30 more days (day 60 total)
}

MAX_STAGE = 6  # After stage 6, lead is moved to "cold" — stop emailing

TEMPLATES = {
    1: {
        "subject": "Re: Your property at {address}",
        "body": """Hi {owner_name},

Quick follow-up on the property at {address}.

I run a free service that connects homeowners with cash real-estate investors — we put your property on a weekly list that goes to 100+ active cash buyers. They contact you directly with offers. No fees, no agents, no public listing.

If you're still thinking about selling, this is the fastest way to get real numbers from real buyers without paying anyone.

Want to be on next Monday's list? Just reply YES and confirm the address.

Tyreese Lumiere
{sender_email}
{phone}""",
    },
    2: {
        "subject": "100+ cash buyers active in {city} — your property?",
        "body": """Hi {owner_name},

We have a lot of active cash investors looking in {city} this week — flippers, buy-and-hold landlords, and BRRRR investors.

If you list {address} on our weekly buyers list, they'll see your property and contact you directly with cash offers. There's no charge to be listed — sellers never pay us a cent.

Most listed properties get 2–5 buyer inquiries within the first week.

Want me to add you to Monday's list? Reply YES and we'll get it done.

Tyreese Lumiere
{sender_email}
{phone}""",
    },
    3: {
        "subject": "Free cash-buyer exposure for {address}",
        "body": """Hi {owner_name},

I want to be direct with you about what we do.

We're not buying {address}. We're a marketplace. Cash investors pay us a monthly fee to receive our weekly list of off-market properties. Sellers list for free.

If you go on our list:
  • 100+ active cash buyers see your property
  • They call or email you directly with offers
  • You pick the best one — or pass on all of them
  • Zero cost to you, ever
  • No MLS, no agent, no public listing

If you're considering selling at all, this is no-risk. Reply YES with the property address and I'll add you to next week's drop.

Tyreese Lumiere
{sender_email}
{phone}""",
    },
    4: {
        "subject": "Final note about {address}",
        "body": """Hi {owner_name},

This is my last follow-up for now — don't want to keep filling your inbox.

If you ever decide to explore selling {address}, the free listing offer is open. Reply anytime and I'll get your property in front of cash buyers in {city} that same week.

Wishing you all the best.

Tyreese Lumiere
{sender_email}
{phone}
{company}""",
    },
    5: {
        "subject": "Checking back in — {address} in {city}",
        "body": """Hi {owner_name},

About a month since we last connected. The {city} cash-buyer market has been busy and we have new investors actively looking.

If anything has changed and you'd like to list {address} on our free seller marketplace, just reply YES.

Same offer: free for sellers, 100+ cash buyers see your property, they contact you directly. We're not in the contract — you negotiate offers yourself.

Tyreese Lumiere
{sender_email}
{phone}""",
    },
    6: {
        "subject": "{city} cash-buyer update — and your property",
        "body": """Hi {owner_name},

Quick update from the {city} cash-buyer market — demand is still strong, especially for distressed and off-market properties.

Circling back on {address}. If you've been on the fence, our free seller listing is still the easiest way to test the waters: list once, see what cash buyers offer, no obligation.

Reply YES and I'll add you to Monday's drop.

Tyreese Lumiere
{sender_email}
{phone}
{company}""",
    },
}


def _load(path, default):
    if not path.exists():
        return default
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"  warning: {path.name} unreadable ({e}); using default")
        return default


def _save(path, data):
    DATA_DIR.mkdir(exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except Exception:
        try: os.unlink(tmp)
        except OSError: pass
        raise


def _now():
    return datetime.datetime.now().isoformat()


def _days_since(iso_str: str) -> int:
    if not iso_str:
        return 9999
    try:
        dt = datetime.datetime.fromisoformat(iso_str)
        return (datetime.datetime.now() - dt).days
    except Exception:
        return 9999


def _body_to_html(body: str) -> str:
    """Convert plain text email body to HTML paragraphs for the branded template."""
    lines = body.strip().split("\n")
    html_parts = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if line.startswith("•") or line.startswith("-"):
            html_parts.append(
                f'<p style="margin:4px 0;padding-left:12px;">'
                f'<span style="color:#c9a84c;font-weight:bold;">&#10003;</span>&nbsp;'
                f'{line.lstrip("•- ").strip()}</p>'
            )
        else:
            html_parts.append(f'<p style="margin:0 0 14px 0;">{line}</p>')
    return "\n".join(html_parts)


def _send_smtp(to_email: str, subject: str, body: str) -> dict:
    return send_branded_email(
        to_email=to_email,
        subject=subject,
        body_text=body,
        body_html_inner=_body_to_html(body),
    )


def _log_email(lead_id: str, to: str, subject: str, stage: int, status: str):
    log = _load(EMAIL_LOG, [])
    log.append({
        "lead_id":  lead_id,
        "to":       to,
        "subject":  subject,
        "stage":    stage,
        "status":   status,
        "sent_at":  _now(),
        "type":     "followup",
    })
    _save(EMAIL_LOG, log)


def get_followup_summary() -> dict:
    """Show where all leads are in the follow-up sequence and how many are due today."""
    leads = _load(LEADS_FILE, {})
    active = [l for l in leads.values() if l.get("status") not in ("assigned", "dead", "cold")]

    stage_counts = {}
    due_today = []
    no_email = []

    for lead in active:
        stage = lead.get("followup_stage", 0)
        stage_counts[stage] = stage_counts.get(stage, 0) + 1

        if not lead.get("seller_email"):
            no_email.append(lead["lead_id"])
            continue

        if stage >= MAX_STAGE:
            continue

        days_needed = FOLLOWUP_SCHEDULE.get(stage, 999)
        last_contact = lead.get("last_followup_at") or lead.get("created_at", "")
        days_waited  = _days_since(last_contact)

        if days_waited >= days_needed:
            due_today.append({
                "lead_id":    lead["lead_id"],
                "address":    lead.get("address", ""),
                "city":       lead.get("city", ""),
                "owner":      lead.get("seller_name", ""),
                "email":      lead.get("seller_email", ""),
                "stage":      stage,
                "next_stage": stage + 1,
                "days_waited": days_waited,
            })

    return {
        "total_active_leads":   len(active),
        "no_email_on_file":     len(no_email),
        "due_for_followup":     len(due_today),
        "leads_due":            due_today[:20],
        "by_stage":             stage_counts,
        "max_stage":            MAX_STAGE,
        "schedule_days":        FOLLOWUP_SCHEDULE,
    }


def send_followup_email(lead_id: str) -> dict:
    """Send the next follow-up email to a single lead and advance their stage."""
    leads = _load(LEADS_FILE, {})
    if lead_id not in leads:
        return {"error": f"Lead {lead_id} not found."}

    lead  = leads[lead_id]
    stage = lead.get("followup_stage", 0)

    if stage >= MAX_STAGE:
        return {"status": "skipped", "reason": f"Lead is at max stage ({MAX_STAGE}). Moving to cold."}

    to_email = lead.get("seller_email", "")
    if not to_email:
        return {"status": "skipped", "reason": "No email on file for this lead."}

    next_stage = stage + 1
    template   = TEMPLATES.get(next_stage)
    if not template:
        return {"status": "skipped", "reason": f"No template for stage {next_stage}."}

    smtp_user = os.environ.get("SMTP_USER", COMPANY_EMAIL)
    subject = template["subject"].format(
        address=lead.get("address", "your property"),
        city=lead.get("city", ""),
        owner_name=lead.get("seller_name", "there"),
    )
    body = template["body"].format(
        owner_name=lead.get("seller_name", "there"),
        address=lead.get("address", "your property"),
        city=lead.get("city", ""),
        sender_email=smtp_user,
        phone=SENDER_PHONE,
        company=COMPANY_NAME,
    )

    result = _send_smtp(to_email, subject, body)

    # Update lead stage regardless of send status (tracks attempt)
    leads[lead_id]["followup_stage"]   = next_stage
    leads[lead_id]["last_followup_at"] = _now()
    leads[lead_id]["updated_at"]       = _now()
    if lead.get("status") == "new":
        leads[lead_id]["status"] = "contacted"
    _save(LEADS_FILE, leads)
    _log_email(lead_id, to_email, subject, next_stage, result["status"])

    return {
        "lead_id":    lead_id,
        "address":    lead.get("address"),
        "owner":      lead.get("seller_name"),
        "stage":      next_stage,
        "email_sent": result["status"] == "sent",
        "status":     result["status"],
    }


def run_all_due_followups(limit: int = 100) -> dict:
    """Send follow-up emails to every lead that is due today. Core autonomous action."""
    summary = get_followup_summary()
    due     = summary.get("leads_due", [])[:limit]

    if not due:
        return {
            "status":  "nothing_due",
            "message": f"No leads are due for follow-up today. Total active: {summary['total_active_leads']}.",
        }

    sent    = []
    skipped = []
    failed  = []

    for item in due:
        result = send_followup_email(item["lead_id"])
        if result.get("email_sent"):
            sent.append(result)
        elif result.get("status") == "skipped":
            skipped.append(result)
        else:
            failed.append(result)

    return {
        "total_due":    len(due),
        "sent":         len(sent),
        "skipped":      len(skipped),
        "failed":       len(failed),
        "sent_details": sent,
        "failed_leads": [f.get("lead_id") for f in failed],
    }


def mark_seller_responded(lead_id: str, notes: str = "", new_status: str = "negotiating") -> dict:
    """Mark a seller as having responded — moves them to negotiating and stops the sequence."""
    leads = _load(LEADS_FILE, {})
    if lead_id not in leads:
        return {"error": f"Lead {lead_id} not found."}

    leads[lead_id]["status"]           = new_status
    leads[lead_id]["seller_responded"] = True
    leads[lead_id]["updated_at"]       = _now()
    if notes:
        existing = leads[lead_id].get("notes", "")
        leads[lead_id]["notes"] = (existing + f"\n[{_now()[:10]}] RESPONDED: {notes}").strip()
    _save(LEADS_FILE, leads)

    return {
        "status":   "updated",
        "lead_id":  lead_id,
        "address":  leads[lead_id].get("address"),
        "new_status": new_status,
        "message":  "Lead moved to negotiating. Follow-up sequence paused.",
    }


def get_hot_leads() -> dict:
    """Get all leads that have responded or are in negotiation/under contract."""
    leads = _load(LEADS_FILE, {})
    hot   = [
        l for l in leads.values()
        if l.get("status") in ("negotiating", "under_contract") or l.get("seller_responded")
    ]
    hot.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
    return {"hot_leads": hot, "count": len(hot)}


def mark_lead_cold(lead_id: str) -> dict:
    """Stop following up on a lead — mark as cold."""
    leads = _load(LEADS_FILE, {})
    if lead_id not in leads:
        return {"error": f"Lead {lead_id} not found."}
    leads[lead_id]["status"]     = "cold"
    leads[lead_id]["updated_at"] = _now()
    _save(LEADS_FILE, leads)
    return {"status": "marked_cold", "lead_id": lead_id}


def get_sequence_stats() -> dict:
    """Full stats on the follow-up sequence performance."""
    leads = _load(LEADS_FILE, {})
    log   = _load(EMAIL_LOG, [])

    followup_emails = [e for e in log if e.get("type") == "followup"]
    responded = [l for l in leads.values() if l.get("seller_responded")]
    negotiating = [l for l in leads.values() if l.get("status") == "negotiating"]
    under_contract = [l for l in leads.values() if l.get("status") == "under_contract"]
    cold = [l for l in leads.values() if l.get("status") == "cold"]

    stage_sends = {}
    for e in followup_emails:
        s = e.get("stage", 0)
        stage_sends[s] = stage_sends.get(s, 0) + 1

    return {
        "total_followup_emails_sent":  len(followup_emails),
        "sellers_responded":           len(responded),
        "in_negotiation":              len(negotiating),
        "under_contract":              len(under_contract),
        "cold_leads":                  len(cold),
        "response_rate_pct":           round(len(responded) / max(len(followup_emails), 1) * 100, 1),
        "emails_per_stage":            stage_sends,
    }


TOOLS = [
    {
        "name": "get_followup_summary",
        "description": "Show all leads due for follow-up today, their stage in the sequence, and totals. Run this first.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "run_all_due_followups",
        "description": "Send follow-up emails to every lead that is due today based on the schedule. Core autonomous action.",
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Max emails to send in one run (default 100)", "default": 100},
            },
        },
    },
    {
        "name": "send_followup_email",
        "description": "Send the next follow-up email to a single lead and advance their stage.",
        "input_schema": {
            "type": "object",
            "properties": {
                "lead_id": {"type": "string"},
            },
            "required": ["lead_id"],
        },
    },
    {
        "name": "mark_seller_responded",
        "description": "Mark a seller as having responded — stops the sequence and moves them to negotiating.",
        "input_schema": {
            "type": "object",
            "properties": {
                "lead_id":    {"type": "string"},
                "notes":      {"type": "string", "description": "What the seller said"},
                "new_status": {"type": "string", "description": "negotiating, under_contract", "default": "negotiating"},
            },
            "required": ["lead_id"],
        },
    },
    {
        "name": "get_hot_leads",
        "description": "Get all leads that have responded or are in negotiation or under contract.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "mark_lead_cold",
        "description": "Stop following up on a lead — mark as cold and remove from sequence.",
        "input_schema": {
            "type": "object",
            "properties": {"lead_id": {"type": "string"}},
            "required": ["lead_id"],
        },
    },
    {
        "name": "get_sequence_stats",
        "description": "Full performance stats: emails sent per stage, response rate, deals in pipeline.",
        "input_schema": {"type": "object", "properties": {}},
    },
]

TOOL_FUNCTIONS = {
    "get_followup_summary":   get_followup_summary,
    "run_all_due_followups":  run_all_due_followups,
    "send_followup_email":    send_followup_email,
    "mark_seller_responded":  mark_seller_responded,
    "get_hot_leads":          get_hot_leads,
    "mark_lead_cold":         mark_lead_cold,
    "get_sequence_stats":     get_sequence_stats,
}
