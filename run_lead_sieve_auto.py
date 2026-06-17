#!/usr/bin/env python3
"""Lead Sieve autonomous loop — daily lead scoring and hot-list delivery.

Usage:
  python3 run_lead_sieve_auto.py          # one cycle
  python3 run_lead_sieve_auto.py --loop   # run every 24 hours
"""
import argparse
import time
from datetime import datetime

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from lead_sieve.tools import run_full_cycle
from paywall.agent_paywall import paywall_prompt
from autonomous.self_healing import with_healing

console = Console()

AGENT_KEY = "lead_sieve"


@with_healing(AGENT_KEY)
def cycle():
    console.print(Panel(
        Text.from_markup(
            f"[bold white]Lead Sieve Cycle[/bold white]\n"
            f"[dim]{datetime.now():%Y-%m-%d %H:%M:%S}[/dim]"),
        title="[bold red]Wholesale Omniverse — Lead Sieve[/bold red]",
        border_style="red"))

    r = run_full_cycle()

    console.print(f"  [cyan]Leads scored:[/cyan]   {r.get('leads_scored', 0)}")
    console.print(f"  [red]HOT leads:[/red]      {r.get('hot_leads', 0)}")
    console.print(f"  [yellow]WARM leads:[/yellow]     {r.get('warm_leads', 0)}")
    console.print(f"  [dim]COLD leads:[/dim]      {r.get('cold_leads', 0)}")
    console.print(f"  [green]Follow-ups triggered:[/green] {r.get('followup_triggered', 0)}")
    console.print(f"  [green]Digest sent:[/green]          {r.get('digest_sent', 0)}")
    console.print(f"  [dim]Report:[/dim]          {r.get('report_path', '')}")
    console.print(f"  [white]MRR:[/white]             ${r.get('mrr', 0):.0f}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--loop", action="store_true",
                        help="Run every 24 hours instead of once")
    args = parser.parse_args()

    if not paywall_prompt(AGENT_KEY):
        return

    if args.loop:
        while True:
            cycle()
            time.sleep(86400)
    else:
        cycle()


if __name__ == "__main__":
    main()
