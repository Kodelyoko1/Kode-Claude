#!/usr/bin/env python3
"""
Cash buyer recruitment automation — no API key required.
Searches REIA sites + Hotfrog for investors and emails them intros.
"""
import time
import argparse
from datetime import datetime
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from buyer_finder.tools import (
    get_buyers_summary, run_all_markets, recruit_buyers_full_cycle,
)
from paywall.agent_paywall import paywall_prompt

console = Console()


def run_cycle(cities: list = None, auto_email: bool = True):
    console.print(Panel(
        Text.from_markup(
            f"[bold white]Cash Buyer Recruitment Cycle[/bold white]\n"
            f"[dim]{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}[/dim]"
        ),
        title="[bold blue]Wholesale Omniverse — Cash Buyer Finder[/bold blue]",
        border_style="blue",
    ))

    # Before snapshot
    before = get_buyers_summary()
    console.print(f"\n[cyan]Buyers on file before:[/cyan] {before['total_buyers']}")

    if cities:
        # Run specific cities
        all_results = []
        total_new = 0
        total_emailed = 0
        for entry in cities:
            city, state = entry.get("city", ""), entry.get("state", "")
            if not city or not state:
                continue
            console.print(f"\n[yellow]Recruiting buyers in {city}, {state}...[/yellow]")
            result = recruit_buyers_full_cycle(city, state, auto_email=auto_email)
            total_new     += result.get("total_new_buyers", 0)
            total_emailed += result.get("emails_sent", 0)
            all_results.append({"city": city, "state": state, **result})
            console.print(f"  [green]{result.get('total_new_buyers', 0)} new buyers found[/green]")
    else:
        # Auto-detect markets from pipeline
        console.print("\n[yellow]Running all top pipeline markets...[/yellow]")
        result = run_all_markets(auto_email=auto_email)
        total_new     = result.get("total_new_buyers", 0)
        total_emailed = result.get("total_emails_sent", 0)
        all_results   = result.get("per_market", [])

    # Market breakdown table
    if all_results:
        mkt_table = Table(title="Results by Market", border_style="green")
        mkt_table.add_column("City")
        mkt_table.add_column("Bing")
        mkt_table.add_column("Craigslist")
        mkt_table.add_column("Redfin")
        mkt_table.add_column("Total New")
        mkt_table.add_column("Emailed")
        for r in all_results:
            mkt_table.add_row(
                f"{r.get('city')}, {r.get('state')}",
                str(r.get("from_bing", 0)),
                str(r.get("from_craigslist", 0)),
                str(r.get("from_redfin", 0)),
                str(r.get("total_new_buyers", 0)),
                str(r.get("emails_sent", 0)),
            )
        console.print(mkt_table)

    # After snapshot
    after = get_buyers_summary()
    console.print(Panel(
        Text.from_markup(
            f"[bold green]Cycle Complete[/bold green]\n\n"
            f"  Buyers before:   [white]{before['total_buyers']}[/white]\n"
            f"  Buyers after:    [white]{after['total_buyers']}[/white]\n"
            f"  New added:       [white]{after['total_buyers'] - before['total_buyers']}[/white]\n"
            f"  Intros emailed:  [white]{total_emailed}[/white]\n"
            f"  Total emailed:   [white]{after['emailed']}[/white]\n"
            f"  Deals closed:    [white]{after['deals_closed']}[/white]"
        ),
        title="[bold green]Summary[/bold green]",
        border_style="green",
    ))

    if after["total_buyers"] < 20:
        console.print("[bold yellow]→ Run again tomorrow. You need 20+ buyers to close deals consistently.[/bold yellow]")
    elif after["total_buyers"] < 50:
        console.print("[yellow]→ Good progress. Run weekly until you hit 50+ buyers.[/yellow]")
    else:
        console.print("[green]→ Strong buyers list. Run monthly to keep it fresh.[/green]")


def main():
    parser = argparse.ArgumentParser(description="Buyer recruitment automation — no API key required")
    parser.add_argument("--interval", type=int, default=0, help="Repeat every N minutes")
    parser.add_argument("--no-email", action="store_true", help="Find buyers but don't email them")
    args = parser.parse_args()

    if not paywall_prompt("buyer_finder"):
        return

    while True:
        run_cycle(auto_email=not args.no_email)
        if args.interval <= 0:
            break
        console.print(f"\n[dim]Next run in {args.interval} minutes. Ctrl+C to stop.[/dim]")
        try:
            time.sleep(args.interval * 60)
        except KeyboardInterrupt:
            console.print("\n[dim]Stopped.[/dim]")
            break


if __name__ == "__main__":
    main()
