#!/usr/bin/env python3
"""PropScout autonomous loop — free PropStream-style prospect engine.

Usage:
  python3 run_propscout_auto.py                   # one cycle
  python3 run_propscout_auto.py --interval 60     # every 60 minutes
  python3 run_propscout_auto.py --diagnose        # preflight + grid health + attribution
  python3 run_propscout_auto.py --health-report   # per-cell health table
  python3 run_propscout_auto.py --backfill        # tag existing motivation-eligible leads
"""
import argparse
import sys
import time
from datetime import datetime

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from propscout.tools import run_full_cycle
from paywall.agent_paywall import paywall_prompt
from autonomous.self_healing import with_healing

console = Console()


@with_healing("propscout")
def cycle():
    console.print(Panel(
        Text.from_markup(
            f"[bold white]PropScout Cycle[/bold white]\n"
            f"[dim]{datetime.now():%Y-%m-%d %H:%M:%S}[/dim]"),
        title="[bold blue]Wholesale Omniverse — PropScout[/bold blue]",
        border_style="blue"))
    r = run_full_cycle()
    console.print(f"  [cyan]Cells scanned:[/cyan]            {r.get('cells_run', 0)}")
    console.print(f"  [cyan]Prospects found:[/cyan]          {r.get('prospects_found', 0)}")
    console.print(f"    [dim]with email:[/dim]               {r.get('with_email', 0)}")
    console.print(f"    [dim]with phone:[/dim]               {r.get('with_phone', 0)}")
    console.print(f"  [green]Cold-email drafts written:[/green] {r.get('drafts_written', 0)}")
    console.print(f"  [green]Outreach auto-sent:[/green]        {r.get('outreach_sent', 0)}")
    console.print(f"  [white]Owner digest sent:[/white]         {r.get('digest_sent', 0)}")
    console.print(f"  [white]MRR:[/white]                       ${r.get('mrr', 0):.0f}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=int, default=0,
                        help="Loop every N minutes (0 = one-shot)")
    parser.add_argument("--diagnose", action="store_true",
                        help="Preflight + grid health + pipeline attribution, then exit")
    parser.add_argument("--health-report", action="store_true",
                        help="Per-cell grid-health table, then exit")
    parser.add_argument("--backfill", action="store_true",
                        help="Tag existing motivation-eligible leads as PropScout-attributed, then exit")
    args = parser.parse_args()

    if args.diagnose:
        from propscout.diagnose import main as diag_main
        sys.exit(diag_main())
    if args.health_report:
        from propscout.health import report_lines, summary as health_summary
        for line in report_lines():
            console.print(line)
        s = health_summary()
        if s["cells"]:
            console.print()
            console.print(
                f"  [white]{s['healthy']}[/white] healthy / "
                f"[yellow]{s['warning']}[/yellow] warning  "
                f"(threshold ≥{s['alert_threshold']} consecutive zeros)  "
                f"all-time found: [white]{s['total_found_all_time']}[/white]"
            )
        return
    if args.backfill:
        from propscout.attribution import backfill
        out = backfill()
        console.print(Panel(
            Text.from_markup(
                f"[bold]Attribution backfill[/bold]\n\n"
                f"  Tagged: [green]{out.get('tagged', 0)}[/green] lead(s)\n"
                f"  By motivation:  "
                + "  ".join(f"{k}={v}" for k, v in out.get("by_motivation", {}).items())
            ),
            border_style="green",
        ))
        return

    if not paywall_prompt("propscout"):
        return
    while True:
        cycle()
        if args.interval <= 0:
            break
        time.sleep(args.interval * 60)


if __name__ == "__main__":
    main()
