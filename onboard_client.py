#!/usr/bin/env python3
"""
Real client onboarding — single entry point for paying customers.

Flow:
  1. Pick service (SAAS plan: starter/pro/enterprise OR OAS tier: basic/standard/premium)
  2. Collect client info (name, email, phone, markets)
  3. Create client record in saas_clients.json or outreach_clients.json
  4. Generate PayPal invoice (or PayPal.me fallback)
  5. Email branded welcome message with payment link

Usage:
  python3 onboard_client.py                    # interactive wizard
  python3 onboard_client.py --list             # list real clients + MRR
  python3 onboard_client.py --status CLIENT_ID # check payment status
  python3 onboard_client.py --activate CLIENT_ID --method zelle
                                                # manual activate (cash/Zelle/Venmo)
"""
import argparse
import datetime
import json
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt, Confirm
from rich.table import Table
from rich.text import Text

from email_template import send_branded_email
from paywall.gate import (
    _load, _save, create_client_paywall, verify_payment,
    list_pending_payments, SAAS_CLIENTS_FILE, OAS_CLIENTS_FILE,
)

console = Console()

SAAS_PLANS = {
    "starter":    {"price": 97,  "markets": 1, "label": "Starter — 1 market, daily deal alerts"},
    "pro":        {"price": 197, "markets": 3, "label": "Pro — 3 markets, full deal analysis + LOIs"},
    "enterprise": {"price": 397, "markets": 99, "label": "Enterprise — unlimited markets, priority support"},
}

OAS_TIERS = {
    "basic":    {"price": 300, "markets": 1, "campaigns": 2, "label": "Basic — 1 market, 2 campaigns/mo"},
    "standard": {"price": 500, "markets": 2, "campaigns": 4, "label": "Standard — 2 markets, 4 campaigns/mo"},
    "premium":  {"price": 800, "markets": 4, "campaigns": 8, "label": "Premium — 4 markets, 8 campaigns/mo"},
}


def _now():
    return datetime.datetime.now().isoformat()


def _next_billing(days: int = 30) -> str:
    return (datetime.datetime.now() + datetime.timedelta(days=days)).strftime("%Y-%m-%d")


def _next_client_id(prefix: str, clients: dict) -> str:
    n = len(clients) + 1
    while f"{prefix}-{n:04d}" in clients:
        n += 1
    return f"{prefix}-{n:04d}"


def register_saas_client(name, email, plan, markets, phone="", notes=""):
    plan = plan.lower()
    if plan not in SAAS_PLANS:
        return {"error": f"Invalid SAAS plan. Choose: {', '.join(SAAS_PLANS)}"}
    clients = _load(SAAS_CLIENTS_FILE)
    for c in clients.values():
        if c.get("email", "").lower() == email.lower():
            return {"error": f"Client {email} already exists.", "client_id": c["client_id"]}
    cid = _next_client_id("SAAS", clients)
    info = SAAS_PLANS[plan]
    clients[cid] = {
        "client_id": cid,
        "name": name,
        "email": email,
        "phone": phone,
        "plan": plan,
        "monthly_fee": info["price"],
        "max_markets": info["markets"],
        "markets": markets,
        "status": "pending_payment",
        "onboarding_complete": True,
        "payments_collected": 0,
        "total_revenue": 0,
        "notes": notes,
        "created_at": _now(),
        "next_billing_date": _next_billing(30),
        "updated_at": _now(),
        "payment_url": "",
        "payment_verified": False,
    }
    _save(SAAS_CLIENTS_FILE, clients)
    return {"client_id": cid, "monthly_fee": info["price"]}


def register_oas_client(name, email, tier, markets, company="", phone="", notes=""):
    tier = tier.lower()
    if tier not in OAS_TIERS:
        return {"error": f"Invalid OAS tier. Choose: {', '.join(OAS_TIERS)}"}
    clients = _load(OAS_CLIENTS_FILE)
    for c in clients.values():
        if c.get("email", "").lower() == email.lower():
            return {"error": f"Client {email} already exists.", "client_id": c["client_id"]}
    cid = _next_client_id("OAS", clients)
    info = OAS_TIERS[tier]
    clients[cid] = {
        "client_id": cid,
        "name": name,
        "email": email,
        "company": company or name,
        "phone": phone,
        "tier": tier,
        "monthly_fee": info["price"],
        "campaigns_per_month": info["campaigns"],
        "target_markets": markets,
        "status": "pending_payment",
        "campaigns_run_this_month": 0,
        "total_campaigns_run": 0,
        "total_leads_found": 0,
        "total_emails_sent": 0,
        "total_revenue": 0,
        "payments_collected": 0,
        "notes": notes,
        "created_at": _now(),
        "next_billing_date": _next_billing(30),
        "updated_at": _now(),
        "payment_url": "",
        "payment_verified": False,
    }
    _save(OAS_CLIENTS_FILE, clients)
    return {"client_id": cid, "monthly_fee": info["price"]}


