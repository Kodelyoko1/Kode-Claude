#!/usr/bin/env python3
"""
Stripe Payment Link Setup — no API credentials required.
Just paste your Stripe payment links from the dashboard and go.

1. Go to dashboard.stripe.com → Payment Links → + New
2. Add a recurring monthly product at your desired price
3. Copy the buy.stripe.com/... link
4. Run this script and paste the links in
"""
import re
import os
from pathlib import Path
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

console = Console()
ENV_FILE = Path(__file__).parent / ".env"

AGENTS = [
    ("buyer_finder", "STRIPE_LINK_BUYER_FINDER", "Cash Buyer Finder Agent",    "97"),
    ("followup",     "STRIPE_LINK_FOLLOWUP",     "Seller Follow-Up Agent",     "147"),
    ("outreach",     "STRIPE_LINK_OUTREACH",     "Outreach-as-a-Service Agent","297"),
    ("wholesale",    "STRIPE_LINK_WHOLESALE",    "Wholesale Deal Analyzer",    "197"),
]

PRICE_KEYS = [
    ("PAYWALL_BUYER_FINDER_PRICE", "Cash Buyer Finder price/month"),
    ("PAYWALL_FOLLOWUP_PRICE",     "Seller Follow-Up price/month"),
    ("PAYWALL_OUTREACH_PRICE",     "Outreach price/month"),
    ("PAYWALL_WHOLESALE_PRICE",    "Wholesale Analyzer price/month"),
]


def read_env() -> dict:
    env = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
    return env


def write_env_key(key: str, value: str):
    text = ENV_FILE.read_text() if ENV_FILE.exists() else ""
    pattern = re.compile(rf"^{re.escape(key)}\s*=.*$", re.MULTILINE)
    replacement = f"{key}={value}"
    if pattern.search(text):
        text = pattern.sub(replacement, text)
    else:
        text = text.rstrip() + f"\n{replacement}\n"
    ENV_FILE.write_text(text)


def main():
    console.print(Panel(
        Text.from_markup(
            "[bold white]Stripe Payment Link Setup[/bold white]\n"
            "[dim]Paste your Stripe links — no API keys required[/dim]"
        ),
        title="[bold blue]Wholesale Omniverse — Stripe Setup[/bold blue]",
        border_style="blue",
    ))

    env = read_env()

    console.print("\n[bold yellow]How to get your Stripe payment links:[/bold yellow]")
    console.print("  1. Go to [bold]dashboard.stripe.com[/bold]")
    console.print("  2. Click [bold]Payment Links[/bold] → [bold]+ New[/bold]")
    console.print("  3. Create a product (e.g. 'Cash Buyer Finder — $97/mo recurring')")
    console.print("  4. Copy the [bold]buy.stripe.com/...[/bold] link")
    console.print("  5. Paste it below\n")
    console.print("[dim]Press Enter to skip (will use PayPal.me fallback for that agent)[/dim]\n")

    updates = {}
    for agent_key, env_key, label, default_price in AGENTS:
        existing = env.get(env_key, "")
        display = existing if existing else "[dim]not set[/dim]"
        console.print(f"[cyan]{label}[/cyan]  (${default_price}/mo)")
        if existing:
            console.print(f"  Current: {existing}")
        link = input(f"  Stripe link: ").strip()
        if link:
            if not link.startswith("http"):
                link = "https://" + link
            updates[env_key] = link
            console.print(f"  [green]✓ Saved[/green]")
        else:
            console.print(f"  [dim]Skipped — PayPal.me fallback will be used[/dim]")
        print()

    # Optional: update prices
    console.print("[bold yellow]Update monthly prices? (Press Enter to keep defaults)[/bold yellow]\n")
    for env_key, label in PRICE_KEYS:
        current = env.get(env_key, "")
        new_val = input(f"  {label} [${current}]: ").strip().lstrip("$")
        if new_val:
            updates[env_key] = new_val

    # Save all
    for key, value in updates.items():
        write_env_key(key, value)

    if updates:
        console.print(f"\n[green]✓ Saved {len(updates)} setting(s) to .env[/green]")

    # Summary table
    env_fresh = read_env()
    tbl = Table(title="Payment Links", border_style="green")
    tbl.add_column("Agent")
    tbl.add_column("Price")
    tbl.add_column("Payment Link")

    username = env_fresh.get("PAYPAL_ME_USERNAME", "wholesaleomniverse")
    for agent_key, env_key, label, default_price in AGENTS:
        price_key = f"PAYWALL_{agent_key.upper()}_PRICE"
        price = env_fresh.get(price_key, default_price)
        link = env_fresh.get(env_key, "")
        if link:
            display_link = link[:55] + "..." if len(link) > 58 else link
            method = "[green]Stripe[/green]"
        else:
            display_link = f"paypal.me/{username}/{price}"
            method = "[yellow]PayPal.me fallback[/yellow]"
        tbl.add_row(label, f"${price}/mo", f"{method}  {display_link}")
    console.print(tbl)

    console.print(Panel(
        Text.from_markup(
            "[bold green]All Set![/bold green]\n\n"
            "When a client tries to use an agent:\n"
            "  1. They see the payment link + a unique access key\n"
            "  2. They pay via Stripe (card, Apple Pay, Google Pay, etc.)\n"
            "  3. Stripe emails you the payment confirmation\n"
            "  4. You activate their key:\n\n"
            "     [bold]python3 manage_clients.py[/bold]\n\n"
            "  5. Client re-runs the agent and enters their key — done"
        ),
        border_style="green",
    ))


if __name__ == "__main__":
    main()
