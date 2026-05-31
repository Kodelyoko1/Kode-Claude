#!/usr/bin/env python3
"""PropScout autonomous loop — free PropStream-style prospect engine."""
import argparse
import time
from datetime import datetime

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from propscout.tools import run_full_cycle
from paywall.agent_paywall import paywall_prompt

console = Console()


def cycle():
    console.print(Panel(
        Text.from_markup(
            f"[bold white]PropScout Cycle[/bold white]\n"
            f"[dim]{datetime.now():%Y-%m-%d %H:%M:%S}[/dim]"),
        title="[bold blue]Wholesale Omniverse — PropScout[/bold blue]",
        border_style="blue"))
    r = run_full_cycle()
    console.print(f"  [cyan]Cells scanned:[/cyan]            {r.get('cells_run', 0)}")
    console.print(f"  [cyan]Prospects found:[/cyan]          {r.get('prospects_found', 0)}")
    console.print(f"    [dim]with email:[/dim]               {r.get('with_email', 0)}")
    console.print(f"    [dim]with phone:[/dim]               {r.get('with_phone', 0)}")
    console.print(f"  [green]Cold-email drafts written:[/green] {r.get('drafts_written', 0)}")
    console.print(f"  [green]Outreach auto-sent:[/green]        {r.get('outreach_sent', 0)}")
    console.print(f"  [white]Owner digest sent:[/white]         {r.get('digest_sent', 0)}")
    console.print(f"  [white]MRR:[/white]                       ${r.get('mrr', 0):.0f}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=int, default=0,
                        help="Loop every N minutes (0 = one-shot)")
    args = parser.parse_args()
    if not paywall_prompt("propscout"):
        return
    while True:
        cycle()
        if args.interval <= 0:
            break
        time.sleep(args.interval * 60)


if __name__ == "__main__":
    main()
