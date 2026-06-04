#!/usr/bin/env python3
"""
TownCrier — hyper-local weekly newsletter (free) + sponsor revenue.

Usage:
  python3 run_towncrier_auto.py                       # one cycle (digest + sponsor pitches)
  python3 run_towncrier_auto.py --interval 60         # loop every N min
  python3 run_towncrier_auto.py --diagnose            # preflight: SMTP, parser, inputs, cadence
  python3 run_towncrier_auto.py --probe-snapshots     # snapshot inventory by city, then exit
  python3 run_towncrier_auto.py --health-report       # per-city event-yield table
  python3 run_towncrier_auto.py --subscribers         # subscriber ledger + per-city counts
  python3 run_towncrier_auto.py --sponsors            # sponsor pipeline + committed revenue
"""
import argparse
import sys
import time
from datetime import datetime

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from towncrier.tools import run_full_cycle
from paywall.agent_paywall import paywall_prompt
from autonomous.self_healing import with_healing

console = Console()


@with_healing("towncrier")
def cycle():
    console.print(Panel(
        Text.from_markup(
            f"[bold white]TownCrier Cycle[/bold white]\n"
            f"[dim]{datetime.now():%Y-%m-%d %H:%M:%S}[/dim]"),
        title="[bold blue]Wholesale Omniverse — TownCrier[/bold blue]",
        border_style="blue"))
    r = run_full_cycle()
    console.print(f"  [cyan]Events aggregated:[/cyan]  {r.get('events', 0)}")
    console.print(f"  [cyan]Sponsor pitches:[/cyan]   {r.get('new_pitches', 0)} new, "
                  f"{r.get('outreach_sent', 0)} sent")
    console.print(f"  [green]Digest delivered:[/green]  {r.get('sent', 0)} subscribers")
    console.print(f"  [white]MRR:[/white]                ${r.get('mrr', 0):.0f}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=int, default=0,
                        help="Loop interval in minutes (0 = single cycle, the default)")
    parser.add_argument("--diagnose", action="store_true",
                        help="Preflight: SMTP, parser, snapshots, per-city health; then exit")
    parser.add_argument("--probe-snapshots", action="store_true",
                        help="Count current snapshot inputs by city, then exit")
    parser.add_argument("--health-report", action="store_true",
                        help="Per-city event-yield table, then exit")
    parser.add_argument("--subscribers", action="store_true",
                        help="List subscribers + per-city counts, then exit")
    parser.add_argument("--sponsors", action="store_true",
                        help="List sponsor pipeline + committed revenue, then exit")
    args = parser.parse_args()

    if args.diagnose:
        from towncrier.diagnose import main as diag_main
        sys.exit(diag_main())

    if args.probe_snapshots:
        from towncrier.health import probe_snapshots
        r = probe_snapshots()
        color = "green" if r.get("ok") else "red"
        body = f"[bold]Snapshot inventory[/bold]\n\n  Total files: {r['total']}\n"
        if r["by_city"]:
            body += "  By city:\n"
            for c, n in sorted(r["by_city"].items(), key=lambda kv: -kv[1]):
                body += f"    {c:<20s}  {n}\n"
        if not r.get("ok") and r.get("error"):
            body += f"\n  [red]{r['error']}[/red]"
        console.print(Panel(Text.from_markup(body.rstrip()), border_style=color))
        sys.exit(0 if r.get("ok") else 1)

    if args.health_report:
        from towncrier.health import report_lines, summary
        for line in report_lines():
            console.print(line)
        s = summary()
        if s["cities"]:
            console.print()
            console.print(
                f"  [white]{s['healthy']}[/white] healthy / "
                f"[yellow]{s['warning']}[/yellow] warning  "
                f"(threshold ≥{s['alert_threshold']} consecutive skips)  "
                f"all-time events: [white]{s['total_events_all_time']}[/white]  "
                f"sent: [white]{s['total_sent_all_time']}[/white]"
            )
        return

    if args.subscribers:
        from towncrier.subscribers import listing
        out = listing()
        body = (f"[bold]Subscribers[/bold]\n\n"
                f"  Total:    {out['total']}\n"
                f"  Active:   [green]{out['active']}[/green]\n"
                f"  Churned:  {out['churned']}\n")
        if out["by_city"]:
            body += "\n  Active by city:\n"
            for c, n in sorted(out["by_city"].items(), key=lambda kv: -kv[1]):
                body += f"    {c:<20s}  {n}\n"
        console.print(Panel(Text.from_markup(body.rstrip()), border_style="blue"))
        for s in out["subscribers"]:
            console.print(
                f"  [dim]{s.get('status','?'):>8s}[/dim]  "
                f"{s.get('city',''):<20s}  {s.get('email','')}"
            )
        return

    if args.sponsors:
        from towncrier.sponsors import listing
        out = listing()
        body = (f"[bold]Sponsor pipeline[/bold]\n\n"
                f"  Total:              {out['total']}\n"
                f"  Pending:            [yellow]{out['pending']}[/yellow]\n"
                f"  Paid:               [green]{out['paid']}[/green]\n"
                f"  Cancelled:          {out['cancelled']}\n"
                f"  Fulfilled:          {out['fulfilled']}\n"
                f"  Committed revenue:  [green]${out['committed_revenue']}[/green]\n"
                f"  Delivered revenue:  [green]${out['delivered_revenue']}[/green]\n"
                f"  Slots remaining:    {out['slots_remaining']}")
        console.print(Panel(Text.from_markup(body), border_style="blue"))
        for s in out["sponsors"]:
            extra = (f"  (sends_left={s.get('sends_remaining', 0)})"
                     if s.get("status") == "paid" else "")
            console.print(
                f"  [dim]{s.get('status','?'):>9s}[/dim]  "
                f"{s.get('plan',''):<12s}  {s.get('city',''):<18s}  "
                f"{s.get('name','')}{extra}"
            )
        return

    if not paywall_prompt("towncrier"):
        return
    while True:
        cycle()
        if args.interval <= 0:
            break
        time.sleep(args.interval * 60)


if __name__ == "__main__":
    main()
