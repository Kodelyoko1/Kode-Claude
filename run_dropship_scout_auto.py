#!/usr/bin/env python3
"""DropshipScout autonomous loop — refresh trends + update public page + weekly digest.

Usage:
  python3 run_dropship_scout_auto.py                  # one cycle
  python3 run_dropship_scout_auto.py --interval 60    # every 60 min
  python3 run_dropship_scout_auto.py --diagnose       # preflight + per-source health + page freshness
  python3 run_dropship_scout_auto.py --health-report  # per-source run-history table
  python3 run_dropship_scout_auto.py --subscribers    # list subscribers + MRR
  python3 run_dropship_scout_auto.py --force-deliver  # bypass Monday-only gate (delivers immediately)
"""
import sys
import time
import argparse
from datetime import datetime
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from dropship_scout.tools import run_full_cycle
from paywall.agent_paywall import paywall_prompt
from autonomous.self_healing import with_healing

console = Console()


@with_healing("dropship_scout")
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
    parser.add_argument("--diagnose", action="store_true",
                        help="Preflight + per-source health + page freshness, then exit")
    parser.add_argument("--health-report", action="store_true",
                        help="Per-source run-history table, then exit")
    parser.add_argument("--subscribers", action="store_true",
                        help="List subscribers + MRR, then exit")
    parser.add_argument("--force-deliver", action="store_true",
                        help="Run a cycle and deliver to active subscribers even if it's not Monday")
    args = parser.parse_args()

    if args.diagnose:
        from dropship_scout.diagnose import main as diag_main
        sys.exit(diag_main())
    if args.health_report:
        from dropship_scout.health import report_lines, summary
        for line in report_lines():
            console.print(line)
        s = summary()
        if s["sources"]:
            console.print()
            console.print(
                f"  [white]{s['healthy']}[/white] healthy / "
                f"[yellow]{s['warning']}[/yellow] warning  "
                f"(threshold ≥{s['alert_threshold']} consecutive zeros)  "
                f"all-time found: [white]{s['total_found_all_time']}[/white]"
            )
        return
    if args.subscribers:
        from dropship_scout.subscription import listing
        out = listing()
        console.print(Panel(
            Text.from_markup(
                f"[bold]Subscribers[/bold]\n\n"
                f"  Total:    {out['total']}\n"
                f"  Active:   [green]{out['active']}[/green]\n"
                f"  Pending:  [yellow]{out['pending']}[/yellow]\n"
                f"  Churned:  {out['churned']}\n"
                f"  MRR:      [green]${out['mrr']}/mo[/green]"
            ),
            border_style="blue",
        ))
        for s in out["subscribers"]:
            console.print(
                f"  [dim]{s.get('status','?'):>8s}[/dim]  "
                f"{s.get('email',''):<40s}  {s.get('name','')}"
            )
        return
    if args.force_deliver:
        from dropship_scout.tools import gather_trending, deliver_subscribers, update_public_page
        from autonomous import storage
        console.print("[yellow]Force-delivering: bypassing Monday-only gate[/yellow]")
        trends = gather_trending()
        storage.save("ds_latest_trends.json", trends)
        update_public_page(trends)
        r = deliver_subscribers(trends)
        console.print(Panel(
            Text.from_markup(
                f"[bold]Force-delivered[/bold]\n\n"
                f"  Subscriber digests sent: [green]{r.get('fulfillment_sent', 0)}[/green]"
            ),
            border_style="green",
        ))
        return

    if not paywall_prompt("dropship_scout"):
        return
    while True:
        cycle()
        if args.interval <= 0:
            break
        time.sleep(args.interval * 60)


if __name__ == "__main__":
    main()
