import json
import os
import sys
import smtplib
import datetime
from pathlib import Path
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
sys.path.insert(0, str(Path(__file__).parent.parent))
from email_template import send_branded_email

# Import parent prospecting + email tools
sys.path.insert(0, str(Path(__file__).parent.parent))
import tools as parent_tools

DATA_DIR = Path(__file__).parent.parent / "data"
OAS_CLIENTS_FILE = DATA_DIR / "outreach_clients.json"
OAS_CAMPAIGNS_FILE = DATA_DIR / "outreach_campaigns.json"

COMPANY_NAME = "Wholesale Omniverse LLC"
COMPANY_EMAIL = "info@wholesaleomniverse.com"

SERVICE_TIERS = {
    "basic":    {"price": 300,  "campaigns_per_month": 2,  "markets": 1, "label": "Basic"},
    "standard": {"price": 500,  "campaigns_per_month": 4,  "markets": 2, "label": "Standard"},
    "premium":  {"price": 800,  "campaigns_per_month": 8,  "markets": 4, "label": "Premium"},
}


def _load(path: Path, default):
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return default


def _save(path: Path, data):
    DATA_DIR.mkdir(exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def _now():
    return datetime.datetime.now().isoformat()


def _billing_date_from_now(days: int = 30) -> str:
    return (datetime.datetime.now() + datetime.timedelta(days=days)).strftime("%Y-%m-%d")


def register_outreach_client(
    name: str,
    email: str,
    tier: str = "basic",
    target_markets: list = None,
    company: str = "",
    phone: str = "",
    notes: str = "",
) -> dict:
    """Register a new outreach retainer client. Tiers: basic ($300/mo), standard ($500/mo), premium ($800/mo)."""
    tier = tier.lower()
    if tier not in SERVICE_TIERS:
        return {"error": f"Invalid tier. Choose from: {', '.join(SERVICE_TIERS.keys())}"}

    clients = _load(OAS_CLIENTS_FILE, {})
    for c in clients.values():
        if c.get("email", "").lower() == email.lower():
            return {"error": f"Client {email} already exists.", "client_id": c["client_id"]}

    client_id = f"OAS-{len(clients)+1:04d}"
    tier_info = SERVICE_TIERS[tier]

    clients[client_id] = {
        "client_id": client_id,
        "name": name,
        "email": email,
        "company": company or name,
        "phone": phone,
        "tier": tier,
        "monthly_fee": tier_info["price"],
        "campaigns_per_month": tier_info["campaigns_per_month"],
        "target_markets": target_markets or [],
        "status": "active",
        "campaigns_run_this_month": 0,
        "total_campaigns_run": 0,
        "total_leads_found": 0,
        "total_emails_sent": 0,
        "total_revenue": 0,
        "payments_collected": 0,
        "notes": notes,
        "created_at": _now(),
        "next_billing_date": _billing_date_from_now(30),
    }
    _save(OAS_CLIENTS_FILE, clients)
    return {
        "status": "registered",
        "client_id": client_id,
        "name": name,
        "tier": tier_info["label"],
        "monthly_fee": tier_info["price"],
        "campaigns_per_month": tier_info["campaigns_per_month"],
        "markets": target_markets or [],
    }


def get_outreach_clients(status: str = "") -> dict:
    """List all outreach retainer clients."""
    clients = _load(OAS_CLIENTS_FILE, {})
    items = list(clients.values())
    if status:
        items = [c for c in items if c.get("status", "").lower() == status.lower()]
    items.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return {"clients": items, "count": len(items)}


def run_client_campaign(
    client_id: str,
    record_type: str = "tax_delinquent",
    max_prospects: int = 15,
    auto_email: bool = True,
) -> dict:
    """
    Run a full prospecting + outreach campaign for a retainer client across all their target markets.
    Requires payment. record_type: tax_delinquent, code_violations, foreclosure, probate, vacant
    """
    from paywall.gate import require_payment
    gate = require_payment(client_id)
    if not gate.get("allowed"):
        return {
            "error": "Payment required before running campaigns.",
            "payment_url": gate.get("payment_url", ""),
            "amount_due": gate.get("amount_due"),
            "client": gate.get("client_name", ""),
        }

    clients = _load(OAS_CLIENTS_FILE, {})
    if client_id not in clients:
        return {"error": f"Client {client_id} not found."}

    c = clients[client_id]
    if c.get("status") != "active":
        return {"error": f"Client {client_id} is not active."}

    markets = c.get("target_markets", [])
    if not markets:
        return {"error": "No target markets configured for this client. Add markets first."}

    campaigns = _load(OAS_CAMPAIGNS_FILE, [])
    campaign_id = f"CAMP-{len(campaigns)+1:04d}"

    all_results = []
    total_leads = 0
    total_emailed = 0

    for market in markets:
        city = market.get("city", "")
        state = market.get("state", "")
        if not city or not state:
            continue

        # Run government records prospecting
        gov_result = parent_tools.prospect_from_government_records(
            city=city,
            state=state,
            record_type=record_type,
            max_prospects=max_prospects,
            auto_email=auto_email,
        )

        # Run Redfin motivated seller scrape
        redfin_result = parent_tools.scrape_craigslist_leads(
            city=city,
            state=state,
            max_results=max_prospects,
            auto_email=False,
        )

        market_leads = gov_result.get("total_prospects_found", 0) + redfin_result.get("leads_found", 0)
        market_emailed = len(gov_result.get("auto_emailed", []))

        total_leads += market_leads
        total_emailed += market_emailed

        all_results.append({
            "city": city,
            "state": state,
            "gov_records_leads": gov_result.get("total_prospects_found", 0),
            "redfin_leads": redfin_result.get("leads_found", 0),
            "emails_sent": market_emailed,
            "record_type": record_type,
        })

    campaign = {
        "campaign_id": campaign_id,
        "client_id": client_id,
        "client_name": c["name"],
        "record_type": record_type,
        "markets_hit": len(all_results),
        "total_leads_found": total_leads,
        "total_emails_sent": total_emailed,
        "market_breakdown": all_results,
        "ran_at": _now(),
    }
    campaigns.append(campaign)
    _save(OAS_CAMPAIGNS_FILE, campaigns)

    # Update client stats
    clients[client_id]["campaigns_run_this_month"] = c.get("campaigns_run_this_month", 0) + 1
    clients[client_id]["total_campaigns_run"] = c.get("total_campaigns_run", 0) + 1
    clients[client_id]["total_leads_found"] = c.get("total_leads_found", 0) + total_leads
    clients[client_id]["total_emails_sent"] = c.get("total_emails_sent", 0) + total_emailed
    clients[client_id]["last_campaign_at"] = _now()
    _save(OAS_CLIENTS_FILE, clients)

    return {
        "campaign_id": campaign_id,
        "client": c["name"],
        "markets_hit": len(all_results),
        "total_leads_found": total_leads,
        "total_emails_sent": total_emailed,
        "breakdown": all_results,
    }


def run_all_active_campaigns(record_type: str = "tax_delinquent", auto_email: bool = True) -> dict:
    """Run campaigns for ALL active retainer clients at once. Used in autonomous mode."""
    clients = _load(OAS_CLIENTS_FILE, {})
    active = [c for c in clients.values() if c.get("status") == "active"]

    if not active:
        return {"status": "no_active_clients", "message": "No active outreach clients to run campaigns for."}

    results = []
    for c in active:
        result = run_client_campaign(
            client_id=c["client_id"],
            record_type=record_type,
            auto_email=auto_email,
        )
        results.append({"client": c["name"], "client_id": c["client_id"], **result})

    total_leads = sum(r.get("total_leads_found", 0) for r in results)
    total_emailed = sum(r.get("total_emails_sent", 0) for r in results)

    return {
        "clients_served": len(results),
        "total_leads_found": total_leads,
        "total_emails_sent": total_emailed,
        "per_client": results,
    }


def get_campaign_report(client_id: str = "", last_n: int = 5) -> dict:
    """Get campaign history and performance for a client (or all clients if no client_id)."""
    campaigns = _load(OAS_CAMPAIGNS_FILE, [])
    if client_id:
        campaigns = [c for c in campaigns if c.get("client_id") == client_id]
    campaigns = sorted(campaigns, key=lambda x: x.get("ran_at", ""), reverse=True)[:last_n]

    total_leads = sum(c.get("total_leads_found", 0) for c in campaigns)
    total_emailed = sum(c.get("total_emails_sent", 0) for c in campaigns)

    return {
        "campaigns": campaigns,
        "count": len(campaigns),
        "total_leads_found": total_leads,
        "total_emails_sent": total_emailed,
    }


def get_service_revenue() -> dict:
    """Total MRR, ARR, and performance across all outreach retainer clients."""
    clients = _load(OAS_CLIENTS_FILE, {})
    active = [c for c in clients.values() if c.get("status") == "active"]
    mrr = sum(c.get("monthly_fee", 0) for c in active)
    arr = mrr * 12
    total_collected = sum(c.get("total_revenue", 0) for c in clients.values())

    by_tier = {}
    for t, info in SERVICE_TIERS.items():
        tier_clients = [c for c in active if c.get("tier") == t]
        by_tier[info["label"]] = {"count": len(tier_clients), "revenue": len(tier_clients) * info["price"]}

    upcoming_renewals = [
        {"client": c["name"], "email": c["email"], "amount": c["monthly_fee"], "date": c.get("next_billing_date", "")}
        for c in active
        if c.get("next_billing_date", "") <= (datetime.date.today() + datetime.timedelta(days=7)).isoformat()
    ]

    return {
        "mrr": mrr,
        "arr": arr,
        "total_collected_all_time": total_collected,
        "active_clients": len(active),
        "by_tier": by_tier,
        "upcoming_renewals_7_days": upcoming_renewals,
        "total_leads_generated_all_time": sum(c.get("total_leads_found", 0) for c in clients.values()),
        "total_emails_sent_all_time": sum(c.get("total_emails_sent", 0) for c in clients.values()),
    }


def record_outreach_payment(client_id: str, amount: float = 0, notes: str = "") -> dict:
    """Record a payment received from a retainer client."""
    clients = _load(OAS_CLIENTS_FILE, {})
    if client_id not in clients:
        return {"error": f"Client {client_id} not found."}

    c = clients[client_id]
    if amount == 0:
        amount = c.get("monthly_fee", 0)

    clients[client_id]["payments_collected"] = c.get("payments_collected", 0) + 1
    clients[client_id]["total_revenue"] = round(c.get("total_revenue", 0) + amount, 2)
    clients[client_id]["next_billing_date"] = _billing_date_from_now(30)
    if notes:
        clients[client_id]["notes"] = (c.get("notes", "") + f"\n[{_now()[:10]}] Payment ${amount}: {notes}").strip()
    clients[client_id]["updated_at"] = _now()
    _save(OAS_CLIENTS_FILE, clients)
    return {
        "status": "payment_recorded",
        "client_id": client_id,
        "amount": amount,
        "total_revenue_from_client": clients[client_id]["total_revenue"],
        "next_billing_date": clients[client_id]["next_billing_date"],
    }


def send_campaign_report_email(client_id: str) -> dict:
    """Email a retainer client their latest campaign results report. Requires payment."""
    from paywall.gate import require_payment
    gate = require_payment(client_id)
    if not gate.get("allowed"):
        return {
            "error": "Payment required before sending reports.",
            "payment_url": gate.get("payment_url", ""),
            "amount_due": gate.get("amount_due"),
        }

    clients = _load(OAS_CLIENTS_FILE, {})
    if client_id not in clients:
        return {"error": f"Client {client_id} not found."}

    c = clients[client_id]
    report = get_campaign_report(client_id=client_id, last_n=3)
    campaigns = report.get("campaigns", [])

    campaign_lines = ""
    for camp in campaigns:
        date = camp.get("ran_at", "")[:10]
        markets_str = ", ".join(
            f"{b['city']} {b['state']}" for b in camp.get("market_breakdown", [])
        )
        campaign_lines += (
            f"  {date} — {camp.get('total_leads_found', 0)} leads found, "
            f"{camp.get('total_emails_sent', 0)} emails sent ({markets_str})\n"
        )

    subject = f"Your Wholesale Omniverse Outreach Report — {datetime.datetime.now().strftime('%B %Y')}"
    body = f"""Hi {c['name'].split()[0]},

Here's your outreach campaign update for {datetime.datetime.now().strftime('%B %Y')}:

RECENT CAMPAIGNS:
{campaign_lines or '  No campaigns run yet.'}

ALL-TIME STATS:
  Total campaigns run:  {c.get('total_campaigns_run', 0)}
  Total leads found:    {c.get('total_leads_found', 0)}
  Total emails sent:    {c.get('total_emails_sent', 0)}

Your leads are being added to your pipeline and emailed automatically. You can follow up with any hot prospects directly or we can manage that for you.

Questions? Reply to this email.

— Tyreese Lumiere
{COMPANY_NAME}
{COMPANY_EMAIL}
207-385-4041
"""

    # Build HTML version
    body_html = f"""
        <p>Hi <strong>{c['name'].split()[0]}</strong>,</p>
        <p>Here's your outreach campaign update for
           <strong>{datetime.datetime.now().strftime('%B %Y')}</strong>:</p>
        <p><strong style="color:#c9a84c;">Recent Campaigns</strong></p>
        <p style="font-family:monospace;background:#f9f9f9;padding:12px;
                  border-left:3px solid #c9a84c;font-size:13px;">
          {campaign_lines.replace(chr(10),'<br>') or 'No campaigns run yet.'}
        </p>
        <p><strong style="color:#c9a84c;">All-Time Stats</strong></p>
        <table cellpadding="4" cellspacing="0" style="font-size:14px;">
          <tr><td>Total campaigns run:</td><td><strong>{c.get('total_campaigns_run',0)}</strong></td></tr>
          <tr><td>Total leads found:</td><td><strong>{c.get('total_leads_found',0)}</strong></td></tr>
          <tr><td>Total emails sent:</td><td><strong>{c.get('total_emails_sent',0)}</strong></td></tr>
        </table>
        <p style="margin-top:20px;">Your leads are being added to your pipeline and emailed
           automatically. Reply to this email with any questions.</p>
    """

    result = send_branded_email(
        to_email=c["email"],
        subject=subject,
        body_text=body,
        body_html_inner=body_html,
    )

    if result["status"] != "sent" and result.get("status") == "smtp_not_configured":
        return {
            "status": "smtp_not_configured",
            "message": "Set SMTP_HOST, SMTP_USER, SMTP_PASS to send reports.",
            "report_preview": body[:400],
        }

    return {"status": result["status"], "to": c["email"], "subject": subject}


def update_outreach_client(client_id: str, status: str = "", tier: str = "", markets: list = None, notes: str = "") -> dict:
    """Update a client's status, tier, or markets."""
    clients = _load(OAS_CLIENTS_FILE, {})
    if client_id not in clients:
        return {"error": f"Client {client_id} not found."}

    c = clients[client_id]
    if status:
        c["status"] = status.lower()
    if tier:
        tier = tier.lower()
        if tier not in SERVICE_TIERS:
            return {"error": f"Invalid tier. Choose from: {', '.join(SERVICE_TIERS.keys())}"}
        c["tier"] = tier
        c["monthly_fee"] = SERVICE_TIERS[tier]["price"]
        c["campaigns_per_month"] = SERVICE_TIERS[tier]["campaigns_per_month"]
    if markets is not None:
        c["target_markets"] = markets
    if notes:
        c["notes"] = (c.get("notes", "") + f"\n[{_now()[:10]}] {notes}").strip()
    c["updated_at"] = _now()
    _save(OAS_CLIENTS_FILE, clients)
    return {"status": "updated", "client_id": client_id}


TOOLS = [
    {
        "name": "register_outreach_client",
        "description": "Register a new outreach retainer client. Tiers: basic ($300/mo, 1 market, 2 campaigns/mo), standard ($500/mo, 2 markets, 4 campaigns/mo), premium ($800/mo, 4 markets, 8 campaigns/mo).",
        "input_schema": {
            "type": "object",
            "properties": {
                "name":    {"type": "string"},
                "email":   {"type": "string"},
                "tier":    {"type": "string", "description": "basic, standard, or premium"},
                "target_markets": {
                    "type": "array",
                    "items": {"type": "object", "properties": {"city": {"type": "string"}, "state": {"type": "string"}}},
                    "description": "List of {city, state} dicts",
                },
                "company": {"type": "string"},
                "phone":   {"type": "string"},
                "notes":   {"type": "string"},
            },
            "required": ["name", "email"],
        },
    },
    {
        "name": "get_outreach_clients",
        "description": "List all outreach retainer clients. Filter by status.",
        "input_schema": {
            "type": "object",
            "properties": {"status": {"type": "string", "description": "active, paused, or cancelled"}},
        },
    },
    {
        "name": "run_client_campaign",
        "description": "Run a full prospecting + email outreach campaign for a single retainer client across their target markets.",
        "input_schema": {
            "type": "object",
            "properties": {
                "client_id":     {"type": "string"},
                "record_type":   {"type": "string", "description": "tax_delinquent, code_violations, foreclosure, probate, or vacant", "default": "tax_delinquent"},
                "max_prospects": {"type": "integer", "description": "Max leads per market", "default": 15},
                "auto_email":    {"type": "boolean", "description": "Automatically send outreach emails", "default": True},
            },
            "required": ["client_id"],
        },
    },
    {
        "name": "run_all_active_campaigns",
        "description": "Run campaigns for ALL active retainer clients at once. Used in autonomous mode.",
        "input_schema": {
            "type": "object",
            "properties": {
                "record_type": {"type": "string", "default": "tax_delinquent"},
                "auto_email":  {"type": "boolean", "default": True},
            },
        },
    },
    {
        "name": "get_campaign_report",
        "description": "Get campaign history and performance. Pass client_id for a specific client, or omit for all.",
        "input_schema": {
            "type": "object",
            "properties": {
                "client_id": {"type": "string"},
                "last_n":    {"type": "integer", "default": 5},
            },
        },
    },
    {
        "name": "get_service_revenue",
        "description": "Full revenue report: MRR, ARR, clients by tier, upcoming renewals, and all-time stats.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "record_outreach_payment",
        "description": "Record a payment received from a retainer client and extend their billing cycle.",
        "input_schema": {
            "type": "object",
            "properties": {
                "client_id": {"type": "string"},
                "amount":    {"type": "number"},
                "notes":     {"type": "string"},
            },
            "required": ["client_id"],
        },
    },
    {
        "name": "send_campaign_report_email",
        "description": "Email a retainer client their latest campaign performance report.",
        "input_schema": {
            "type": "object",
            "properties": {"client_id": {"type": "string"}},
            "required": ["client_id"],
        },
    },
    {
        "name": "update_outreach_client",
        "description": "Update a client's status, tier, target markets, or notes.",
        "input_schema": {
            "type": "object",
            "properties": {
                "client_id": {"type": "string"},
                "status":    {"type": "string"},
                "tier":      {"type": "string"},
                "markets":   {"type": "array", "items": {"type": "object"}},
                "notes":     {"type": "string"},
            },
            "required": ["client_id"],
        },
    },
]

TOOL_FUNCTIONS = {
    "register_outreach_client":   register_outreach_client,
    "get_outreach_clients":       get_outreach_clients,
    "run_client_campaign":        run_client_campaign,
    "run_all_active_campaigns":   run_all_active_campaigns,
    "get_campaign_report":        get_campaign_report,
    "get_service_revenue":        get_service_revenue,
    "record_outreach_payment":    record_outreach_payment,
    "send_campaign_report_email": send_campaign_report_email,
    "update_outreach_client":     update_outreach_client,
}

# Merge paywall tools
from paywall.tools import TOOLS as _PAYWALL_TOOLS, TOOL_FUNCTIONS as _PAYWALL_FNS
TOOLS = TOOLS + _PAYWALL_TOOLS
TOOL_FUNCTIONS = {**TOOL_FUNCTIONS, **_PAYWALL_FNS}
