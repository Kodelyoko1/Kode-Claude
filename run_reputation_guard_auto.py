#!/usr/bin/env python3
"""ReputationGuard autonomous loop — acquisition + fulfillment."""
import time, argparse
from datetime import datetime
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from reputation_guard.tools import run_full_cycle
from paywall.agent_paywall import paywall_prompt
from autonomous.self_healing import with_healing

console = Console()


@with_healing("reputation_guard")
def cycle():
    console.print(Panel(
        Text.from_markup(
            f"[bold white]ReputationGuard Cycle[/bold white]\n"
            f"[dim]{datetime.now():%Y-%m-%d %H:%M:%S}[/dim]"),
        title="[bold blue]Wholesale Omniverse — ReputationGuard[/bold blue]",
        border_style="blue"))
    r = run_full_cycle()
    console.print(f"  [cyan]New prospects:[/cyan]      {r.get('new_prospects', 0)}")
    console.print(f"  [cyan]Outreach sent:[/cyan]      {r.get('outreach_sent', 0)}")
    console.print(f"  [green]Fulfillment sent:[/green]   {r.get('fulfillment_sent', 0)}")
    console.print(f"  [white]MRR:[/white]                ${r.get('mrr', 0):.0f}")
    console.print(f"  [white]Total revenue:[/white]      ${r.get('total_paid', 0):.0f}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=int, default=0)
    args = parser.parse_args()
    if not paywall_prompt("reputation_guard"):
        return
    while True:
        cycle()
        if args.interval <= 0:
            break
        time.sleep(args.interval * 60)


if __name__ == "__main__":
    main()
