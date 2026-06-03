#!/usr/bin/env python3
"""ModBot autonomous loop — classify comment batches, deliver, pitch leads."""
import time, argparse
from datetime import datetime
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from modbot.tools import run_full_cycle
from paywall.agent_paywall import paywall_prompt
from autonomous.self_healing import with_healing

console = Console()


@with_healing("modbot")
def cycle():
    console.print(Panel(
        Text.from_markup(
            f"[bold white]ModBot Cycle[/bold white]\n"
            f"[dim]{datetime.now():%Y-%m-%d %H:%M:%S}[/dim]"),
        title="[bold blue]Wholesale Omniverse — ModBot[/bold blue]",
        border_style="blue"))
    r = run_full_cycle()
    console.print(f"  [cyan]Batches processed:[/cyan] {r.get('batches_processed', 0)}")
    console.print(f"  [cyan]Comments classified:[/cyan] {r.get('comments_classified', 0)}")
    console.print(f"  [red]Hide:[/red] {r.get('action_hide', 0)}  "
                  f"[yellow]Flag:[/yellow] {r.get('action_flag', 0)}  "
                  f"[green]Reply:[/green] {r.get('action_reply', 0)}  "
                  f"[dim]Leave:[/dim] {r.get('action_leave', 0)}")
    console.print(f"  [cyan]Lead pitches:[/cyan]     {r.get('outreach_sent', 0)}")
    console.print(f"  [green]Subs delivered:[/green]    {r.get('fulfillment_sent', 0)}")
    console.print(f"  [white]MRR:[/white]               ${r.get('mrr', 0):.0f}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=int, default=0)
    args = parser.parse_args()
    if not paywall_prompt("modbot"):
        return
    while True:
        cycle()
        if args.interval <= 0:
            break
        time.sleep(args.interval * 60)


if __name__ == "__main__":
    main()
