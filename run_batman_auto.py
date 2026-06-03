#!/usr/bin/env python3
"""
Batman — autonomous self-healing agent fleet manager.

Usage:
  python3 run_batman_auto.py                              # dry-run cycle (reports only)
  BATMAN_LIVE=1 python3 run_batman_auto.py                # auto-repair mode
  python3 run_batman_auto.py --diagnose                   # self-preflight (Batman's own config)
  python3 run_batman_auto.py --snapshot                   # fleet check, no report/email side-effects
  python3 run_batman_auto.py --coverage-report            # which data files have auto-restore schemas
  python3 run_batman_auto.py --subscribers                # list subscribers + MRR
"""
import argparse
import sys
from datetime import datetime

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from batman.tools import run_full_cycle
from paywall.agent_paywall import paywall_prompt
from autonomous.self_healing import run_with_healing

console = Console()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--diagnose", action="store_true",
                        help="Self-preflight: Batman's own SMTP, dirs, schema coverage, last report — then exit")
    parser.add_argument("--snapshot", action="store_true",
                        help="Fleet check (logs + JSON integrity + stale) with NO report write + NO email")
    parser.add_argument("--coverage-report", action="store_true",
                        help="Show which data/*.json files are covered by DEFAULT_SCHEMAS")
    parser.add_argument("--subscribers", action="store_true",
                        help="List subscribers + MRR, then exit")
    args = parser.parse_args()

    if args.diagnose:
        from batman.diagnose import main as diag_main
        sys.exit(diag_main())
    if args.snapshot:
        from batman.tools import scan_run_logs, verify_json_integrity, find_stale_agents
        scan = scan_run_logs()
        integ = verify_json_integrity()
        stale = find_stale_agents()
        findings = (len(scan["failures"]) + len(scan["tracebacks"])
                    + len(integ["corrupted"]) + len(stale["stale_agents"]))
        color = "red" if findings else "green"
        console.print(Panel(
            Text.from_markup(
                f"[bold]Fleet snapshot[/bold]  (no report written, no email sent)\n\n"
                f"  Logs scanned:        {scan['logs_scanned']}\n"
                f"  Failure lines:       [yellow]{len(scan['failures'])}[/yellow]\n"
                f"  Tracebacks:          [yellow]{len(scan['tracebacks'])}[/yellow]\n"
                f"  JSON files checked:  {integ['json_checked']}\n"
                f"  Corrupted (>guard):  [red]{len(integ['corrupted'])}[/red]\n"
                f"  Skipped (live-write): {len(integ['skipped_live'])}\n"
                f"  Stale agents:        [yellow]{len(stale['stale_agents'])}[/yellow]\n\n"
                f"  Total findings:      [{color}]{findings}[/{color}]"
            ),
            border_style=color,
        ))
        for f in scan["failures"][:5]:
            console.print(f"  [yellow]✗[/yellow] [{f['log']}] {f['agent']}: {f['reason']}")
        for c in integ["corrupted"][:5]:
            console.print(f"  [red]✗[/red] {c['file']} — {c['error'][:80]}")
        for a in stale["stale_agents"][:5]:
            console.print(
                f"  [yellow]⏱[/yellow] {a['agent']} ({a['cadence']}) — "
                f"{a['hours_since']}h stale (threshold {a['threshold']}h)"
            )
        return
    if args.coverage_report:
        from batman.tools import DEFAULT_SCHEMAS, DATA_DIR
        all_json = sorted(p.name for p in DATA_DIR.glob("*.json"))
        covered = set(DEFAULT_SCHEMAS.keys())
        cov   = [f for f in all_json if f in covered]
        uncov = [f for f in all_json if f not in covered]
        console.print(Panel(
            Text.from_markup(
                f"[bold]Schema coverage[/bold]\n\n"
                f"  Total data/*.json files:  {len(all_json)}\n"
                f"  Covered (auto-restore):   [green]{len(cov)}[/green]\n"
                f"  Uncovered (quarantine but no auto-restore):  [yellow]{len(uncov)}[/yellow]"
            ),
            border_style="blue",
        ))
        console.print("\n[bold]Covered (auto-restorable on corruption):[/bold]")
        for f in cov:
            console.print(f"  [green]✓[/green] {f}")
        console.print("\n[bold]Uncovered (owner must hand-restore on corruption):[/bold]")
        for f in uncov:
            console.print(f"  [yellow]·[/yellow] {f}")
        return
    if args.subscribers:
        from batman.subscription import listing
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
            border_style="red",
        ))
        for s in out["subscribers"]:
            console.print(
                f"  [dim]{s.get('status','?'):>8s}[/dim]  "
                f"{s.get('plan',''):<16s}  {s.get('email','')}"
            )
        return

    if not paywall_prompt("batman"):
        return
    console.print(Panel(
        Text.from_markup(
            f"[bold white]Batman Cycle[/bold white]\n"
            f"[dim]{datetime.now():%Y-%m-%d %H:%M:%S}[/dim]"
        ),
        title="[bold red]Wholesale Omniverse — Batman[/bold red]",
        border_style="red",
    ))
    result = run_with_healing("batman", run_full_cycle)
    console.print(
        f"  Mode:                {result['mode']}\n"
        f"  Run logs scanned:    {result['logs_scanned']}\n"
        f"  Failure lines:       {result['failures']}\n"
        f"  Tracebacks:          {result['tracebacks']}\n"
        f"  JSON files checked:  {result['json_checked']}\n"
        f"  Corrupted files:     {result['corrupted']}\n"
        f"  Quarantined:         {result['quarantined']}\n"
        f"  Restored to default: {result['restored']}\n"
        f"  Stale agents:        {result['stale_agents']}\n"
        f"  Owner email sent:    {result['report_sent']}\n"
        f"  Report:              {result['report_path']}"
    )


if __name__ == "__main__":
    main()
