#!/usr/bin/env python3
"""SpeedAudit autonomous loop — audit URLs, deliver to subs, pitch leads.

Usage:
  python3 run_speedaudit_auto.py                    # full cycle
  python3 run_speedaudit_auto.py --interval 1440    # daily
  python3 run_speedaudit_auto.py --diagnose         # preflight: SMTP + HTTP + queues + subscribers
  python3 run_speedaudit_auto.py --health-report    # aggregate score stats + subscriber freshness
  python3 run_speedaudit_auto.py --audit-now URL    # one-off ad-hoc audit, prints score + top fixes
  python3 run_speedaudit_auto.py --subscribers      # list subscribers + MRR
"""
import sys
import time, argparse
from datetime import datetime
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from speedaudit.tools import run_full_cycle
from paywall.agent_paywall import paywall_prompt
from autonomous.self_healing import with_healing

console = Console()


@with_healing("speedaudit")
def cycle():
    console.print(Panel(
        Text.from_markup(
            f"[bold white]SpeedAudit Cycle[/bold white]\n"
            f"[dim]{datetime.now():%Y-%m-%d %H:%M:%S}[/dim]"),
        title="[bold blue]Wholesale Omniverse — SpeedAudit[/bold blue]",
        border_style="blue"))
    r = run_full_cycle()
    console.print(f"  [cyan]Audits produced:[/cyan]   {r.get('audits_produced', 0)}")
    console.print(f"  [cyan]Lead previews:[/cyan]     {r.get('outreach_sent', 0)}")
    console.print(f"  [green]Monthly delivered:[/green]  {r.get('fulfillment_sent', 0)}")
    console.print(f"  [white]MRR:[/white]              ${r.get('mrr', 0):.0f}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=int, default=0)
    parser.add_argument("--diagnose", action="store_true",
                        help="Preflight: SMTP + HTTP + queues + recent yield, then exit")
    parser.add_argument("--health-report", action="store_true",
                        help="Aggregate score stats + subscriber freshness")
    parser.add_argument("--audit-now", metavar="URL",
                        help="Audit a single URL ad-hoc and print score + top 3 fixes")
    parser.add_argument("--subscribers", action="store_true",
                        help="List subscribers + MRR")
    args = parser.parse_args()

    if args.diagnose:
        from speedaudit.diagnose import main as diag_main
        sys.exit(diag_main())
    if args.health_report:
        from speedaudit.health import report_lines
        for line in report_lines():
            console.print(line)
        return
    if args.audit_now:
        from speedaudit.tools import audit_url
        result = audit_url(args.audit_now)
        if "error" in result:
            console.print(Panel(
                Text.from_markup(
                    f"[bold]Audit error[/bold]\n\n"
                    f"  URL:    {result.get('url', args.audit_now)}\n"
                    f"  Error:  [red]{result['error']}[/red]\n"
                    f"  Elapsed: {result.get('elapsed_s', 'n/a')}s"
                ),
                border_style="red",
            ))
            sys.exit(1)
        score = result.get("score", 0)
        color = "green" if score >= 75 else "yellow" if score >= 50 else "red"
        console.print(Panel(
            Text.from_markup(
                f"[bold]SpeedAudit — {result['final_url']}[/bold]\n\n"
                f"  Score:        [{color}]{score}/100[/{color}]\n"
                f"  TTFB+xfer:    {result['elapsed_s']}s\n"
                f"  HTML payload: {result['bytes']/1024:.0f} KB\n"
                f"  HTTPS:        {'yes' if result['final_url'].startswith('https') else 'no'}\n"
                f"  Redirected:   {result['redirected']}"
            ),
            border_style=color,
        ))
        fixes = result.get("fixes", [])[:3]
        if fixes:
            console.print("\n[bold]Top 3 fixes:[/bold]")
            for impact, msg in fixes:
                console.print(f"  [yellow](-{impact})[/yellow] {msg}")
        return
    if args.subscribers:
        from speedaudit.subscribers import listing
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
                f"{s.get('plan',''):<14s}  {s.get('email',''):<28s}  {s.get('site','')}"
            )
        return

    if not paywall_prompt("speedaudit"):
        return
    while True:
        cycle()
        if args.interval <= 0:
            break
        time.sleep(args.interval * 60)


if __name__ == "__main__":
    main()
