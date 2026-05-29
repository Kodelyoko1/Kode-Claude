#!/usr/bin/env python3
"""
PayPal Account Setup Wizard
Run this once to connect your PayPal account to all agents.
Updates .env with your credentials — no manual editing needed.
"""
import os
import re
import sys
from pathlib import Path
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.prompt import Prompt, Confirm

console = Console()
ENV_FILE = Path(__file__).parent / ".env"


def read_env() -> dict:
    env = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
    return env


def update_env(updates: dict):
    """Write key=value pairs into .env, updating existing keys in place."""
    text = ENV_FILE.read_text() if ENV_FILE.exists() else ""
    lines = text.splitlines()

    for key, value in updates.items():
        pattern = re.compile(rf"^{re.escape(key)}\s*=.*$", re.MULTILINE)
        replacement = f"{key}={value}"
        if pattern.search(text):
            text = pattern.sub(replacement, text)
        else:
            # Append after the PayPal section header if present
            text = text.rstrip() + f"\n{replacement}\n"

    ENV_FILE.write_text(text)


def test_paypal_connection(client_id: str, client_secret: str, mode: str) -> bool:
    """Try to get a PayPal OAuth2 token to verify credentials."""
    try:
        import requests
        base = "https://api-m.paypal.com" if mode == "live" else "https://api-m.sandbox.paypal.com"
        resp = requests.post(
            f"{base}/v1/oauth2/token",
            auth=(client_id, client_secret),
            data={"grant_type": "client_credentials"},
            headers={"Accept": "application/json"},
            timeout=10,
        )
        return resp.status_code == 200
    except Exception:
        return False


