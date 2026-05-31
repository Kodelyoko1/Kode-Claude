#!/usr/bin/env python3
"""ShowNotes autonomous loop — transcripts → structured show notes, deliver to subs."""
import time, argparse
from datetime import datetime
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from shownotes.tools import run_full_cycle
from paywall.agent_paywall import paywall_prompt

console = Console()


def cycle():
    console.print(Panel(
        Text.from_markup(
            f"[bold white]ShowNotes Cycle[/bold white]\n"
            f"[dim]{datetime.now():%Y-%m-%d %H:%M:%S}[/dim]"),
        title="[bold blue]Wholesale Omniverse — ShowNotes[/bold blue]",
        border_style="blue"))
    r = run_full_cycle()
    console.print(f"  [cyan]Show notes built:[/cyan]    {r.get('shownotes_produced', 0)}")
    console.print(f"  [cyan]Sample pitches:[/cyan]      {r.get('outreach_sent', 0)}")
    console.print(f"  [green]Subs delivered:[/green]      {r.get('fulfillment_sent', 0)}")
    console.print(f"  [white]MRR:[/white]                 ${r.get('mrr', 0):.0f}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=int, default=0)
    args = parser.parse_args()
    if not paywall_prompt("shownotes"):
        return
    while True:
        cycle()
        if args.interval <= 0:
            break
        time.sleep(args.interval * 60)


if __name__ == "__main__":
    main()
