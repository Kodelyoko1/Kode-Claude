#!/usr/bin/env python3
"""
Client subscription manager — activate, deactivate, list subscribers.
Run after a client pays on Stripe to give them access.
"""
import os
import sys
import argparse
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from paywall.agent_paywall import (
    activate_subscription, list_subscriptions,
    create_subscription, AGENT_NAMES, _price,
)

console = Console()


def cmd_list(agent_key=""):
    data = list_subscriptions(agent_key)
    tbl = Table(title="Subscriptions", border_style="blue")
    tbl.add_column("Access Key", style="yellow")
    tbl.add_column("Name")
    tbl.add_column("Agent")
    tbl.add_column("Price")
    tbl.add_column("Status")
    tbl.add_column("Expires")
    for s in data["subscriptions"]:
        status_color = "green" if s["status"] == "active" else "yellow"
        expires = s.get("expires_at", "")[:10] if s.get("expires_at") else "—"
        tbl.add_row(
            s["access_key"],
            s.get("name", ""),
            AGENT_NAMES.get(s.get("agent", ""), s.get("agent", "")),
            f"${s.get('price', 0):.0f}/mo",
            f"[{status_color}]{s['status']}[/{status_color}]",
            expires,
        )
    console.print(tbl)
    console.print(f"\n  Active: [green]{data['active']}[/green]   "
                  f"Pending: [yellow]{data['pending_payment']}[/yellow]   "
                  f"MRR: [bold]${data['mrr']:.0f}/mo[/bold]")


def cmd_activate(access_key: str):
    result = activate_subscription(access_key)
    if "error" in result:
        console.print(f"[red]{result['error']}[/red]")
    else:
        console.print(Panel(
            Text.from_markup(
                f"[bold green]Client Activated[/bold green]\n\n"
                f"  Name:       {result.get('name')}\n"
                f"  Agent:      {AGENT_NAMES.get(result.get('agent', ''), result.get('agent'))}\n"
                f"  Access Key: [yellow]{result['access_key']}[/yellow]\n"
                f"  Expires:    {result.get('expires_at', '')[:10]}\n\n"
                f"  Send them their key: [bold]{result['access_key']}[/bold]"
            ),
            border_style="green",
        ))


def cmd_new(agent_key: str):
    if agent_key not in AGENT_NAMES:
        console.print(f"[red]Unknown agent: {agent_key}[/red]")
        console.print(f"Options: {', '.join(AGENT_NAMES.keys())}")
        return
    name  = input("  Client name: ").strip()
    email = input("  Client email: ").strip()
    if not name or not email:
        console.print("[red]Name and email required.[/red]")
        return
    result = create_subscription(agent_key, name, email)
    console.print(Panel(
        Text.from_markup(
            f"[bold green]Subscription Created[/bold green]\n\n"
            f"  Client:      {name} ({email})\n"
            f"  Agent:       {AGENT_NAMES[agent_key]}\n"
            f"  Price:       ${result['price']:.0f}/mo\n"
            f"  Access Key:  [bold yellow]{result['access_key']}[/bold yellow]\n"
            f"  Payment URL: {result['payment_url']}\n\n"
            f"  1. Send them the payment link\n"
            f"  2. Once they pay, run:  python3 manage_clients.py --activate {result['access_key']}\n"
            f"  3. Email them their access key"
        ),
        border_style="blue",
    ))


def main():
    parser = argparse.ArgumentParser(description="Manage client subscriptions")
    parser.add_argument("--list",     action="store_true",     help="List all subscriptions")
    parser.add_argument("--activate", metavar="ACCESS_KEY",    help="Activate a client after payment")
    parser.add_argument("--new",      metavar="AGENT",         help="Create new subscription (buyer_finder, followup, outreach, wholesale)")
    parser.add_argument("--agent",    metavar="AGENT",         help="Filter --list by agent key")
    args = parser.parse_args()

    if args.activate:
        cmd_activate(args.activate)
    elif args.new:
        cmd_new(args.new)
    else:
        # Default: show list
        cmd_list(args.agent or "")


if __name__ == "__main__":
    main()