def main():
    console.print(Panel(
        Text.from_markup(
            "[bold white]PayPal Account Setup Wizard[/bold white]\n"
            "[dim]Connect your PayPal to all Wholesale Omniverse agents[/dim]"
        ),
        title="[bold blue]Wholesale Omniverse — PayPal Setup[/bold blue]",
        border_style="blue",
    ))

    env = read_env()

    # ── Step 1: PayPal Developer API credentials ───────────────────────────────
    console.print("\n[bold yellow]Step 1 — PayPal API Credentials[/bold yellow]")
    console.print(
        "  Get these at [bold]developer.paypal.com[/bold]\n"
        "  → Log in → My Apps & Credentials → Create App\n"
        "  → Copy the [bold]Client ID[/bold] and [bold]Secret[/bold]\n"
    )
    console.print("[dim]  Press Enter to keep existing value (shown in brackets)[/dim]\n")

    existing_id = env.get("PAYPAL_CLIENT_ID", "")
    existing_secret = env.get("PAYPAL_CLIENT_SECRET", "")

    masked_id = f"...{existing_id[-8:]}" if len(existing_id) > 8 else (existing_id or "not set")
    masked_secret = f"...{existing_secret[-6:]}" if len(existing_secret) > 6 else (existing_secret or "not set")

    client_id = Prompt.ask(
        f"  PayPal Client ID [{masked_id}]",
        default=existing_id,
    ).strip()

    try:
        client_secret = Prompt.ask(
            f"  PayPal Client Secret [{masked_secret}]",
            default=existing_secret,
            password=True,
        ).strip()
    except (EOFError, Exception):
        client_secret = input(f"  PayPal Client Secret [{masked_secret}]: ").strip() or existing_secret

    # ── Step 2: Live vs Sandbox ────────────────────────────────────────────────
    console.print("\n[bold yellow]Step 2 — Mode[/bold yellow]")
    current_mode = env.get("PAYPAL_MODE", "live")
    mode_choice = Prompt.ask(
        "  Mode [live/sandbox]",
        default=current_mode,
        choices=["live", "sandbox"],
    ).strip().lower()

    # ── Step 3: PayPal.me username ─────────────────────────────────────────────
    console.print("\n[bold yellow]Step 3 — PayPal.me Username[/bold yellow]")
    console.print("  Your PayPal.me link: [bold]paypal.me/YOUR_USERNAME[/bold]")
    console.print("  Find it at: paypal.com/myaccount/profile → PayPal.me\n")

    current_username = env.get("PAYPAL_ME_USERNAME", "wholesaleomniverse")
    paypalme = Prompt.ask(
        f"  PayPal.me username [{current_username}]",
        default=current_username,
    ).strip().lstrip("@")

    # ── Step 4: Business email ─────────────────────────────────────────────────
    console.print("\n[bold yellow]Step 4 — PayPal Business Email[/bold yellow]")
    current_email = env.get("PAYPAL_EMAIL", "WholesaleOmniverse@gmail.com")
    paypal_email = Prompt.ask(
        f"  PayPal business email [{current_email}]",
        default=current_email,
    ).strip()

    # ── Step 5: Agent pricing ──────────────────────────────────────────────────
    console.print("\n[bold yellow]Step 5 — Agent Subscription Prices[/bold yellow]")
    console.print("  Monthly prices charged when clients subscribe to each agent\n")

    prices = {}
    defaults = {
        "PAYWALL_BUYER_FINDER_PRICE":  ("Buyer Finder Agent",      env.get("PAYWALL_BUYER_FINDER_PRICE",  "97")),
        "PAYWALL_FOLLOWUP_PRICE":      ("Follow-Up Sequence Agent", env.get("PAYWALL_FOLLOWUP_PRICE",     "147")),
        "PAYWALL_OUTREACH_PRICE":      ("Outreach Service Agent",   env.get("PAYWALL_OUTREACH_PRICE",     "297")),
        "PAYWALL_WHOLESALE_PRICE":     ("Wholesale Deal Analyzer",  env.get("PAYWALL_WHOLESALE_PRICE",    "197")),
    }
    for key, (label, default) in defaults.items():
        val = Prompt.ask(f"  {label} price/month [${default}]", default=default).strip().lstrip("$")
        prices[key] = val

    # ── Save all settings ──────────────────────────────────────────────────────
    updates = {
        "PAYPAL_CLIENT_ID":      client_id,
        "PAYPAL_CLIENT_SECRET":  client_secret,
        "PAYPAL_MODE":           mode_choice,
        "PAYPAL_EMAIL":          paypal_email,
        "PAYPAL_ME_USERNAME":    paypalme,
        **prices,
    }
    update_env(updates)
    console.print("\n[green]✓ Saved to .env[/green]")

    # ── Test connection ────────────────────────────────────────────────────────
    if client_id and client_secret:
        console.print("\n[yellow]Testing PayPal connection...[/yellow]")
        ok = test_paypal_connection(client_id, client_secret, mode_choice)
        if ok:
            console.print(f"[bold green]✓ PayPal {mode_choice.upper()} connection successful![/bold green]")
        else:
            console.print(
                "[bold red]✗ Connection failed.[/bold red] "
                "Check your Client ID and Secret at developer.paypal.com\n"
                "[dim]Note: sandbox credentials won't work in live mode and vice versa.[/dim]"
            )
    else:
        console.print("[dim]No credentials entered — PayPal.me fallback will be used for payments.[/dim]")

    # ── Summary table ──────────────────────────────────────────────────────────
    tbl = Table(title="PayPal Configuration Summary", border_style="green")
    tbl.add_column("Setting")
    tbl.add_column("Value")
    tbl.add_row("Mode",           f"[bold]{mode_choice.upper()}[/bold]")
    tbl.add_row("PayPal.me",      f"paypal.me/{paypalme}")
    tbl.add_row("Business Email", paypal_email)
    tbl.add_row("API Connected",  "[green]Yes[/green]" if (client_id and client_secret) else "[yellow]No (using PayPal.me fallback)[/yellow]")
    for key, (label, _) in defaults.items():
        tbl.add_row(label, f"${prices[key]}/mo")
    console.print(tbl)

    console.print(Panel(
        Text.from_markup(
            "[bold green]Setup Complete![/bold green]\n\n"
            "All agents will now use your PayPal account for payments.\n\n"
            "[bold]To charge a new client:[/bold]\n"
            "  python3 outreach_service_main.py  → register client → charge\n"
            "  python3 main.py                   → deal analyzer paywall\n\n"
            "[bold]To verify a payment:[/bold]\n"
            "  python3 -c \"from paywall.gate import verify_payment; print(verify_payment('CLIENT-ID'))\"\n\n"
            "[bold]PayPal.me link (no API needed):[/bold]\n"
            f"  paypal.me/{paypalme}"
        ),
        title="[bold green]All Set[/bold green]",
        border_style="green",
    ))


if __name__ == "__main__":
    main()
