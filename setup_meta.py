#!/usr/bin/env python3
"""
Meta (Facebook + Instagram) Setup Wizard
Walks you through:
  1. Creating a Meta for Developers app
  2. Generating a short-lived User Access Token in the Graph API Explorer
  3. Exchanging it for a long-lived (never-expires) Page Access Token
  4. Verifying it can post + writing META_ACCESS_TOKEN + META_PAGE_ID to .env
"""
import os
import re
import sys
from pathlib import Path
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.prompt import Prompt, Confirm
import requests

console = Console()
ENV_FILE = Path(__file__).parent / ".env"
GRAPH = "https://graph.facebook.com/v20.0"


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
    text = ENV_FILE.read_text() if ENV_FILE.exists() else ""
    for key, value in updates.items():
        pattern = re.compile(rf"^{re.escape(key)}\s*=.*$", re.MULTILINE)
        replacement = f"{key}={value}"
        if pattern.search(text):
            text = pattern.sub(replacement, text)
        else:
            text = text.rstrip() + f"\n{replacement}\n"
    ENV_FILE.write_text(text)


def exchange_for_long_lived_user(short_token: str, app_id: str, app_secret: str) -> str:
    r = requests.get(
        f"{GRAPH}/oauth/access_token",
        params={
            "grant_type": "fb_exchange_token",
            "client_id": app_id,
            "client_secret": app_secret,
            "fb_exchange_token": short_token,
        }, timeout=15,
    )
    r.raise_for_status()
    return r.json().get("access_token", "")


def get_page_token(user_token: str, page_id: str) -> str:
    """
    A long-lived user token + a Page you admin → a permanent Page Access Token
    via the /{page_id}?fields=access_token endpoint.
    """
    r = requests.get(
        f"{GRAPH}/{page_id}",
        params={"fields": "access_token,name", "access_token": user_token},
        timeout=15,
    )
    r.raise_for_status()
    data = r.json()
    return data.get("access_token", ""), data.get("name", "")


def verify_page_token(page_token: str, page_id: str) -> tuple:
    r = requests.get(
        f"{GRAPH}/{page_id}",
        params={"fields": "id,name,fan_count,category", "access_token": page_token},
        timeout=15,
    )
    if r.status_code != 200:
        return False, f"HTTP {r.status_code}: {r.text[:200]}"
    return True, r.json()


