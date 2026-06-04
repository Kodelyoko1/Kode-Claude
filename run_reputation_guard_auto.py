#!/usr/bin/env python3
"""
ReputationGuard — autonomous review-management agent.

Usage:
  python3 run_reputation_guard_auto.py                       # one cycle
  python3 run_reputation_guard_auto.py --interval 60         # loop every N min
  python3 run_reputation_guard_auto.py --diagnose            # preflight
  python3 run_reputation_guard_auto.py --probe-snapshots     # snapshot inventory by business, then exit
  python3 run_reputation_guard_auto.py --health-report       # per-business yield table
  python3 run_reputation_guard_auto.py --clients             # client ledger + MRR + one-time
  python3 run_reputation_guard_auto.py --prospects           # prospect pipeline state, then exit
"""
import argparse
import sys
import time
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
    parser.add_argument("--interval", type=int, default=0,
                        help="Loop interval in minutes (0 = single cycle)")
    parser.add_argument("--diagnose", action="store_true",
                        help="Preflight: SMTP, parser, snapshots, coverage; then exit")
    parser.add_argument("--probe-snapshots", action="store_true",
                        help="Count snapshot files per business + newest age, then exit")
    parser.add_argument("--health-report", action="store_true",
                        help="Per-business yield history table, then exit")
    parser.add_argument("--clients", action="store_true",
                        help="List clients + MRR + one-time collected, then exit")
    parser.add_argument("--prospects", action="store_true",
                        help="Show prospect pipeline state, then exit")
    args = parser.parse_args()

    if args.diagnose:
        from reputation_guard.diagnose import main as diag_main
        sys.exit(diag_main())

    if args.probe_snapshots:
        from reputation_guard.health import probe_snapshots
        r = probe_snapshots()
        color = "green" if r.get("ok") else "red"
        body = (f"[bold]Snapshot inventory[/bold]\n\n"
                f"  Total files: {r['total']}\n"
                f"  Newest age:  "
                + (f"{r['newest_age_days']}d" if r['newest_age_days'] is not None else "—") + "\n")
        if r["by_business"]:
            body += "\n  By business:\n"
            for slug, meta in sorted(r["by_business"].items(),
                                      key=lambda kv: kv[1].get("age_days", 0)):
                body += f"    {slug:<28s}  {meta.get('age_days', 0)}d old\n"
        if r.get("error"):
            body += f"\n  [red]{r['error']}[/red]"
        console.print(Panel(Text.from_markup(body.rstrip()), border_style=color))
        sys.exit(0 if r.get("ok") else 1)

    if args.health_report:
        from reputation_guard.health import report_lines, summary
        for line in report_lines():
            console.print(line)
        s = summary()
        if s["businesses"]:
            console.print()
            console.print(
                f"  [white]{s['healthy']}[/white] healthy / "
                f"[yellow]{s['warning']}[/yellow] warning  "
                f"(threshold ≥{s['alert_threshold']} consecutive skips)  "
                f"all-time negatives: [white]{s['total_negatives_all_time']}[/white]  "
                f"drafts: [white]{s['total_drafts_all_time']}[/white]"
            )
        return

    if args.clients:
        from reputation_guard.clients import listing
        out = listing()
        body = (f"[bold]Clients[/bold]\n\n"
                f"  Total:               {out['total']}\n"
                f"  Active:              [green]{out['active']}[/green]\n"
                f"  Pending:             [yellow]{out['pending']}[/yellow]\n"
                f"  Fulfilled:           {out['fulfilled']}\n"
                f"  Churned:             {out['churned']}\n"
                f"  MRR:                 [green]${out['mrr']:.0f}/mo[/green]\n"
                f"  One-time collected:  [green]${out['one_time_collected']}[/green]")
        console.print(Panel(Text.from_markup(body), border_style="blue"))
        for c in out["clients"]:
            console.print(
                f"  [dim]{c.get('status','?'):>9s}[/dim]  "
                f"{c.get('plan',''):<16s}  "
                f"{c.get('business_slug',''):<28s}  {c.get('contact_email','')}"
            )
        return

    if args.prospects:
        import json
        from pathlib import Path
        pros_path = Path("data/rg_prospects.json")
        if not pros_path.exists():
            console.print("(no prospects — scan_prospects() hasn't found any negative-heavy businesses yet)")
            return
        try:
            pros = json.loads(pros_path.read_text())
        except (OSError, json.JSONDecodeError):
            console.print("[red]rg_prospects.json unreadable[/red]")
            return
        if not isinstance(pros, list) or not pros:
            console.print("(rg_prospects.json empty)")
            return
        queued = sum(1 for p in pros if p.get("status") == "queued")
        contacted = sum(1 for p in pros if p.get("status") == "contacted")
        awaiting_email = sum(1 for p in pros
                             if p.get("status") == "queued" and not p.get("contact_email"))
        console.print(Panel(Text.from_markup(
            f"[bold]Prospect pipeline[/bold]\n\n"
            f"  Total:                  {len(pros)}\n"
            f"  Queued:                 [yellow]{queued}[/yellow]\n"
            f"  Contacted:              [green]{contacted}[/green]\n"
            f"  Awaiting contact_email: [yellow]{awaiting_email}[/yellow]"
        ), border_style="blue"))
        for p in pros[:20]:
            console.print(
                f"  [dim]{p.get('status','?'):>10s}[/dim]  "
                f"neg={p.get('negative_count', 0):>2d}  "
                f"{p.get('business_slug', ''):<32s}  "
                + (p.get("contact_email") or "[red](no contact_email)[/red]")
            )
        if len(pros) > 20:
            console.print(f"  ... +{len(pros) - 20} more")
        return

    if not paywall_prompt("reputation_guard"):
        return
    while True:
        cycle()
        if args.interval <= 0:
            break
        time.sleep(args.interval * 60)


if __name__ == "__main__":
    main()
