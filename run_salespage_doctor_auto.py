#!/usr/bin/env python3
"""SalesPageDoctor autonomous loop — discover creator pages, audit, send preview reports."""
import time
import argparse
from datetime import datetime
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from salespage_doctor.tools import run_full_cycle
from paywall.agent_paywall import paywall_prompt

console = Console()


def cycle():
    console.print(Panel(
        Text.from_markup(
            f"[bold white]SalesPageDoctor Cycle[/bold white]\n"
            f"[dim]{datetime.now():%Y-%m-%d %H:%M:%S}[/dim]"),
        title="[bold blue]Wholesale Omniverse — SalesPageDoctor[/bold blue]",
        border_style="blue"))
    r = run_full_cycle()
    console.print(f"  [cyan]Prospects discovered:[/cyan] {r.get('discovered', 0)}")
    console.print(f"  [cyan]Audit previews sent:[/cyan]  {r.get('outreach_sent', 0)}")
    console.print(f"  [green]Client audits sent:[/green]   {r.get('fulfillment_sent', 0)}")
    console.print(f"  [white]MRR:[/white]                  ${r.get('mrr', 0):.0f}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=int, default=0)
    args = parser.parse_args()
    if not paywall_prompt("salespage_doctor"):
        return
    while True:
        cycle()
        if args.interval <= 0:
            break
        time.sleep(args.interval * 60)


if __name__ == "__main__":
    main()