def send_welcome_email(client_id: str, payment_url: str, amount: float) -> dict:
    client, _, _ = _client_lookup(client_id)
    if not client:
        return {"status": "client_not_found"}
    name = client["name"]
    service = (
        f"{client.get('plan', '').title()} Plan"
        if client_id.startswith("SAAS-")
        else f"{client.get('tier', '').title()} Outreach Tier"
    )
    subject = f"Welcome to Wholesale Omniverse — Activate your {service}"
    body_text = (
        f"Hi {name},\n\n"
        f"Thanks for choosing Wholesale Omniverse. Your {service} subscription is ready to go.\n\n"
        f"Please complete your first payment of ${amount:.0f} here:\n{payment_url}\n\n"
        f"Once payment clears, your client account ({client_id}) will be activated automatically and "
        f"you'll start receiving deliverables within 24 hours.\n\n"
        f"Reply to this email if you have any questions.\n\n"
        f"— Tyreese Lumiere, Wholesale Omniverse LLC"
    )
    body_html = (
        f"Hi <strong>{name}</strong>,<br><br>"
        f"Thanks for choosing Wholesale Omniverse. Your <strong>{service}</strong> subscription is ready to go.<br><br>"
        f"Please complete your first payment of <strong>${amount:.0f}</strong> using the button below:<br><br>"
        f'<a href="{payment_url}" style="display:inline-block;padding:12px 28px;background:#f59e0b;color:#0f172a;'
        f'font-weight:700;text-decoration:none;border-radius:6px;">Pay ${amount:.0f} Now</a><br><br>'
        f"Or paste this link into your browser: <a href=\"{payment_url}\" style=\"color:#f59e0b;\">{payment_url}</a><br><br>"
        f"Once payment clears, your client account (<strong>{client_id}</strong>) will be activated and "
        f"you'll start receiving deliverables within 24 hours.<br><br>"
        f"Reply to this email if you have any questions."
    )
    return send_branded_email(
        to_email=client["email"],
        subject=subject,
        body_text=body_text,
        body_html_inner=body_html,
    )


def _client_lookup(client_id: str):
    if client_id.startswith("SAAS-"):
        clients = _load(SAAS_CLIENTS_FILE)
        return clients.get(client_id), clients, SAAS_CLIENTS_FILE
    if client_id.startswith("OAS-"):
        clients = _load(OAS_CLIENTS_FILE)
        return clients.get(client_id), clients, OAS_CLIENTS_FILE
    return None, {}, None


def _prompt_markets() -> list:
    raw = Prompt.ask("  Target markets (e.g. 'Detroit, MI; Atlanta, GA' — semicolon-separated)").strip()
    out = []
    for chunk in raw.split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "," in chunk:
            city, state = chunk.split(",", 1)
        elif " " in chunk:
            parts = chunk.rsplit(" ", 1)
            city, state = parts[0], parts[1]
        else:
            city, state = chunk, ""
        out.append({"city": city.strip(), "state": state.strip().upper()})
    return out


