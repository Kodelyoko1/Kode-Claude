#!/usr/bin/env python3
"""
NicheLens — paid hyper-niche curation newsletters + affiliate injection.

Usage:
  python3 run_nichelens_auto.py                       # one cycle (all subscribed niches)
  python3 run_nichelens_auto.py --interval 60         # loop every N min
  python3 run_nichelens_auto.py --diagnose            # preflight: SMTP, inputs, dark niches, cadence
  python3 run_nichelens_auto.py --probe-snapshots     # snapshot inventory per niche, then exit
  python3 run_nichelens_auto.py --health-report       # per-niche yield history table
  python3 run_nichelens_auto.py --subscribers         # subscriber ledger + MRR
  python3 run_nichelens_auto.py --niches              # niches: configs + subscribers + snapshots side by side
"""
import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from nichelens.tools import run_full_cycle
from paywall.agent_paywall import paywall_prompt
from autonomous.self_healing import with_healing

console = Console()


@with_healing("nichelens")
def cycle():
    console.print(Panel(
        Text.from_markup(
            f"[bold white]NicheLens Cycle[/bold white]\n"
            f"[dim]{datetime.now():%Y-%m-%d %H:%M:%S}[/dim]"),
        title="[bold blue]Wholesale Omniverse — NicheLens[/bold blue]",
        border_style="blue"))
    r = run_full_cycle()
    console.print(f"  [green]Newsletters sent:[/green] {r.get('fulfillment_sent', 0)}")
    console.print(f"  [white]MRR:[/white]              ${r.get('mrr', 0):.0f}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=int, default=0,
                        help="Loop interval in minutes (0 = single cycle, the default)")
    parser.add_argument("--diagnose", action="store_true",
                        help="Preflight: SMTP, parser, snapshots, dark niches, cadence; then exit")
    parser.add_argument("--probe-snapshots", action="store_true",
                        help="Count snapshot files per niche subdir, then exit")
    parser.add_argument("--health-report", action="store_true",
                        help="Per-niche yield history table, then exit")
    parser.add_argument("--subscribers", action="store_true",
                        help="List subscribers + MRR + per-niche counts, then exit")
    parser.add_argument("--niches", action="store_true",
                        help="Show configs + subscribers + snapshots side by side, then exit")
    args = parser.parse_args()

    if args.diagnose:
        from nichelens.diagnose import main as diag_main
        sys.exit(diag_main())

    if args.probe_snapshots:
        from nichelens.health import probe_snapshots
        r = probe_snapshots()
        color = "green" if r.get("ok") else "red"
        body = (f"[bold]Snapshot inventory[/bold]\n\n"
                f"  Total files:  {r['total']}\n"
                f"  Newest age:   "
                + (f"{r['newest_age_days']}d" if r['newest_age_days'] is not None else "—") + "\n")
        if r["by_niche"]:
            body += "\n  By niche:\n"
            for n, c in sorted(r["by_niche"].items(), key=lambda kv: -kv[1]):
                body += f"    {n:<24s}  {c}\n"
        if r.get("error"):
            body += f"\n  [red]{r['error']}[/red]"
        console.print(Panel(Text.from_markup(body.rstrip()), border_style=color))
        sys.exit(0 if r.get("ok") else 1)

    if args.health_report:
        from nichelens.health import report_lines, summary
        for line in report_lines():
            console.print(line)
        s = summary()
        if s["niches"]:
            console.print()
            console.print(
                f"  [white]{s['healthy']}[/white] healthy / "
                f"[yellow]{s['warning']}[/yellow] warning  "
                f"(threshold ≥{s['alert_threshold']} consecutive skips)  "
                f"all-time items: [white]{s['total_items_all_time']}[/white]  "
                f"sent: [white]{s['total_sent_all_time']}[/white]"
            )
        return

    if args.subscribers:
        from nichelens.subscribers import listing
        out = listing()
        body = (f"[bold]Subscribers[/bold]\n\n"
                f"  Total:    {out['total']}\n"
                f"  Active:   [green]{out['active']}[/green] "
                f"(paid={out['active_paid']}, free={out['active_free']})\n"
                f"  Pending:  [yellow]{out['pending']}[/yellow]\n"
                f"  Churned:  {out['churned']}\n"
                f"  MRR:      [green]${out['mrr']:.0f}/mo[/green]")
        if out["by_niche"]:
            body += "\n\n  Active by niche:\n"
            for n, c in sorted(out["by_niche"].items(), key=lambda kv: -kv[1]):
                body += f"    {n:<24s}  {c}\n"
        console.print(Panel(Text.from_markup(body.rstrip()), border_style="blue"))
        for s in out["subscribers"]:
            console.print(
                f"  [dim]{s.get('status','?'):>8s}[/dim]  "
                f"{s.get('tier','?'):<4s}  {s.get('niche',''):<24s}  {s.get('email','')}"
            )
        return

    if args.niches:
        from nichelens.health import probe_snapshots
        from nichelens.subscribers import listing
        cfg = {}
        cfg_path = Path("data/nl_niche_configs.json")
        if cfg_path.exists():
            try: cfg = json.loads(cfg_path.read_text())
            except (OSError, json.JSONDecodeError): cfg = {}
        snaps = probe_snapshots().get("by_niche", {})
        subs  = listing().get("by_niche", {})
        all_niches = sorted(set(cfg) | set(snaps) | set(subs))
        if not all_niches:
            console.print("(no niches anywhere — no configs, snapshots, or subscribers)")
            return
        console.print(f"{'NICHE':<24s}  {'CONFIG':>6s}  {'SNAPS':>5s}  {'SUBS':>5s}")
        for n in all_niches:
            console.print(
                f"{n:<24s}  "
                f"{'yes' if n in cfg else '—':>6s}  "
                f"{snaps.get(n, 0):>5d}  "
                f"{subs.get(n, 0):>5d}"
            )
        return

    if not paywall_prompt("nichelens"):
        return
    while True:
        cycle()
        if args.interval <= 0:
            break
        time.sleep(args.interval * 60)


if __name__ == "__main__":
    main()
