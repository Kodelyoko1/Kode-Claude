#!/usr/bin/env python3
"""
Amazon Influencer <> Pinterest Agent — storefront curation, SEO pin
generation, compliant dispatch, and performance tracking.

Usage:
  python3 run_amazon_pinterest_auto.py                    # one cycle, live post
  python3 run_amazon_pinterest_auto.py --dry-run           # show what would post
  python3 run_amazon_pinterest_auto.py --status            # credentials + storefront summary
  python3 run_amazon_pinterest_auto.py --max-pins 3        # cap this run's batch size
  python3 run_amazon_pinterest_auto.py --board <board_id>  # override board routing
  python3 run_amazon_pinterest_auto.py --performance       # fetch Pinterest analytics + rank top pins
  python3 run_amazon_pinterest_auto.py --history           # show recent dispatch log

Setup: see amazon_pinterest_agent/BLUEPRINT.md for the full architecture,
compliance rules, and env vars (AMAZON_ASSOCIATE_TAG, PINTEREST_ACCESS_TOKEN,
PINTEREST_BOARD_ID / AP_BOARD_MAP). Product manifest lives in
data/ap_storefront.json — see that file for the expected shape.
"""
import argparse
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from amazon_pinterest_agent.tools import run_full_cycle, status, history, fetch_performance, top_performers
from paywall.agent_paywall import paywall_prompt
from autonomous.self_healing import run_with_healing

console = Console()


def cmd_status():
    s = status()
    tbl = Table(title="Amazon <> Pinterest Agent Status", border_style="blue")
    tbl.add_column("Check")
    tbl.add_column("Value")
    tbl.add_row("Associate tag configured", "[green]yes[/green]" if s["associate_tag_set"] else "[red]no[/red]")
    tbl.add_row("Pinterest token configured", "[green]yes[/green]" if s["pinterest_token_set"] else "[red]no[/red]")
    tbl.add_row("Board mapping configured", "[green]yes[/green]" if s["board_mapping_set"] else "[red]no[/red]")
    tbl.add_row("Products in storefront manifest", str(s["products_in_storefront"]))
    tbl.add_row("Active products", str(s["active_products"]))
    console.print(tbl)


def cmd_history():
    rows = history(limit=15)
    if not rows:
        console.print("[dim]No dispatch history yet.[/dim]")
        return
    tbl = Table(title="Recent Pin Dispatches", border_style="green")
    tbl.add_column("When")
    tbl.add_column("ASIN")
    tbl.add_column("Status")
    tbl.add_column("Detail")
    for r in rows:
        color = {"posted": "green", "dry_run": "cyan", "blocked": "yellow",
                  "skipped": "yellow", "failed": "red"}.get(r.get("status"), "white")
        detail = r.get("error") or ", ".join(r.get("violations", [])) or r.get("pin_id") or "—"
        tbl.add_row(r.get("dispatched_at", "")[:19].replace("T", " "),
                    r.get("asin", "?"), f"[{color}]{r.get('status')}[/{color}]", str(detail))
    console.print(tbl)


def cmd_performance():
    result = fetch_performance()
    if result.get("status") != "ok":
        console.print(f"[yellow]Performance fetch skipped: {result.get('reason')}[/yellow]")
        return
    console.print(f"[green]Checked {result['pins_checked']} pins.[/green]\n")
    top = top_performers()
    if not top:
        console.print("[dim]No cached performance data yet.[/dim]")
        return
    tbl = Table(title="Top Performing Pins (by outbound/affiliate clicks)", border_style="magenta")
    tbl.add_column("ASIN")
    tbl.add_column("Pin ID")
    tbl.add_column("Outbound Clicks")
    for row in top:
        tbl.add_row(row.get("asin", "?"), row.get("pin_id", "?"), str(row.get("outbound_clicks", 0)))
    console.print(tbl)


def cmd_dispatch(max_pins, board, dry_run):
    info = "(DRY RUN — no actual pins posted)" if dry_run else ""
    console.print(Panel(
        Text.from_markup(
            f"[bold white]Amazon <> Pinterest Cycle[/bold white] {info}\n"
            f"[dim]{datetime.now():%Y-%m-%d %H:%M:%S}[/dim]"
        ),
        title="[bold blue]Wholesale Omniverse — Amazon Influencer x Pinterest Agent[/bold blue]",
        border_style="blue",
    ))

    def cycle():
        return run_full_cycle(max_pins=max_pins, board_override=board, dry_run=dry_run)

    result = run_with_healing("amazon_pinterest", cycle)

    if result.get("status") == "skipped":
        console.print(f"[yellow]Skipped: {result.get('reason')}[/yellow]")
        return

    tbl = Table(title="Dispatch Results", border_style="green")
    tbl.add_column("ASIN")
    tbl.add_column("Status")
    tbl.add_column("Detail")
    for r in result["results"]:
        color = {"posted": "green", "dry_run": "cyan", "blocked": "yellow",
                  "skipped": "yellow", "failed": "red"}.get(r.get("status"), "white")
        detail = r.get("error") or ", ".join(r.get("violations", [])) or r.get("pin_id") or "—"
        tbl.add_row(r.get("asin", "?"), f"[{color}]{r.get('status')}[/{color}]", str(detail))
    console.print(tbl)

    console.print(
        f"\n  Batch size: {result['batch_size']}   "
        f"[green]{result['posted']} posted[/green]   "
        f"[yellow]{result['blocked']} blocked[/yellow]   "
        f"[red]{result['failed']} failed[/red]"
    )


def main():
    parser = argparse.ArgumentParser(description="Amazon Influencer <> Pinterest Agent")
    parser.add_argument("--status", action="store_true", help="Show credential + storefront status")
    parser.add_argument("--history", action="store_true", help="Show recent dispatch history")
    parser.add_argument("--performance", action="store_true", help="Fetch Pinterest analytics, rank top pins")
    parser.add_argument("--dry-run", action="store_true", help="Show what would post without posting")
    parser.add_argument("--max-pins", type=int, default=None, help="Cap this run's batch size")
    parser.add_argument("--board", default=None, help="Override board routing for this run")
    args = parser.parse_args()

    if args.status:
        cmd_status()
        return
    if args.history:
        cmd_history()
        return
    if args.performance:
        cmd_performance()
        return

    if not paywall_prompt("amazon_pinterest"):
        return

    cmd_dispatch(args.max_pins, args.board, args.dry_run)


if __name__ == "__main__":
    main()
