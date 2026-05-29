#!/usr/bin/env python3
"""
Pinterest Setup Wizard
Walks you through:
  1. Creating a Pinterest developer app
  2. Generating an Access Token via the OAuth shortcut on the app page
  3. Picking which board pins go to
  4. Verifying the token can list + create pins
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
API = "https://api.pinterest.com/v5"


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


def verify_token(token: str) -> tuple:
    try:
        r = requests.get(f"{API}/user_account",
                         headers={"Authorization": f"Bearer {token}"},
                         timeout=10)
        if r.status_code != 200:
            return False, f"HTTP {r.status_code}: {r.text[:200]}", None
        return True, "verified", r.json()
    except Exception as e:
        return False, str(e), None


def list_boards(token: str) -> list:
    try:
        r = requests.get(f"{API}/boards",
                         headers={"Authorization": f"Bearer {token}"},
                         params={"page_size": 25}, timeout=10)
        if r.status_code != 200:
            return []
        return r.json().get("items", [])
    except Exception:
        return []


def create_board(token: str, name: str, description: str = "") -> dict:
    r = requests.post(f"{API}/boards",
                      headers={"Authorization": f"Bearer {token}",
                               "Content-Type": "application/json"},
                      json={"name": name, "description": description,
                            "privacy": "PUBLIC"},
                      timeout=10)
    if r.status_code in (200, 201):
        return r.json()
    return {"error": f"HTTP {r.status_code}: {r.text[:200]}"}


def main():
    console.print(Panel(
        Text.from_markup(
            "[bold white]Pinterest Setup Wizard[/bold white]\n"
            "[dim]Wires up Pinterest posting for the social agent[/dim]"
        ),
        title="[bold blue]Wholesale Omniverse — Pinterest Setup[/bold blue]",
        border_style="blue",
    ))

    console.print(Panel(
        Text.from_markup(
            "[bold]Before you start:[/bold]\n\n"
            "[bold yellow]1. Create a Pinterest business account[/bold yellow]\n"
            "   • If you don't have one, sign up at [bold]pinterest.com/business/create[/bold]\n"
            "   • Free, takes 2 minutes\n\n"
            "[bold yellow]2. Create a developer app[/bold yellow]\n"
            "   • Go to [bold]https://developers.pinterest.com/apps/[/bold]\n"
            "   • Click [bold]Create app[/bold]\n"
            "   • Name: WholesaleOmniverse, give it a short description\n"
            "   • For 'redirect URIs' put [bold]https://localhost/[/bold] (we won't use it)\n"
            "   • Submit\n\n"
            "[bold yellow]3. Generate the access token[/bold yellow]\n"
            "   • On your new app's page, scroll to the [bold]Trial access tokens[/bold] section\n"
            "   • Tick scopes: [bold]boards:read[/bold], [bold]boards:write[/bold], "
            "[bold]pins:read[/bold], [bold]pins:write[/bold], "
            "[bold]user_accounts:read[/bold]\n"
            "   • Click [bold]Generate access token[/bold] → copy it (starts with [italic]pina_AAA...[/italic])"
        ),
        border_style="yellow",
    ))

    if not Confirm.ask("\n  Ready to enter your token?", default=True):
        console.print("[dim]Run me again when you have it.[/dim]")
        return

    env = read_env()
    token = Prompt.ask("  PINTEREST_ACCESS_TOKEN ",
                       default=env.get("PINTEREST_ACCESS_TOKEN", ""),
                       password=True)
    if not token:
        console.print("[red]Token is required.[/red]")
        sys.exit(1)

    console.print("\n[yellow]Verifying token...[/yellow]")
    ok, msg, info = verify_token(token)
    if not ok:
        console.print(Panel(Text.from_markup(f"[red]✗ {msg}[/red]"),
                            border_style="red"))
        if not Confirm.ask("  Save anyway?", default=False):
            return
    else:
        console.print(f"[green]✓ Authenticated as: {info.get('username', '?')}  "
                      f"({info.get('account_type', '?')})[/green]")

    # ── Board selection ──────────────────────────────────────────────────────
    console.print("\n[yellow]Fetching your boards...[/yellow]")
    boards = list_boards(token)
    board_id = ""

    if boards:
        console.print("\n  Existing boards:")
        for i, b in enumerate(boards, 1):
            console.print(f"    {i}.  [yellow]{b['id']}[/yellow]  {b['name']}")
        console.print(f"    n.  Create a new board")
        choice = Prompt.ask("  Pick a board (number) or 'n' to create",
                            default="n")
        if choice.isdigit() and 1 <= int(choice) <= len(boards):
            board_id = boards[int(choice) - 1]["id"]
        else:
            choice = "n"
    else:
        console.print("[dim]No boards yet — let's create one.[/dim]")
        choice = "n"

    if choice == "n":
        name = Prompt.ask("  New board name", default="Cash Home Buyers")
        desc = Prompt.ask("  Description (optional)",
                          default="We buy houses fast. Cash offers, no repairs, close in 2-3 weeks.")
        result = create_board(token, name, desc)
        if "id" in result:
            board_id = result["id"]
            console.print(f"[green]✓ Board created: {board_id}  '{name}'[/green]")
        else:
            console.print(f"[red]✗ Board creation failed: {result.get('error')}[/red]")
            return

    # ── Save + done ──────────────────────────────────────────────────────────
    update_env({
        "PINTEREST_ACCESS_TOKEN": token,
        "PINTEREST_BOARD_ID":     board_id,
    })

    console.print(Panel(
        Text.from_markup(
            "[bold green]Pinterest is wired up.[/bold green]\n\n"
            "  Try a dry run:\n"
            "    [bold]python3 run_social_auto.py --platforms pinterest --dry-run[/bold]\n\n"
            "  Post for real:\n"
            "    [bold]python3 run_pinterest_auto.py[/bold]\n\n"
            "  [yellow]Tip:[/yellow] Pinterest rewards consistency over volume.\n"
            "  1-3 pins/day for 30 days beats 50 pins in one day.\n"
            "  The nightly cron already handles this cadence."
        ),
        border_style="green",
    ))


if __name__ == "__main__":
    main()
