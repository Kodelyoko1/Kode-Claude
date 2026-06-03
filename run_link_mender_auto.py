#!/usr/bin/env python3
"""LinkMender autonomous loop — preview audits + monthly client audits.

Usage:
  python3 run_link_mender_auto.py                    # one full cycle (discover + acquire + fulfill)
  python3 run_link_mender_auto.py --interval 1440    # daily
  python3 run_link_mender_auto.py --diagnose         # preflight: SMTP + HTTP + Bing + funnel
  python3 run_link_mender_auto.py --health-report    # per-query yield + funnel from prospects.json
  python3 run_link_mender_auto.py --bing-probe       # live Bing search test
  python3 run_link_mender_auto.py --clients          # list clients + MRR
"""
import sys
import time, argparse
from datetime import datetime
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from link_mender.tools import run_full_cycle
from paywall.agent_paywall import paywall_prompt
from autonomous.self_healing import with_healing

console = Console()


@with_healing("link_mender")
def cycle():
    console.print(Panel(
        Text.from_markup(
            f"[bold white]LinkMender Cycle[/bold white]\n"
            f"[dim]{datetime.now():%Y-%m-%d %H:%M:%S}[/dim]"),
        title="[bold blue]Wholesale Omniverse — LinkMender[/bold blue]",
        border_style="blue"))
    r = run_full_cycle()
    console.print(f"  [cyan]Preview audits:[/cyan]   {r.get('new_audits', 0)}")
    console.print(f"  [cyan]Outreach sent:[/cyan]    {r.get('outreach_sent', 0)}")
    console.print(f"  [green]Client audits:[/green]    {r.get('fulfillment_sent', 0)}")
    console.print(f"  [white]MRR:[/white]              ${r.get('mrr', 0):.0f}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=int, default=0)
    parser.add_argument("--diagnose", action="store_true",
                        help="Preflight: SMTP + HTTP + Bing + funnel, then exit")
    parser.add_argument("--health-report", action="store_true",
                        help="Per-query yield + funnel breakdown derived from lm_prospects.json")
    parser.add_argument("--bing-probe", action="store_true",
                        help="Run a single live Bing search and report results, then exit")
    parser.add_argument("--clients", action="store_true",
                        help="List clients + MRR, then exit")
    args = parser.parse_args()

    if args.diagnose:
        from link_mender.diagnose import main as diag_main
        sys.exit(diag_main())
    if args.health_report:
        from link_mender.health import report_lines
        for line in report_lines():
            console.print(line)
        return
    if args.bing_probe:
        from link_mender.tools import _bing_search, DEFAULT_PROSPECT_QUERIES
        # Use today's actual rotated query so the probe matches what discover_prospects uses
        query = DEFAULT_PROSPECT_QUERIES[datetime.now().day % len(DEFAULT_PROSPECT_QUERIES)]
        results = _bing_search(query, n=5)
        color = "green" if results else "red"
        console.print(Panel(
            Text.from_markup(
                f"[bold]Bing search probe[/bold]\n\n"
                f"  Query:    {query}\n"
                f"  Results:  [{color}]{len(results)}[/{color}]"
            ),
            border_style=color,
        ))
        for r in results:
            wrapped = " [yellow](still wrapped /ck/a)[/yellow]" if "/ck/a" in r["url"] else ""
            console.print(f"  · {r['title'][:60]}{wrapped}")
            console.print(f"    [dim]{r['url'][:90]}[/dim]")
        return
    if args.clients:
        from link_mender.clients import listing
        out = listing()
        console.print(Panel(
            Text.from_markup(
                f"[bold]Clients[/bold]\n\n"
                f"  Total:    {out['total']}\n"
                f"  Active:   [green]{out['active']}[/green]\n"
                f"  Pending:  [yellow]{out['pending']}[/yellow]\n"
                f"  Churned:  {out['churned']}\n"
                f"  MRR:      [green]${out['mrr']}/mo[/green]"
            ),
            border_style="blue",
        ))
        for c in out["clients"]:
            console.print(
                f"  [dim]{c.get('status','?'):>8s}[/dim]  "
                f"{c.get('plan',''):<14s}  {c.get('site_slug','')}"
            )
        return

    if not paywall_prompt("link_mender"):
        return
    while True:
        cycle()
        if args.interval <= 0:
            break
        time.sleep(args.interval * 60)


if __name__ == "__main__":
    main()
