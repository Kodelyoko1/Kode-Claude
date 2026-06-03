#!/usr/bin/env python3
"""
Client Prospector automation — finds paying clients for SAAS + OAS products.

Scrapes wholesalers, investors, and flippers from Hotfrog in target markets,
then sends pitch emails for either the Wholesale Deal Analyzer (SAAS)
or Outreach-as-a-Service (OAS).

Usage:
  python3 run_prospector_auto.py                                 # default product=saas, markets from env
  python3 run_prospector_auto.py --product oas
  python3 run_prospector_auto.py --markets "Detroit,MI;Atlanta,GA"
  python3 run_prospector_auto.py --no-email                      # scrape only, don't pitch
  python3 run_prospector_auto.py --pitch-only                    # pitch existing 'new' prospects
  python3 run_prospector_auto.py --diagnose                      # preflight + funnel + revenue ceiling
  python3 run_prospector_auto.py --followup                      # send 2nd-touch to silent pitched prospects
  python3 run_prospector_auto.py --expire-stale                  # flip followed-up → stale after STALE_DAYS
  python3 run_prospector_auto.py --digest                        # email owner the daily action digest
  python3 run_prospector_auto.py --digest-dry-run                # write digest to disk, don't email
"""
import os
import sys
import time
import argparse
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from client_prospector.tools import (
    find_prospects_hotfrog, pitch_all_new, list_prospects, PRODUCT_INFO,
)

console = Console()

DEFAULT_MARKETS = [
    ("Detroit", "MI"), ("Memphis", "TN"), ("Atlanta", "GA"),
    ("Cleveland", "OH"), ("Birmingham", "AL"), ("Jacksonville", "FL"),
]


def _parse_markets(arg: str) -> list:
    out = []
    for chunk in arg.split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "," in chunk:
            city, state = chunk.split(",", 1)
        else:
            parts = chunk.rsplit(" ", 1)
            city, state = parts[0], parts[-1] if len(parts) > 1 else ""
        out.append((city.strip(), state.strip().upper()))
    return out


from autonomous.self_healing import with_healing


@with_healing("prospector")
def run_cycle(markets: list, product: str, auto_email: bool):
    info = PRODUCT_INFO[product]
    console.print(Panel(
        Text.from_markup(
            f"[bold white]Client Prospecting Cycle — {info['name']}[/bold white]\n"
            f"[dim]{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  |  "
            f"{len(markets)} market(s)  |  pitch={auto_email}[/dim]"
        ),
        title="[bold blue]Wholesale Omniverse — Client Prospector[/bold blue]",
        border_style="blue",
    ))

    before = list_prospects()

    results = []
    for city, state in markets:
        console.print(f"\n[yellow]Scraping {city}, {state}...[/yellow]")
        r = find_prospects_hotfrog(city, state)
        console.print(f"  [green]{r['prospects_found']} new prospects[/green]")
        results.append({"city": city, "state": state, **r})

    if results:
        tbl = Table(title="Prospects by Market", border_style="green")
        tbl.add_column("City")
        tbl.add_column("Found")
        for r in results:
            tbl.add_row(f"{r['city']}, {r['state']}", str(r["prospects_found"]))
        console.print(tbl)

    pitch_result = {}
    if auto_email:
        console.print(f"\n[yellow]Sending {info['name']} pitch to new prospects...[/yellow]")
        pitch_result = pitch_all_new(product=product, limit=25)
        console.print(
            f"  Pitched: [green]{pitch_result.get('sent', 0)}[/green] "
            f"of {pitch_result.get('attempted', 0)}"
        )
        for f in pitch_result.get("failures", []):
            console.print(f"  [dim]✗ {f['prospect_id']}: {f.get('error')}[/dim]")

    after = list_prospects()
    console.print(Panel(
        Text.from_markup(
            f"[bold green]Cycle Complete[/bold green]\n\n"
            f"  Prospects before:  [white]{before['total']}[/white]\n"
            f"  Prospects after:   [white]{after['total']}[/white]\n"
            f"  New added:         [white]{after['total'] - before['total']}[/white]\n"
            f"  Pitches sent:      [white]{pitch_result.get('sent', 0)}[/white]\n"
            f"  Pitched (lifetime):[white]{after['pitched']}[/white]\n"
            f"  Replied:           [white]{after['replied']}[/white]\n"
            f"  Converted:         [white]{after['converted']}[/white]"
        ),
        title="[bold green]Summary[/bold green]",
        border_style="green",
    ))

    if after["converted"] == 0 and after["replied"] == 0:
        console.print("[dim]→ Run daily. Expect first replies after 50–100 pitches sent.[/dim]")
    elif after["replied"] > 0 and after["converted"] == 0:
        console.print(
            f"[bold yellow]→ {after['replied']} prospects replied — "
            f"check inbox, then run [bold]python3 onboard_client.py[/bold] to convert.[/bold yellow]"
        )