def cmd_new():
    console.print(Panel(
        Text.from_markup(
            "[bold]New Client Onboarding[/bold]\n\n"
            "  Pick a service:\n"
            "    [yellow]saas[/yellow]  — Wholesale Deal Analyzer subscription (self-serve software)\n"
            "    [yellow]oas[/yellow]   — Outreach-as-a-Service (we run the campaigns for them)"
        ),
        border_style="blue",
        title="Wholesale Omniverse",
    ))

    service = Prompt.ask("  Service type", choices=["saas", "oas"], default="saas")

    if service == "saas":
        console.print("\n  [dim]SAAS Plans:[/dim]")
        for k, v in SAAS_PLANS.items():
            console.print(f"    [yellow]{k:11}[/yellow] ${v['price']:>4}/mo — {v['label']}")
        plan = Prompt.ask("  Plan", choices=list(SAAS_PLANS), default="pro")
    else:
        console.print("\n  [dim]Outreach Tiers:[/dim]")
        for k, v in OAS_TIERS.items():
            console.print(f"    [yellow]{k:11}[/yellow] ${v['price']:>4}/mo — {v['label']}")
        plan = Prompt.ask("  Tier", choices=list(OAS_TIERS), default="standard")

    name  = Prompt.ask("  Client full name").strip()
    email = Prompt.ask("  Client email").strip()
    phone = Prompt.ask("  Phone (optional)", default="").strip()
    company = Prompt.ask("  Company (optional)", default="").strip() if service == "oas" else ""
    markets = _prompt_markets()
    notes = Prompt.ask("  Notes (optional)", default="").strip()

    if not name or not email or not markets:
        console.print("[red]Name, email, and at least one market are required.[/red]")
        return

    if service == "saas":
        reg = register_saas_client(name, email, plan, markets, phone=phone, notes=notes)
    else:
        reg = register_oas_client(name, email, plan, markets, company=company, phone=phone, notes=notes)

    if "error" in reg:
        console.print(f"[red]{reg['error']}[/red]")
        return

    cid = reg["client_id"]
    amount = reg["monthly_fee"]

    console.print(f"\n[green]Client record created:[/green] {cid}")

    # Generate payment link via PayPal
    use_invoice = Confirm.ask("  Send a PayPal invoice (emails client from PayPal)?", default=True)
    pay = create_client_paywall(cid, use_invoice=use_invoice)
    payment_url = pay.get("payment_url", "")
    method = pay.get("method", "")

    console.print(f"  Payment method: [yellow]{method}[/yellow]")
    console.print(f"  Payment URL:    {payment_url}")

    # Send branded welcome email
    if Confirm.ask("  Send branded welcome email to client?", default=True):
        result = send_welcome_email(cid, payment_url, amount)
        if result.get("status") == "sent":
            console.print("[green]  ✓ Welcome email sent.[/green]")
        else:
            console.print(f"[yellow]  ⚠ Email not sent: {result.get('status')} {result.get('error', '')}[/yellow]")

    console.print(Panel(
        Text.from_markup(
            f"[bold green]Onboarded[/bold green]\n\n"
            f"  Client ID:   [bold yellow]{cid}[/bold yellow]\n"
            f"  Name:        {name}\n"
            f"  Email:       {email}\n"
            f"  Amount due:  ${amount:.0f}/mo\n"
            f"  Payment URL: {payment_url}\n\n"
            f"  Next steps:\n"
            f"    • Client pays via the link above.\n"
            f"    • Run [bold]python3 onboard_client.py --status {cid}[/bold] to check payment.\n"
            f"    • For Zelle/Venmo/cash: [bold]python3 onboard_client.py --activate {cid}[/bold]"
        ),
        border_style="green",
    ))


def cmd_list():
    saas = _load(SAAS_CLIENTS_FILE)
    oas  = _load(OAS_CLIENTS_FILE)

    tbl = Table(title="Real Clients", border_style="blue")
    tbl.add_column("ID", style="yellow")
    tbl.add_column("Name")
    tbl.add_column("Service")
    tbl.add_column("Plan/Tier")
    tbl.add_column("Fee")
    tbl.add_column("Status")
    tbl.add_column("Next Billing")

    mrr_active = 0.0
    total_pending = 0

    for c in saas.values():
        active = c.get("status") == "active" and c.get("payment_verified")
        if active:
            mrr_active += c.get("monthly_fee", 0)
        else:
            total_pending += 1
        color = "green" if active else "yellow"
        tbl.add_row(
            c["client_id"], c.get("name", ""),
            "SAAS", c.get("plan", ""),
            f"${c.get('monthly_fee', 0):.0f}/mo",
            f"[{color}]{c.get('status', '')}[/{color}]",
            c.get("next_billing_date", "—"),
        )
    for c in oas.values():
        active = c.get("status") == "active" and c.get("payment_verified")
        if active:
            mrr_active += c.get("monthly_fee", 0)
        else:
            total_pending += 1
        color = "green" if active else "yellow"
        tbl.add_row(
            c["client_id"], c.get("name", ""),
            "OAS", c.get("tier", ""),
            f"${c.get('monthly_fee', 0):.0f}/mo",
            f"[{color}]{c.get('status', '')}[/{color}]",
            c.get("next_billing_date", "—"),
        )

    if not (saas or oas):
        console.print("[dim]No clients yet. Run [bold]python3 onboard_client.py[/bold] to add one.[/dim]")
        return

    console.print(tbl)
    console.print(f"\n  Active MRR: [bold green]${mrr_active:.0f}/mo[/bold green]   "
                  f"Pending: [yellow]{total_pending}[/yellow]")


