#!/usr/bin/env python3
"""
One-time setup for ViralRecycler.

Steps:
  1. Installs yt-dlp + Google API libs
  2. Walks you through the YouTube OAuth flow
  3. Verifies ffmpeg is present
  4. Tests a download + transform on a free Creative Commons clip
"""
import subprocess
import sys
from pathlib import Path
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

console = Console()
DATA_DIR = Path(__file__).parent / "data"


def install_deps():
    pkgs = ["yt-dlp", "google-api-python-client",
            "google-auth-oauthlib", "google-auth-httplib2", "requests"]
    console.print(f"[cyan]Installing: {', '.join(pkgs)}[/cyan]")
    r = subprocess.run([sys.executable, "-m", "pip", "install", "--user", *pkgs],
                       capture_output=True, text=True)
    if r.returncode != 0:
        console.print(f"[red]Install failed:[/red] {r.stderr[-500:]}")
        return False
    console.print("[green]✓ Dependencies installed[/green]")
    return True


def check_ffmpeg():
    import shutil
    if shutil.which("ffmpeg"):
        console.print("[green]✓ ffmpeg found[/green]")
        return True
    console.print("[red]✗ ffmpeg not found. Install: sudo apt install ffmpeg[/red]")
    return False


def authorize_youtube():
    console.print(Panel(
        Text.from_markup(
            "[bold yellow]YouTube OAuth Setup[/bold yellow]\n\n"
            "  1. Go to [white]console.cloud.google.com[/white] (free account)\n"
            "  2. Create a project (any name)\n"
            "  3. Enable [white]YouTube Data API v3[/white] in API Library\n"
            "  4. APIs & Services → Credentials → Create OAuth 2.0 Client ID\n"
            "     Application type: [white]Desktop app[/white]\n"
            "  5. Download the JSON file\n"
            "  6. Save it as [white]data/yt_client_secrets.json[/white]\n"
            "  7. Then run this script again with --authorize"
        ),
        border_style="yellow",
    ))
    secrets = DATA_DIR / "yt_client_secrets.json"
    if not secrets.exists():
        console.print(f"[yellow]Waiting for {secrets}…[/yellow]")
        return False
    from viral_recycler.youtube import authorize_interactive
    result = authorize_interactive()
    if result.get("error"):
        console.print(f"[red]{result['error']}[/red]")
        return False
    console.print(f"[green]✓ YouTube authorized. Token saved at {result['token_path']}[/green]")
    return True


def setup_tiktok_note():
    console.print(Panel(
        Text.from_markup(
            "[bold yellow]TikTok Setup (optional — falls back to manual upload)[/bold yellow]\n\n"
            "  Official API (free, needs approval):\n"
            "    1. [white]developers.tiktok.com[/white] → register app\n"
            "    2. Enable Content Posting API\n"
            "    3. Get an access token → set [white]TIKTOK_ACCESS_TOKEN[/white] in .env\n\n"
            "  Until then, the agent emails you the finished MP4 + caption,\n"
            "  and you tap upload on your phone. ~30 seconds per video."
        ),
        border_style="yellow",
    ))


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--authorize", action="store_true", help="Run OAuth flow only")
    args = parser.parse_args()

    if args.authorize:
        authorize_youtube()
        return

    console.print(Panel(
        Text.from_markup("[bold white]ViralRecycler Setup[/bold white]"),
        border_style="blue"))
    install_deps()
    check_ffmpeg()
    authorize_youtube()
    setup_tiktok_note()
    console.print("\n[bold green]Setup complete.[/bold green]")
    console.print("Next: drop URLs into [white]data/vr_sources.json[/white] and run:")
    console.print("  [white]python3 run_viral_recycler_auto.py[/white]")


if __name__ == "__main__":
    main()
