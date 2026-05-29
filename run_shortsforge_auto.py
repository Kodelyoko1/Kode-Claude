#!/usr/bin/env python3
"""ShortsForge autonomous loop — turn transcripts into YouTube Shorts briefs."""
import time, argparse
from datetime import datetime
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from shortsforge.tools import run_full_cycle, set_channel_config, get_channel_config
from paywall.agent_paywall import paywall_prompt

console = Console()


def cycle():
    cfg = get_channel_config()
    console.print(Panel(
        Text.from_markup(
            f"[bold white]ShortsForge Cycle[/bold white]\n"
            f"[dim]{datetime.now():%Y-%m-%d %H:%M:%S}[/dim]\n"
            f"[dim]Channel: {cfg['channel_name']}[/dim]"),
        title="[bold blue]Wholesale Omniverse — ShortsForge[/bold blue]",
        border_style="blue"))
    r = run_full_cycle()
    console.print(f"  [cyan]Briefs produced:[/cyan]   {r.get('briefs_made', 0)}")
    console.print(f"  [cyan]Outreach sent:[/cyan]     {r.get('outreach_sent', 0)}")
    console.print(f"  [green]Newsletters sent:[/green]  {r.get('newsletters_sent', 0)}")
    console.print(f"  [white]MRR:[/white]              ${r.get('mrr', 0):.0f}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=int, default=0)
    parser.add_argument("--set-channel", type=str, help="Set the channel name")
    parser.add_argument("--set-handle",  type=str, help="Set the channel handle (e.g. @yourchannel)")
    parser.add_argument("--set-substack", type=str, help="Set the Substack URL")
    args = parser.parse_args()

    if any([args.set_channel, args.set_handle, args.set_substack]):
        updates = {}
        if args.set_channel:  updates["channel_name"] = args.set_channel
        if args.set_handle:   updates["channel_handle"] = args.set_handle
        if args.set_substack: updates["substack_url"] = args.set_substack
        result = set_channel_config(**updates)
        console.print(f"[green]✓ Channel config updated:[/green] {result}")
        return

    if not paywall_prompt("shortsforge"):
        return
    while True:
        cycle()
        if args.interval <= 0:
            break
        time.sleep(args.interval * 60)


if __name__ == "__main__":
    main()