def main():
    console.print(Panel(
        Text.from_markup(
            "[bold white]Meta (Facebook) Setup Wizard[/bold white]\n"
            "[dim]Wires up Facebook Page posting for the social agent[/dim]"
        ),
        title="[bold blue]Wholesale Omniverse — Meta Setup[/bold blue]",
        border_style="blue",
    ))

    console.print(Panel(
        Text.from_markup(
            "[bold]Before you start, complete these in your browser:[/bold]\n\n"
            "[bold yellow]1. Create the app[/bold yellow]\n"
            "   • Go to [bold]https://developers.facebook.com/apps[/bold]\n"
            "   • Click [bold]Create App[/bold] → choose [bold]Business[/bold] → name it\n"
            "   • In the new app: [bold]App Settings → Basic[/bold]\n"
            "   • Copy the [bold]App ID[/bold] and [bold]App Secret[/bold]\n\n"
            "[bold yellow]2. Connect your Page[/bold yellow]\n"
            "   • Your Facebook Page must be admin'd by the same Facebook account\n"
            "   • Note your [bold]Page ID[/bold] (Page → About → Page Transparency)\n\n"
            "[bold yellow]3. Get a User Access Token[/bold yellow]\n"
            "   • Open [bold]https://developers.facebook.com/tools/explorer[/bold]\n"
            "   • Select your app at the top right\n"
            "   • Click [bold]Generate Access Token[/bold]\n"
            "   • Add scopes: [bold]pages_manage_posts[/bold], [bold]pages_read_engagement[/bold], "
            "[bold]pages_show_list[/bold]\n"
            "   • Authorize → copy the token (starts with [italic]EAA...[/italic])"
        ),
        border_style="yellow",
    ))

    if not Confirm.ask("\n  Ready to enter values?", default=True):
        console.print("[dim]Run me again when you have them.[/dim]")
        return

    env = read_env()

    app_id      = Prompt.ask("  META_APP_ID         ", default=env.get("META_APP_ID", ""))
    app_secret  = Prompt.ask("  META_APP_SECRET     ", default=env.get("META_APP_SECRET", ""), password=True)
    page_id     = Prompt.ask("  META_PAGE_ID        ", default=env.get("META_PAGE_ID", ""))
    short_token = Prompt.ask("  Short-lived USER token (from Graph API Explorer)",
                             password=True)

    if not all([app_id, app_secret, page_id, short_token]):
        console.print("[red]All four values are required.[/red]")
        sys.exit(1)

    # ── Step 1: exchange for long-lived user token ────────────────────────────
    console.print("\n[yellow]Exchanging short-lived → long-lived user token...[/yellow]")
    try:
        long_user_token = exchange_for_long_lived_user(short_token, app_id, app_secret)
        if not long_user_token:
            console.print("[red]✗ Exchange returned empty token. Check app_id/secret.[/red]")
            sys.exit(1)
        console.print(f"[green]✓ Long-lived user token: {long_user_token[:14]}...[/green]")
    except Exception as e:
        console.print(f"[red]✗ Exchange failed: {e}[/red]")
        sys.exit(1)

    # ── Step 2: pull permanent page access token ─────────────────────────────
    console.print("[yellow]Fetching permanent Page Access Token...[/yellow]")
    try:
        page_token, page_name = get_page_token(long_user_token, page_id)
        if not page_token:
            console.print("[red]✗ No page token in response. Are you a Page admin?[/red]")
            sys.exit(1)
        console.print(f"[green]✓ Page Access Token issued for: {page_name}[/green]")
    except Exception as e:
        console.print(f"[red]✗ Page token fetch failed: {e}[/red]")
        sys.exit(1)

    # ── Step 3: verify the page token actually works ─────────────────────────
    console.print("[yellow]Verifying Page Access Token...[/yellow]")
    ok, info = verify_page_token(page_token, page_id)
    if not ok:
        console.print(f"[red]✗ Verification failed: {info}[/red]")
        if not Confirm.ask("  Save anyway?", default=False):
            sys.exit(1)
    else:
        console.print(Panel(
            Text.from_markup(
                f"[bold green]✓ Verified[/bold green]\n\n"
                f"  Page:     [white]{info.get('name')}[/white]\n"
                f"  Page ID:  [white]{info.get('id')}[/white]\n"
                f"  Category: [white]{info.get('category', '—')}[/white]\n"
                f"  Followers:[white]{info.get('fan_count', 0)}[/white]"
            ),
            border_style="green",
        ))

    # ── Step 4: write to .env ─────────────────────────────────────────────────
    update_env({
        "META_APP_ID":         app_id,
        "META_APP_SECRET":     app_secret,
        "META_PAGE_ID":        page_id,
        "META_ACCESS_TOKEN":   page_token,
        # The meta_ads adapter checks META_AD_ACCOUNT_ID; leave blank until you set up ads.
        "META_AD_ACCOUNT_ID":  env.get("META_AD_ACCOUNT_ID", ""),
    })

    console.print(Panel(
        Text.from_markup(
            "[bold green]Meta is wired up.[/bold green]\n\n"
            "  Try a dry run:\n"
            "    [bold]python3 run_social_auto.py --platforms meta_ads --dry-run[/bold]\n\n"
            "  Post for real to your Page:\n"
            "    [bold]python3 run_social_auto.py --platforms meta_ads --audience sellers[/bold]\n\n"
            "  [yellow]Note:[/yellow] Organic page posting works now.\n"
            "  Paid ads / boosted posts require META_AD_ACCOUNT_ID and a funded Ad Account."
        ),
        border_style="green",
    ))


if __name__ == "__main__":
    main()
