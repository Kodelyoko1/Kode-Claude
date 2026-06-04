#!/usr/bin/env python3
"""
PaperBrief — vertical research summarization newsletter.

Usage:
  python3 run_paperbrief_auto.py                       # one cycle (build briefs + send digests)
  python3 run_paperbrief_auto.py --interval 60         # loop every N min
  python3 run_paperbrief_auto.py --diagnose            # preflight: SMTP, pypdf, queue, builds
  python3 run_paperbrief_auto.py --probe-inputs        # queue + PDFs + briefs triangulation
  python3 run_paperbrief_auto.py --health-report       # per-vertical yield history
  python3 run_paperbrief_auto.py --builds 50           # last 50 per-paper build outcomes
  python3 run_paperbrief_auto.py --subscribers         # subscriber ledger + MRR
  python3 run_paperbrief_auto.py --queue               # queue state by vertical + delivered/pending
"""
import argparse
import sys
import time
from datetime import datetime

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from paperbrief.tools import run_full_cycle
from paywall.agent_paywall import paywall_prompt
from autonomous.self_healing import with_healing

console = Console()


@with_healing("paperbrief")
def cycle():
    console.print(Panel(
        Text.from_markup(
            f"[bold white]PaperBrief Cycle[/bold white]\n"
            f"[dim]{datetime.now():%Y-%m-%d %H:%M:%S}[/dim]"),
        title="[bold blue]Wholesale Omniverse — PaperBrief[/bold blue]",
        border_style="blue"))
    r = run_full_cycle()
    console.print(f"  [cyan]Briefs built:[/cyan]       {r.get('briefs_built', 0)}")
    console.print(f"  [cyan]Free samples sent:[/cyan]  {r.get('outreach_sent', 0)}")
    console.print(f"  [green]Digests delivered:[/green]  {r.get('fulfillment_sent', 0)}")
    console.print(f"  [white]MRR:[/white]                ${r.get('mrr', 0):.0f}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=int, default=0,
                        help="Loop interval in minutes (0 = single cycle, the default)")
    parser.add_argument("--diagnose", action="store_true",
                        help="Preflight: SMTP, pypdf, queue, build outcomes; then exit")
    parser.add_argument("--probe-inputs", action="store_true",
                        help="Queue + PDFs + briefs triangulation, then exit")
    parser.add_argument("--health-report", action="store_true",
                        help="Per-vertical yield history table, then exit")
    parser.add_argument("--builds", type=int, default=0,
                        help="Show last N per-paper build outcomes, then exit")
    parser.add_argument("--subscribers", action="store_true",
                        help="List subscribers + MRR, then exit")
    parser.add_argument("--queue", action="store_true",
                        help="Show queue state by vertical + delivered/pending, then exit")
    args = parser.parse_args()

    if args.diagnose:
        from paperbrief.diagnose import main as diag_main
        sys.exit(diag_main())

    if args.probe_inputs:
        from paperbrief.health import probe_inputs
        r = probe_inputs()
        color = "green" if r.get("ok") else "red"
        body = (f"[bold]Inputs[/bold]\n\n"
                f"  Queue total:        {r['queue_total']}\n"
                f"  Queue undelivered:  {r['queue_undelivered']}\n"
                f"  PDFs on disk:       {r['pdfs_total']}\n"
                f"  PDFs newest age:    "
                + (f"{r['pdfs_newest_age_days']}d" if r['pdfs_newest_age_days'] is not None else "—") + "\n"
                f"  Briefs built:       {r['briefs_total']}\n"
                f"  min_digest_briefs:  {r['min_digest_briefs']}\n")
        if r["queue_by_vertical"]:
            body += "\n  Undelivered by vertical:\n"
            for v, n in sorted(r["queue_by_vertical"].items(), key=lambda kv: -kv[1]):
                body += f"    {v:<20s}  {n}\n"
        if r["missing_pdfs"]:
            body += f"\n  [red]Missing PDFs ({len(r['missing_pdfs'])}):[/red] "
            body += ", ".join(r["missing_pdfs"][:6])
            if len(r["missing_pdfs"]) > 6:
                body += f" +{len(r['missing_pdfs']) - 6}"
            body += "\n"
        if r["orphan_pdfs"]:
            body += f"\n  [dim]Orphan PDFs ({len(r['orphan_pdfs'])}):[/dim] "
            body += ", ".join(r["orphan_pdfs"][:6])
            if len(r["orphan_pdfs"]) > 6:
                body += f" +{len(r['orphan_pdfs']) - 6}"
            body += "\n"
        console.print(Panel(Text.from_markup(body.rstrip()), border_style=color))
        sys.exit(0 if r.get("ok") else 1)

    if args.health_report:
        from paperbrief.health import report_lines, vertical_summary, build_outcome_summary
        for line in report_lines():
            console.print(line)
        s = vertical_summary()
        if s["verticals"]:
            console.print()
            console.print(
                f"  [white]{s['healthy']}[/white] healthy / "
                f"[yellow]{s['warning']}[/yellow] warning  "
                f"(threshold ≥{s['alert_threshold']} consecutive skips)  "
                f"all-time sent: [white]{s['total_sent_all_time']}[/white]"
            )
        bs = build_outcome_summary()
        if bs["total"]:
            console.print(
                f"  builds log: total={bs['total']}  success={bs['success']}  "
                f"missing_pdf={bs['missing_pdf']}  extract_failed={bs['extract_failed']}"
            )
            if bs["repeated_failures"]:
                console.print(
                    "  [yellow]repeated_failures:[/yellow] " +
                    ", ".join(f"{r['paper_id']}(-{r['streak']})" for r in bs["repeated_failures"][:5])
                )
        return

    if args.builds:
        from paperbrief.health import recent_builds, build_outcome_summary
        for r in recent_builds(args.builds):
            color = "green" if r["outcome"] == "success" else "red"
            console.print(
                f"  [dim]{r['ts'][:19]}[/dim]  [{color}]{r['outcome']:<14s}[/{color}]  "
                f"{r['paper_id']}"
                + (f"  [dim]{r['detail'][:60]}[/dim]" if r.get("detail") else "")
            )
        s = build_outcome_summary()
        console.print(
            f"\n  log_total={s['total']}  "
            f"[green]success={s['success']}[/green]  "
            f"[red]missing_pdf={s['missing_pdf']}[/red]  "
            f"[red]extract_failed={s['extract_failed']}[/red]"
        )
        return

    if args.subscribers:
        from paperbrief.subscribers import listing
        out = listing()
        body = (f"[bold]Subscribers[/bold]\n\n"
                f"  Total:    {out['total']}\n"
                f"  Active:   [green]{out['active']}[/green]\n"
                f"  Pending:  [yellow]{out['pending']}[/yellow]\n"
                f"  Churned:  {out['churned']}\n"
                f"  MRR:      [green]${out['mrr']:.0f}/mo[/green]")
        if out["by_vertical"]:
            body += "\n\n  Active by vertical:\n"
            for v, n in sorted(out["by_vertical"].items(), key=lambda kv: -kv[1]):
                body += f"    {v:<20s}  {n}\n"
        console.print(Panel(Text.from_markup(body.rstrip()), border_style="blue"))
        for s in out["subscribers"]:
            console.print(
                f"  [dim]{s.get('status','?'):>8s}[/dim]  "
                f"{s.get('plan',''):<16s}  {s.get('vertical',''):<20s}  {s.get('email','')}"
            )
        return

    if args.queue:
        from paperbrief.health import probe_inputs
        r = probe_inputs()
        if r["queue_total"] == 0:
            console.print("(queue empty)")
            return
        console.print(
            f"queue_total={r['queue_total']}  undelivered={r['queue_undelivered']}  "
            f"delivered={r['queue_total'] - r['queue_undelivered']}\n"
        )
        if r["queue_by_vertical"]:
            console.print(f"{'VERTICAL':<20s}  {'UNDELIVERED':>11s}")
            for v, n in sorted(r["queue_by_vertical"].items(), key=lambda kv: -kv[1]):
                gate = "✓" if n >= r["min_digest_briefs"] else "·"
                console.print(f"{v:<20s}  {n:>11d}  {gate} ({n}/{r['min_digest_briefs']})")
        if r["missing_pdfs"]:
            console.print(
                f"\n  [red]Missing PDFs ({len(r['missing_pdfs'])}):[/red] " +
                ", ".join(r["missing_pdfs"][:6])
                + (f" +{len(r['missing_pdfs']) - 6}" if len(r["missing_pdfs"]) > 6 else "")
            )
        return

    if not paywall_prompt("paperbrief"):
        return
    while True:
        cycle()
        if args.interval <= 0:
            break
        time.sleep(args.interval * 60)


if __name__ == "__main__":
    main()