def main():
    parser = argparse.ArgumentParser(description="Client Prospector — find paying SAAS/OAS clients")
    parser.add_argument("--product",   default="saas", choices=["saas", "oas"], help="Which product to pitch")
    parser.add_argument("--markets",   default="", help='Markets as "City,ST;City,ST"')
    parser.add_argument("--no-email",  action="store_true", help="Scrape only, don't send pitches")
    parser.add_argument("--pitch-only", action="store_true", help="Skip scraping, just pitch existing 'new' prospects")
    parser.add_argument("--diagnose",  action="store_true", help="Preflight + funnel + revenue ceiling, then exit")
    parser.add_argument("--followup",  action="store_true", help="Send 2nd-touch follow-ups, then exit")
    parser.add_argument("--expire-stale", action="store_true",
                        help="Mark followed-up prospects with no reply as stale, then exit")
    parser.add_argument("--digest",    action="store_true", help="Email owner the daily action digest, then exit")
    parser.add_argument("--digest-dry-run", action="store_true",
                        help="Write digest to data/cp_digests/ without emailing, then exit")
    parser.add_argument("--interval",  type=int, default=0, help="Repeat every N minutes")
    args = parser.parse_args()

    # One-shot subcommands — no scrape/pitch cycle, no --interval loop.
    if args.diagnose:
        from client_prospector.diagnose import main as diag_main
        sys.exit(diag_main())
    if args.followup:
        from client_prospector.followup import send_followups
        out = send_followups()
        console.print(Panel(
            Text.from_markup(
                f"[bold]Follow-up batch[/bold]\n\n"
                f"  Attempted: {out['attempted']}\n"
                f"  Sent:      [green]{out['sent']}[/green]\n"
                f"  Failures:  {len(out.get('failures', []))}"
            ),
            border_style="green",
        ))
        for f in out.get("failures", [])[:5]:
            console.print(f"  [dim]✗ {f['prospect_id']}: {f.get('error')}[/dim]")
        return
    if args.expire_stale:
        from client_prospector.followup import expire_stale
        out = expire_stale()
        console.print(Panel(
            Text.from_markup(
                f"[bold]Stale sweep[/bold]\n\n"
                f"  Expired: [yellow]{out['expired']}[/yellow]"
            ),
            border_style="yellow",
        ))
        return
    if args.digest or args.digest_dry_run:
        from client_prospector.digest import send_owner_digest
        out = send_owner_digest(dry_run=args.digest_dry_run)
        status_color = "green" if out.get("status") in ("sent", "dry_run") else "red"
        console.print(Panel(
            Text.from_markup(
                f"[bold]Owner digest[/bold]\n\n"
                f"  Status:           [{status_color}]{out.get('status')}[/{status_color}]\n"
                f"  Awaiting onboard: {out.get('awaiting_onboard', 0)}\n"
                f"  Preview:          {out.get('preview_path', '')}"
                + (f"\n  Error: [red]{out['error']}[/red]" if out.get("error") else "")
            ),
            border_style=status_color,
        ))
        return

    if args.markets:
        markets = _parse_markets(args.markets)
    else:
        env_markets = os.environ.get("PROSPECTOR_MARKETS", "")
        markets = _parse_markets(env_markets) if env_markets else DEFAULT_MARKETS

    while True:
        if args.pitch_only:
            from client_prospector.tools import pitch_all_new, PRODUCT_INFO
            r = pitch_all_new(product=args.product, limit=25)
            info = PRODUCT_INFO[args.product]
            console.print(Panel(
                Text.from_markup(
                    f"[bold]Pitch-only run — {info['name']}[/bold]\n\n"
                    f"  Sent:      {r['sent']}\n"
                    f"  Attempted: {r['attempted']}\n"
                    f"  Failures:  {len(r.get('failures', []))}"
                ),
                border_style="green",
            ))
        else:
            run_cycle(markets, product=args.product, auto_email=not args.no_email)

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
