#!/usr/bin/env python3
"""TownCrier autonomous loop — build digest + pitch sponsors."""
import time, argparse
from datetime import datetime
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from towncrier.tools import run_full_cycle
from paywall.agent_paywall import paywall_prompt

console = Console()


def cycle():
    console.print(Panel(
        Text.from_markup(
            f"[bold white]TownCrier Cycle[/bold white]\n"
            f"[dim]{datetime.now():%Y-%m-%d %H:%M:%S}[/dim]"),
        title="[bold blue]Wholesale Omniverse — TownCrier[/bold blue]",
        border_style="blue"))
    r = run_full_cycle()
    console.print(f"  [cyan]Events aggregated:[/cyan]  {r.get('events', 0)}")
    console.print(f"  [cyan]Sponsor pitches:[/cyan]   {r.get('new_pitches', 0)} new, {r.get('outreach_sent', 0)} sent")
    console.print(f"  [green]Digest delivered:[/green]  {r.get('sent', 0)} subscribers")
    console.print(f"  [white]MRR:[/white]                ${r.get('mrr', 0):.0f}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=int, default=0)
    args = parser.parse_args()
    if not paywall_prompt("towncrier"):
        return
    while True:
        cycle()
        if args.interval <= 0:
            break
        time.sleep(args.interval * 60)


if __name__ == "__main__":
    main()
