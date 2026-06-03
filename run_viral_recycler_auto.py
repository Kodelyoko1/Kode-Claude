#!/usr/bin/env python3
"""ViralRecycler autonomous loop — download, transform, post."""
import time, argparse
from datetime import datetime
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from viral_recycler.tools import run_full_cycle
from paywall.agent_paywall import paywall_prompt
from autonomous.self_healing import with_healing

console = Console()


@with_healing("viral_recycler")
def cycle(max_uploads: int):
    console.print(Panel(
        Text.from_markup(
            f"[bold white]ViralRecycler Cycle[/bold white]\n"
            f"[dim]{datetime.now():%Y-%m-%d %H:%M:%S}[/dim]"),
        title="[bold blue]Wholesale Omniverse — ViralRecycler[/bold blue]",
        border_style="blue"))
    r = run_full_cycle(max_uploads=max_uploads)
    console.print(f"  [green]Uploaded:[/green]        {r.get('uploaded', 0)}")
    console.print(f"  [yellow]Skipped (cap):[/yellow]   {r.get('skipped', 0)}")
    if r.get("errors"):
        console.print(f"  [red]Errors:[/red]")
        for e in r["errors"]:
            console.print(f"    [dim]{e.get('stage', '?')}:[/dim] {e.get('error', '')}")
    for s in r.get("successes", []):
        yt_url = s.get("youtube", {}).get("shorts_url", "")
        console.print(f"  [white]→[/white] [cyan]{s['hook'][:60]}[/cyan]")
        if yt_url:
            console.print(f"     YouTube: {yt_url}")
        tt = s.get("tiktok", {})
        if tt.get("status") == "uploaded":
            console.print(f"     TikTok:  posted (publish_id={tt.get('publish_id', '?')})")
        elif tt.get("status") == "handed_off":
            console.print(f"     TikTok:  emailed to {tt.get('to', '')} for manual post")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=int, default=0)
    parser.add_argument("--max-uploads", type=int, default=1,
                        help="Max uploads per run (daily safety cap also applies)")
    args = parser.parse_args()
    if not paywall_prompt("viral_recycler"):
        return
    while True:
        cycle(args.max_uploads)
        if args.interval <= 0:
            break
        time.sleep(args.interval * 60)


if __name__ == "__main__":
    main()
