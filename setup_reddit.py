#!/usr/bin/env python3
"""
Reddit Account Setup Wizard
Walks you through creating a Reddit script app, then writes the credentials to .env
and verifies the connection by fetching your own user info.
"""
import os
import re
import sys
from pathlib import Path
from rich.console import Console
from rich.panel import Panel
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
    text = ENV_FILE.read_text() if ENV_FILE.exists() else ""
    for key, value in updates.items():
        pattern = re.compile(rf"^{re.escape(key)}\s*=.*$", re.MULTILINE)
        replacement = f"{key}={value}"
        if pattern.search(text):
            text = pattern.sub(replacement, text)
        else:
            text = text.rstrip() + f"\n{replacement}\n"
    ENV_FILE.write_text(text)


def test_connection(client_id, client_secret, username, password) -> tuple:
    """Try to fetch /api/v1/me — proves all four creds work."""
    try:
        import praw
        r = praw.Reddit(
            client_id=client_id,
            client_secret=client_secret,
            username=username,
            password=password,
            user_agent="wholesaleomniverse-setup/1.0",
        )
        me = r.user.me()
        if me is None:
            return False, "Reddit returned None for user.me() — usually wrong username/password."
        return True, f"u/{me.name}  (karma: {me.link_karma + me.comment_karma})"
    except Exception as e:
        return False, str(e)


def main():
    console.print(Panel(
        Text.from_markup(
            "[bold white]Reddit Setup Wizard[/bold white]\n"
            "[dim]Wires up Reddit posting for the social agent[/dim]"
        ),
        title="[bold blue]Wholesale Omniverse — Reddit Setup[/bold blue]",
        border_style="blue",
    ))

    console.print(Panel(
        Text.from_markup(
            "[bold]Before you start:[/bold]\n\n"
            "  1. Visit [bold]https://www.reddit.com/prefs/apps[/bold]\n"
            "  2. Scroll to bottom → click [bold]'create another app...'[/bold]\n"
            "  3. Fill in:\n"
            "      • [yellow]name[/yellow]: WholesaleOmniverseSocial\n"
            "      • [yellow]app type[/yellow]: [bold]script[/bold]  ← important\n"
            "      • [yellow]redirect uri[/yellow]: http://localhost:8080\n"
            "  4. Click [bold]create app[/bold]\n"
            "  5. Copy the [bold]client_id[/bold] (under the app name)\n"
            "     and the [bold]secret[/bold] (next to 'secret')"
        ),
        border_style="yellow",
    ))

    if not Confirm.ask("\n  Ready to enter credentials?", default=True):
        console.print("[dim]Run me again when you have them.[/dim]")
        return

    env = read_env()

    client_id     = Prompt.ask("  REDDIT_CLIENT_ID    ", default=env.get("REDDIT_CLIENT_ID", ""))
    client_secret = Prompt.ask("  REDDIT_CLIENT_SECRET", default=env.get("REDDIT_CLIENT_SECRET", ""), password=True)
    username      = Prompt.ask("  REDDIT_USERNAME     ", default=env.get("REDDIT_USERNAME", ""))
    password      = Prompt.ask("  REDDIT_PASSWORD     ", default=env.get("REDDIT_PASSWORD", ""), password=True)

    if not all([client_id, client_secret, username, password]):
        console.print("[red]All four values are required.[/red]")
        sys.exit(1)

    console.print("\n[yellow]Verifying credentials...[/yellow]")
    ok, info = test_connection(client_id, client_secret, username, password)
    if not ok:
        console.print(Panel(
            Text.from_markup(f"[red]✗ Verification failed[/red]\n\n{info}"),
            border_style="red",
        ))
        if not Confirm.ask("  Save credentials anyway?", default=False):
            console.print("[dim]Aborted — nothing written.[/dim]")
            sys.exit(1)
    else:
        console.print(f"[green]✓ Connected as {info}[/green]")

    update_env({
        "REDDIT_CLIENT_ID":     client_id,
        "REDDIT_CLIENT_SECRET": client_secret,
        "REDDIT_USERNAME":      username,
        "REDDIT_PASSWORD":      password,
    })

    console.print(Panel(
        Text.from_markup(
            "[bold green]Reddit is wired up.[/bold green]\n\n"
            "  Try a dry run:\n"
            "    [bold]python3 run_social_auto.py --status[/bold]\n"
            "    [bold]python3 run_social_auto.py --platforms reddit --dry-run[/bold]\n\n"
            "  Post for real:\n"
            "    [bold]python3 run_social_auto.py --platforms reddit --audience wholesalers[/bold]\n\n"
            "  [yellow]Heads up:[/yellow] Reddit is strict about self-promotion.\n"
            "  Most subreddits require karma + account age before letting you post links\n"
            "  or promotional content. Start by commenting helpfully for a few days."
        ),
        border_style="green",
    ))


if __name__ == "__main__":
    main()
