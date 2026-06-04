#!/usr/bin/env python3
"""
TrendScout — paid weekly digital-product-niche newsletter.

Usage:
  python3 run_trendscout_auto.py                       # one cycle (teaser + report)
  python3 run_trendscout_auto.py --interval 60         # loop every N min
  python3 run_trendscout_auto.py --diagnose            # preflight: SMTP, inputs, yield, cadence
  python3 run_trendscout_auto.py --probe-inputs        # input inventory by suffix, then exit
  python3 run_trendscout_auto.py --health-report       # per-week yield history table
  python3 run_trendscout_auto.py --subscribers         # subscriber ledger + MRR
  python3 run_trendscout_auto.py --scan-signals        # preview top niches without sending
"""
import argparse
import sys
import time
from datetime import datetime

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from trendscout.tools import run_full_cycle
from paywall.agent_paywall import paywall_prompt
from autonomous.self_healing import with_healing

console = Console()


@with_healing("trendscout")
def cycle():
    console.print(Panel(
        Text.from_markup(
            f"[bold white]TrendScout Cycle[/bold white]\n"
            f"[dim]{datetime.now():%Y-%m-%d %H:%M:%S}[/dim]"),
        title="[bold blue]Wholesale Omniverse — TrendScout[/bold blue]",
        border_style="blue"))
    r = run_full_cycle()
    console.print(f"  [cyan]Teasers sent:[/cyan]      {r.get('outreach_sent', 0)}")
    console.print(f"  [green]Reports delivered:[/green] {r.get('fulfillment_sent', 0)}")
    console.print(f"  [white]MRR:[/white]                ${r.get('mrr', 0):.0f}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=int, default=0,
                        help="Loop interval in minutes (0 = single cycle, the default)")
    parser.add_argument("--diagnose", action="store_true",
                        help="Preflight: SMTP, inputs, yield, cadence; then exit")
    parser.add_argument("--probe-inputs", action="store_true",
                        help="Count ts_inputs/ files by suffix + newest age, then exit")
    parser.add_argument("--health-report", action="store_true",
                        help="Per-week yield history table, then exit")
    parser.add_argument("--subscribers", action="store_true",
                        help="List subscribers + MRR, then exit")
    parser.add_argument("--scan-signals", action="store_true",
                        help="Preview the top niches scan_signals() would emit, no outreach")
    args = parser.parse_args()

    if args.diagnose:
        from trendscout.diagnose import main as diag_main
        sys.exit(diag_main())

    if args.probe_inputs:
        from trendscout.health import probe_inputs
        r = probe_inputs()
        color = "green" if r.get("ok") else "red"
        body = (f"[bold]Input inventory[/bold]\n\n"
                f"  Accepted files: {r['accepted']}\n"
                f"  Total files:    {r['total']}\n"
                f"  Newest age:     "
                + (f"{r['newest_age_days']}d" if r['newest_age_days'] is not None else "—") + "\n")
        if r["by_suffix"]:
            body += "\n  By suffix:\n"
            for suf, n in sorted(r["by_suffix"].items(), key=lambda kv: -kv[1]):
                body += f"    {suf:<10s}  {n}\n"
        if r.get("error"):
            body += f"\n  [red]{r['error']}[/red]"
        console.print(Panel(Text.from_markup(body.rstrip()), border_style=color))
        sys.exit(0 if r.get("ok") else 1)

    if args.health_report:
        from trendscout.health import report_lines, summary
        for line in report_lines():
            console.print(line)
        s = summary()
        if s["weeks"]:
            console.print()
            console.print(
                f"  [white]{s['delivered']}[/white] delivered / "
                f"[yellow]{s['skipped']}[/yellow] skipped  "
                f"streak: -[yellow]{s['consecutive_skips']}[/yellow] "
                f"(threshold {s['alert_threshold']})  "
                f"last delivered: [white]{s['last_delivered'] or '—'}[/white]  "
                f"all-time sent: [white]{s['total_sent']}[/white]"
            )
        return

    if args.subscribers:
        from trendscout.subscribers import listing
        out = listing()
        console.print(Panel(
            Text.from_markup(
                f"[bold]Subscribers[/bold]\n\n"
                f"  Total:    {out['total']}\n"
                f"  Active:   [green]{out['active']}[/green]\n"
                f"  Pending:  [yellow]{out['pending']}[/yellow]\n"
                f"  Churned:  {out['churned']}\n"
                f"  MRR:      [green]${out['mrr']:.0f}/mo[/green]"
            ),
            border_style="blue",
        ))
        for s in out["subscribers"]:
            console.print(
                f"  [dim]{s.get('status','?'):>8s}[/dim]  "
                f"{s.get('plan',''):<18s}  {s.get('email','')}"
            )
        return

    if args.scan_signals:
        from trendscout.tools import scan_signals, score_niches
        signals = scan_signals()
        top = score_niches(signals, top_n=10)
        body = (f"[bold]Signal preview[/bold]\n\n"
                f"  Raw signals: {sum(signals.values())}\n"
                f"  Distinct candidates: {len(signals)}\n"
                f"  Scored niches (top 10):\n")
        if not top:
            body += "    (none — insufficient input or all candidates blocked)"
        else:
            for i, n in enumerate(top, 1):
                body += (f"    {i:>2d}. {n['niche']:<32s}  "
                         f"score={n['score']:>5.1f}  raw={n['raw_count']}\n")
        console.print(Panel(Text.from_markup(body.rstrip()), border_style="blue"))
        return

    if not paywall_prompt("trendscout"):
        return
    while True:
        cycle()
        if args.interval <= 0:
            break
        time.sleep(args.interval * 60)


if __name__ == "__main__":
    main()
