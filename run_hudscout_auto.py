#!/usr/bin/env python3
"""
HUDScout — autonomous government-foreclosed property scraper.
Run:    python3 run_hudscout_auto.py
"""
from datetime import datetime

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from hudscout.tools import run_full_cycle
from paywall.agent_paywall import paywall_prompt

console = Console()


def main():
    if not paywall_prompt("hudscout"):
        return
    console.print(Panel(
        Text.from_markup(
            f"[bold white]HUDScout Cycle[/bold white]\n"
            f"[dim]{datetime.now():%Y-%m-%d %H:%M:%S}[/dim]"
        ),
        title="[bold blue]Wholesale Omniverse — HUDScout[/bold blue]",
        border_style="blue",
    ))
    result = run_full_cycle()
    console.print(
        f"  States searched:    {result['states_searched']}\n"
        f"  Raw listings:       {result['raw_harvested']}\n"
        f"  New leads:          {result['new_leads']}\n"
        f"  Digest fulfilment:  {result['fulfillment_sent']}\n"
        f"  Active subs:        {result.get('active_subs', 0)}\n"
        f"  MRR:                ${result['mrr']:.0f}"
    )
    if result.get("digest_path"):
        console.print(f"  Digest written to:  {result['digest_path']}")


if __name__ == "__main__":
    main()
