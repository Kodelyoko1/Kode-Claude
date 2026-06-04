#!/usr/bin/env python3
"""
CareerForge — autonomous resume tailoring (free ATS score → paid
tailored deliverable). Usage-based pricing per CLAUDE.md:
  $29/tailoring  ·  $49/mo unlimited (~20/mo)  ·  $147 career package

Usage:
  python3 run_careerforge_auto.py                       # one cycle
  python3 run_careerforge_auto.py --interval 60         # loop every N min
  python3 run_careerforge_auto.py --diagnose            # preflight: SMTP, inputs, outcomes
  python3 run_careerforge_auto.py --probe-inputs        # profiles + orders + jobs + leads
  python3 run_careerforge_auto.py --orders 50           # last 50 per-order outcomes
  python3 run_careerforge_auto.py --scores              # ATS score distribution
  python3 run_careerforge_auto.py --usage               # per-user monthly usage vs cap
  python3 run_careerforge_auto.py --clients             # client ledger + MRR + one-time
"""
import argparse
import sys
import time
from datetime import datetime

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from careerforge.tools import run_full_cycle
from paywall.agent_paywall import paywall_prompt
from autonomous.self_healing import with_healing

console = Console()


@with_healing("careerforge")
def cycle():
    console.print(Panel(
        Text.from_markup(
            f"[bold white]CareerForge Cycle[/bold white]\n"
            f"[dim]{datetime.now():%Y-%m-%d %H:%M:%S}[/dim]"),
        title="[bold blue]Wholesale Omniverse — CareerForge[/bold blue]",
        border_style="blue"))
    r = run_full_cycle()
    console.print(f"  [cyan]Free scores sent:[/cyan] {r.get('outreach_sent', 0)}")
    console.print(f"  [green]Resumes shipped:[/green]  {r.get('fulfillment_sent', 0)}")
    console.print(f"  [white]Total revenue:[/white]    ${r.get('total_paid', 0):.0f}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=int, default=0,
                        help="Loop interval in minutes (0 = single cycle)")
    parser.add_argument("--diagnose", action="store_true",
                        help="Preflight: SMTP, inputs, outcomes, usage; then exit")
    parser.add_argument("--probe-inputs", action="store_true",
                        help="Triangulate profiles + orders + jobs + leads, then exit")
    parser.add_argument("--orders", type=int, default=0,
                        help="Show last N per-order outcomes, then exit")
    parser.add_argument("--scores", action="store_true",
                        help="ATS score distribution from delivered orders, then exit")
    parser.add_argument("--usage", action="store_true",
                        help="Per-user usage in the current month, then exit")
    parser.add_argument("--clients", action="store_true",
                        help="List clients + MRR + one-time + by-plan, then exit")
    args = parser.parse_args()

    if args.diagnose:
        from careerforge.diagnose import main as diag_main
        sys.exit(diag_main())

    if args.probe_inputs:
        from careerforge.health import probe_inputs
        r = probe_inputs()
        color = "green" if r.get("ok") else "red"
        body = (f"[bold]Inputs[/bold]\n\n"
                f"  Profiles:                {r['profiles']}\n"
                f"  Jobs files:              {r['jobs_files']}\n"
                f"  Orders total:            {r['orders_total']}\n"
                f"  Orders paid_pending:     [yellow]{r['orders_paid_pending']}[/yellow]\n"
                f"  Orders delivered:        [green]{r['orders_delivered']}[/green]\n"
                f"  Leads total:             {r['leads_total']}\n"
                f"  Leads ready (full data): {r['leads_ready']}\n"
                f"  Monthly cap (CF_MONTHLY_CAP): {r['monthly_cap']}")
        if r["orders_missing_profile"]:
            body += (f"\n\n  [red]Paid orders missing profile ({len(r['orders_missing_profile'])}):[/red] "
                     + ", ".join(r["orders_missing_profile"][:6]))
            if len(r["orders_missing_profile"]) > 6:
                body += f" +{len(r['orders_missing_profile']) - 6}"
        console.print(Panel(Text.from_markup(body), border_style=color))
        sys.exit(0 if r.get("ok") else 1)

    if args.orders:
        from careerforge.health import recent_orders, order_outcome_summary
        for r in recent_orders(args.orders):
            color = "green" if r["outcome"] == "success" else "red"
            console.print(
                f"  [dim]{r['ts'][:19]}[/dim]  [{color}]{r['outcome']:<11s}[/{color}]  "
                f"{r['user_id']:<20s}  {(r.get('detail') or '')[:60]}"
            )
        s = order_outcome_summary()
        console.print(
            f"\n  log_total={s['total']}  "
            f"[green]success={s['success']}[/green]  "
            f"[red]no_profile={s['no_profile']}[/red]  "
            f"[red]no_jd={s['no_jd']}[/red]  "
            f"[red]no_email={s['no_email']}[/red]"
        )
        return

    if args.scores:
        from careerforge.health import score_summary
        s = score_summary()
        body = (f"[bold]ATS scores[/bold]\n\n"
                f"  Total logged: {s['total']}\n"
                f"  Average:      {s['avg']}\n\n"
                f"  Distribution:\n")
        for bucket, n in s["dist"].items():
            body += f"    {bucket:<8s}  {n}\n"
        console.print(Panel(Text.from_markup(body.rstrip()), border_style="blue"))
        return

    if args.usage:
        from careerforge.health import (
            monthly_usage_per_user, MONTHLY_CAP, OVER_CAP_WARN,
        )
        usage = monthly_usage_per_user()
        if not usage:
            console.print("(no usage recorded this month)")
            return
        console.print(f"{'USER':<24s}  {'COUNT':>5s} / {MONTHLY_CAP}  STATUS")
        for u, n in sorted(usage.items(), key=lambda kv: -kv[1]):
            if n > MONTHLY_CAP:
                gate = "[red]OVER[/red]"
            elif n >= OVER_CAP_WARN:
                gate = "[yellow]warn[/yellow]"
            else:
                gate = "[green]ok[/green]"
            console.print(f"{u:<24s}  {n:>5d} / {MONTHLY_CAP}  {gate}")
        return

    if args.clients:
        from careerforge.clients import listing
        out = listing()
        body = (f"[bold]Clients[/bold]\n\n"
                f"  Total:               {out['total']}\n"
                f"  Active:              [green]{out['active']}[/green]\n"
                f"  Pending:             [yellow]{out['pending']}[/yellow]\n"
                f"  Fulfilled:           {out['fulfilled']}\n"
                f"  Churned:             {out['churned']}\n"
                f"  MRR:                 [green]${out['mrr']:.0f}/mo[/green]\n"
                f"  One-time collected:  [green]${out['one_time_collected']}[/green]")
        if out["by_plan"]:
            body += "\n\n  By plan:\n"
            for p, n in sorted(out["by_plan"].items(), key=lambda kv: -kv[1]):
                body += f"    {p:<16s}  {n}\n"
        console.print(Panel(Text.from_markup(body.rstrip()), border_style="blue"))
        for c in out["clients"]:
            console.print(
                f"  [dim]{c.get('status','?'):>9s}[/dim]  "
                f"{c.get('plan',''):<16s}  "
                f"{c.get('user_id',''):<20s}  {c.get('email','')}"
            )
        return

    if not paywall_prompt("careerforge"):
        return
    while True:
        cycle()
        if args.interval <= 0:
            break
        time.sleep(args.interval * 60)


if __name__ == "__main__":
    main()
