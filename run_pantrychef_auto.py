#!/usr/bin/env python3
"""
PantryChef — autonomous weekly meal plans from a user's pantry.
$14/mo basic  ·  $29/mo full+family  ·  $79 one-time 30-day deep package.

Usage:
  python3 run_pantrychef_auto.py                       # one cycle
  python3 run_pantrychef_auto.py --interval 60         # loop every N min
  python3 run_pantrychef_auto.py --diagnose            # preflight
  python3 run_pantrychef_auto.py --probe-inputs        # profiles + subs + thin pantries
  python3 run_pantrychef_auto.py --plans 50            # last 50 per-plan outcomes
  python3 run_pantrychef_auto.py --yield               # recipe-yield distribution
  python3 run_pantrychef_auto.py --usage               # per-user plans this month
  python3 run_pantrychef_auto.py --subscribers         # subscriber ledger + MRR + one-time
"""
import argparse
import sys
import time
from datetime import datetime

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from pantrychef.tools import run_full_cycle
from paywall.agent_paywall import paywall_prompt
from autonomous.self_healing import with_healing

console = Console()


@with_healing("pantrychef")
def cycle():
    console.print(Panel(
        Text.from_markup(
            f"[bold white]PantryChef Cycle[/bold white]\n"
            f"[dim]{datetime.now():%Y-%m-%d %H:%M:%S}[/dim]"),
        title="[bold blue]Wholesale Omniverse — PantryChef[/bold blue]",
        border_style="blue"))
    r = run_full_cycle()
    console.print(f"  [cyan]Free samples:[/cyan]    {r.get('outreach_sent', 0)}")
    console.print(f"  [green]Plans delivered:[/green]  {r.get('fulfillment_sent', 0)}")
    console.print(f"  [white]MRR:[/white]              ${r.get('mrr', 0):.0f}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=int, default=0,
                        help="Loop interval in minutes (0 = single cycle)")
    parser.add_argument("--diagnose", action="store_true",
                        help="Preflight: SMTP, inputs, outcomes, yield; then exit")
    parser.add_argument("--probe-inputs", action="store_true",
                        help="Triangulate profiles + subscribers + pantry depth, then exit")
    parser.add_argument("--plans", type=int, default=0,
                        help="Show last N per-plan outcomes, then exit")
    parser.add_argument("--yield", dest="show_yield", action="store_true",
                        help="Recipe-yield distribution, then exit")
    parser.add_argument("--usage", action="store_true",
                        help="Per-user plans delivered this month, then exit")
    parser.add_argument("--subscribers", action="store_true",
                        help="List subscribers + MRR + by-plan, then exit")
    args = parser.parse_args()

    if args.diagnose:
        from pantrychef.diagnose import main as diag_main
        sys.exit(diag_main())

    if args.probe_inputs:
        from pantrychef.health import probe_inputs
        r = probe_inputs()
        color = "green" if r.get("ok") else "red"
        body = (f"[bold]Inputs[/bold]\n\n"
                f"  Profiles on disk:    {r['profiles']}\n"
                f"  Subscribers total:   {r['subscribers_total']}\n"
                f"  Subscribers active:  [green]{r['subscribers_active']}[/green]\n"
                f"  Pantry min:          {r['pantry_min']}")
        if r["subs_missing_profile"]:
            body += (f"\n\n  [red]Active subs missing profile ({len(r['subs_missing_profile'])}):[/red] "
                     + ", ".join(r["subs_missing_profile"][:6]))
            if len(r["subs_missing_profile"]) > 6:
                body += f" +{len(r['subs_missing_profile']) - 6}"
        if r["thin_pantries"]:
            body += f"\n\n  [yellow]Thin pantries (<{r['pantry_min']} items):[/yellow]"
            for t in r["thin_pantries"][:8]:
                body += f"\n    {t['user_id']:<24s}  {t['items']} items"
        console.print(Panel(Text.from_markup(body), border_style=color))
        sys.exit(0 if r.get("ok") else 1)

    if args.plans:
        from pantrychef.health import recent_plans, plan_outcome_summary
        for r in recent_plans(args.plans):
            color = "green" if r["outcome"] == "success" else "red"
            console.print(
                f"  [dim]{r['ts'][:19]}[/dim]  [{color}]{r['outcome']:<18s}[/{color}]  "
                f"{r['user_id']:<20s}  {(r.get('detail') or '')[:50]}"
            )
        s = plan_outcome_summary()
        console.print(
            f"\n  log_total={s['total']}  "
            f"[green]success={s['success']}[/green]  "
            f"[red]no_user_profile={s['no_user_profile']}[/red]  "
            f"[red]pantry_too_small={s['pantry_too_small']}[/red]  "
            f"[red]mail_failed={s['mail_failed']}[/red]"
        )
        return

    if args.show_yield:
        from pantrychef.health import yield_summary, users_with_thin_plans, MIN_RECIPES
        s = yield_summary()
        body = (f"[bold]Recipe yield[/bold]\n\n"
                f"  Plans logged:          {s['total']}\n"
                f"  Avg recipes/plan:      {s['avg_recipes']}\n"
                f"  Avg shopping items:    {s['avg_shopping']}\n"
                f"  Thin plans (<{MIN_RECIPES}):  [yellow]{s['thin_plans']}[/yellow]")
        thin = users_with_thin_plans()
        if thin:
            body += "\n\n  [yellow]Users with thin recent plans:[/yellow]\n"
            for u in thin[:10]:
                body += (f"    {u['user_id']:<24s}  "
                         f"{u['thin_in_window']}/{u['window']} thin  "
                         f"last_recipes={u['last_recipes']}\n")
        console.print(Panel(Text.from_markup(body.rstrip()), border_style="blue"))
        return

    if args.usage:
        from pantrychef.health import monthly_usage_per_user
        usage = monthly_usage_per_user()
        if not usage:
            console.print("(no plans delivered this month)")
            return
        console.print(f"{'USER':<24s}  {'PLANS':>5s}")
        for u, n in sorted(usage.items(), key=lambda kv: -kv[1]):
            console.print(f"{u:<24s}  {n:>5d}")
        return

    if args.subscribers:
        from pantrychef.subscribers import listing
        out = listing()
        body = (f"[bold]Subscribers[/bold]\n\n"
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
        for s in out["subscribers"]:
            console.print(
                f"  [dim]{s.get('status','?'):>9s}[/dim]  "
                f"{s.get('plan',''):<16s}  "
                f"{s.get('user_id',''):<20s}  {s.get('email','')}"
            )
        return

    if not paywall_prompt("pantrychef"):
        return
    while True:
        cycle()
        if args.interval <= 0:
            break
        time.sleep(args.interval * 60)


if __name__ == "__main__":
    main()
