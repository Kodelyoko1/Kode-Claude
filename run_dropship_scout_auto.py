#!/usr/bin/env python3
"""DropshipScout autonomous loop — refresh trends + update public page + weekly digest."""
import time
import argparse
from datetime import datetime
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from dropship_scout.tools import run_full_cycle
from paywall.agent_paywall import paywall_prompt

console = Console()


def cycle():
    console.print(Panel(
        Text.from_markup(
            f"[bold white]DropshipScout Cycle[/bold white]\n"
            f"[dim]{datetime.now():%Y-%m-%d %H:%M:%S}[/dim]"),
        title="[bold blue]Wholesale Omniverse — DropshipScout[/bold blue]",
        border_style="blue"))
    r = run_full_cycle()
    console.print(f"  [cyan]TikTok hashtags scraped:[/cyan]  {r.get('tiktok_hashtags', 0)}")
    console.print(f"  [cyan]Amazon mover products:[/cyan]    {r.get('amazon_products', 0)}")
    console.print(f"  [green]Subscriber digests sent:[/green]  {r.get('fulfillment_sent', 0)}")
    console.print(f"  [white]MRR:[/white]                      ${r.get('mrr', 0):.0f}")
    console.print(f"  [dim]Public page: {r.get('public_page', '')}[/dim]")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=int, default=0)
    args = parser.parse_args()
    if not paywall_prompt("dropship_scout"):
        return
    while True:
        cycle()
        if args.interval <= 0:
            break
        time.sleep(args.interval * 60)


if __name__ == "__main__":
    main()