def cmd_status(client_id: str):
    client, _, _ = _client_lookup(client_id)
    if not client:
        console.print(f"[red]Client {client_id} not found.[/red]")
        return
    result = verify_payment(client_id)
    console.print(Panel(
        Text.from_markup(
            f"[bold]{client.get('name')}[/bold]  ({client_id})\n\n"
            f"  Status:       {result.get('status')}\n"
            f"  Activated:    {result.get('activated', False)}\n"
            f"  Amount:       ${client.get('monthly_fee', 0):.0f}/mo\n"
            f"  Payment URL:  {client.get('payment_url', '—')}"
        ),
        border_style="blue",
    ))


def cmd_activate(client_id: str, method: str = "manual"):
    client, _, _ = _client_lookup(client_id)
    if not client:
        console.print(f"[red]Client {client_id} not found.[/red]")
        return
    result = verify_payment(client_id)  # manually marks paid if no invoice
    # Stamp the payment method in notes for the audit trail
    clients = _load(SAAS_CLIENTS_FILE if client_id.startswith("SAAS-") else OAS_CLIENTS_FILE)
    note_line = f"[{datetime.datetime.now().strftime('%Y-%m-%d')}] Payment ${client.get('monthly_fee')} via {method}"
    existing = clients[client_id].get("notes", "")
    clients[client_id]["notes"] = (existing + "\n" + note_line).strip()
    clients[client_id]["payments_collected"] = clients[client_id].get("payments_collected", 0) + 1
    clients[client_id]["total_revenue"] = (
        clients[client_id].get("total_revenue", 0) + clients[client_id].get("monthly_fee", 0)
    )
    _save(SAAS_CLIENTS_FILE if client_id.startswith("SAAS-") else OAS_CLIENTS_FILE, clients)
    console.print(Panel(
        Text.from_markup(
            f"[bold green]Activated[/bold green]  {client.get('name')} ({client_id})\n"
            f"  Method: {method}\n  Status: {result.get('status')}"
        ),
        border_style="green",
    ))


def cmd_pending():
    pending = list_pending_payments()
    if pending["count"] == 0:
        console.print("[green]No pending payments. All clients paid.[/green]")
        return
    tbl = Table(title="Pending Payments", border_style="yellow")
    tbl.add_column("Client ID", style="yellow")
    tbl.add_column("Service")
    tbl.add_column("Name")
    tbl.add_column("Email")
    tbl.add_column("Amount")
    tbl.add_column("Payment URL")
    for p in pending["pending"]:
        tbl.add_row(p["client_id"], p["service"], p["name"] or "",
                    p["email"] or "", f"${p['amount_due']:.0f}", p.get("payment_url", "") or "—")
    console.print(tbl)


def main():
    parser = argparse.ArgumentParser(description="Wholesale Omniverse — Real Client Onboarding")
    parser.add_argument("--list",     action="store_true", help="List all real clients + MRR")
    parser.add_argument("--pending",  action="store_true", help="Show clients with pending payments")
    parser.add_argument("--status",   metavar="CLIENT_ID", help="Check payment status for a client")
    parser.add_argument("--activate", metavar="CLIENT_ID", help="Manually mark client as paid")
    parser.add_argument("--method",   default="manual", help="Payment method (paypal, zelle, venmo, cash)")
    args = parser.parse_args()

    if args.list:
        cmd_list()
    elif args.pending:
        cmd_pending()
    elif args.status:
        cmd_status(args.status)
    elif args.activate:
        cmd_activate(args.activate, args.method)
    else:
        cmd_new()


if __name__ == "__main__":
    main()
