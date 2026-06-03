#!/usr/bin/env python3
"""
HUDScout — autonomous government-foreclosed property scraper.

Usage:
  python3 run_hudscout_auto.py                       # one sweep cycle
  python3 run_hudscout_auto.py --diagnose            # preflight + per-state health + token probe
  python3 run_hudscout_auto.py --probe-session       # just the antiforgery handshake, no data scrape
  python3 run_hudscout_auto.py --health-report       # per-state run history table
  python3 run_hudscout_auto.py --subscribers         # list subscribers + MRR
"""
import argparse
import json
import sys
from datetime import datetime

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from hudscout.tools import run_full_cycle
from paywall.agent_paywall import paywall_prompt
from autonomous.self_healing import run_with_healing

console = Console()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--diagnose", action="store_true",
                        help="Preflight: SMTP, HUD session, per-state health, then exit")
    parser.add_argument("--probe-session", action="store_true",
                        help="Probe HUD antiforgery handshake only, then exit")
    parser.add_argument("--health-report", action="store_true",
                        help="Per-state grid health table, then exit")
    parser.add_argument("--subscribers", action="store_true",
                        help="List subscribers + MRR, then exit")
    args = parser.parse_args()

    if args.diagnose:
        from hudscout.diagnose import main as diag_main
        sys.exit(diag_main())
    if args.probe_session:
        from hudscout.health import probe_session
        r = probe_session()
        color = "green" if r.get("ok") else "red"
        console.print(Panel(
            Text.from_markup(
                f"[bold]HUD session probe[/bold]\n\n"
                f"  Status: [{color}]{'ok' if r.get('ok') else 'fail'}[/{color}]\n"
                + (f"  Token len: {r['token_len']}  Cookies: {r['cookies']}"
                   if r.get("ok") else f"  Error: {r.get('error','')}")
            ),
            border_style=color,
        ))
        sys.exit(0 if r.get("ok") else 1)
    if args.health_report:
        from hudscout.health import report_lines, summary
        for line in report_lines():
            console.print(line)
        s = summary()
        if s["states"]:
            console.print()
            console.print(
                f"  [white]{s['healthy']}[/white] healthy / "
                f"[yellow]{s['warning']}[/yellow] warning  "
                f"(threshold ≥{s['alert_threshold']} consecutive zeros)  "
                f"all-time found: [white]{s['total_found_all_time']}[/white]"
            )
        return
    if args.subscribers:
        from hudscout.subscription import listing
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
                f"{s.get('plan',''):<16s}  {s.get('email','')}"
            )
        return

    if not paywall_prompt("hudscout"):
        return
    console.print(Panel(
        Text.from_markup(
            f"[bold white]HUDScout Cycle[/bold white]\n"
            f"[dim]{datetime.now():%Y-%m-%d %H:%M:%S}[/dim]"
        ),
        title="[bold blue]Wholesale Omniverse — HUDScout[/bold blue]",
        border_style="blue",
    ))
    result = run_with_healing("hudscout", run_full_cycle)
    console.print(
        f"  States searched:    {result['states_searched']}\n"
        f"  Raw listings:       {result['raw_harvested']}\n"
        f"  New leads:          {result['new_leads']}\n"
        f"  Digest fulfilment:  {result['fulfillment_sent']}\n"
        f"  Active subs:        {result.get('active_subs', 0)}\n"
        f"  MRR:                ${result['mrr']:.0f}"
    )
    if result.get("digest_path"):
        console.print(f"  Digest written to:  {result['digest_path']}")


if __name__ == "__main__":
    main()
