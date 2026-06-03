#!/usr/bin/env python3
"""PodCleaner autonomous loop — clean audio, deliver to subs, pitch leads."""
import time, argparse
from datetime import datetime
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from podcleaner.tools import run_full_cycle
from paywall.agent_paywall import paywall_prompt
from autonomous.self_healing import with_healing

console = Console()


@with_healing("podcleaner")
def cycle():
    console.print(Panel(
        Text.from_markup(
            f"[bold white]PodCleaner Cycle[/bold white]\n"
            f"[dim]{datetime.now():%Y-%m-%d %H:%M:%S}[/dim]"),
        title="[bold blue]Wholesale Omniverse — PodCleaner[/bold blue]",
        border_style="blue"))
    r = run_full_cycle()
    console.print(f"  [cyan]Episodes cleaned:[/cyan] {r.get('episodes_cleaned', 0)}")
    console.print(f"  [yellow]Failures:[/yellow]        {r.get('failures', 0)}")
    console.print(f"  [cyan]Silence removed:[/cyan]  {r.get('total_silence_removed_s', 0)}s")
    console.print(f"  [cyan]Lead pitches:[/cyan]     {r.get('outreach_sent', 0)}")
    console.print(f"  [green]Subs delivered:[/green]   {r.get('fulfillment_sent', 0)}")
    console.print(f"  [white]MRR:[/white]              ${r.get('mrr', 0):.0f}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=int, default=0)
    args = parser.parse_args()
    if not paywall_prompt("podcleaner"):
        return
    while True:
        cycle()
        if args.interval <= 0:
            break
        time.sleep(args.interval * 60)


if __name__ == "__main__":
    main()
