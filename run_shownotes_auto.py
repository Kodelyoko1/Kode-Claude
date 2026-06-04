#!/usr/bin/env python3
"""
ShowNotes — transcripts → structured podcast/video show notes.
$29/episode  ·  $99/mo (4 eps)  ·  $297/mo unlimited.

Two input sources (deduped by slug):
  · data/sn_inputs/<slug>.txt   (owner-dropped)
  · data/tr_outputs/<slug>.txt  (auto-chained from Transcribe)

Usage:
  python3 run_shownotes_auto.py                       # one cycle
  python3 run_shownotes_auto.py --interval 60         # loop every N min
  python3 run_shownotes_auto.py --diagnose            # preflight
  python3 run_shownotes_auto.py --probe-inputs        # both source dirs side-by-side
  python3 run_shownotes_auto.py --probe-anthropic     # verify ANTHROPIC_API_KEY works
  python3 run_shownotes_auto.py --episodes 50         # last 50 per-episode outcomes
  python3 run_shownotes_auto.py --srt                 # SRT parse outcome summary
  python3 run_shownotes_auto.py --usage               # per-email deliveries this month
  python3 run_shownotes_auto.py --subscribers         # subscriber ledger + MRR
"""
import argparse
import sys
import time
from datetime import datetime

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from shownotes.tools import run_full_cycle
from paywall.agent_paywall import paywall_prompt
from autonomous.self_healing import with_healing

console = Console()


@with_healing("shownotes")
def cycle():
    console.print(Panel(
        Text.from_markup(
            f"[bold white]ShowNotes Cycle[/bold white]\n"
            f"[dim]{datetime.now():%Y-%m-%d %H:%M:%S}[/dim]"),
        title="[bold blue]Wholesale Omniverse — ShowNotes[/bold blue]",
        border_style="blue"))
    r = run_full_cycle()
    console.print(f"  [cyan]Show notes built:[/cyan]    {r.get('shownotes_produced', 0)}")
    console.print(f"  [cyan]Sample pitches:[/cyan]      {r.get('outreach_sent', 0)}")
    console.print(f"  [green]Subs delivered:[/green]      {r.get('fulfillment_sent', 0)}")
    console.print(f"  [white]MRR:[/white]                 ${r.get('mrr', 0):.0f}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=int, default=0,
                        help="Loop interval in minutes (0 = single cycle)")
    parser.add_argument("--diagnose", action="store_true",
                        help="Preflight: SMTP, inputs, Claude, outcomes; then exit")
    parser.add_argument("--probe-inputs", action="store_true",
                        help="Both source dirs side-by-side + Transcribe chain age, then exit")
    parser.add_argument("--probe-anthropic", action="store_true",
                        help="Verify ANTHROPIC_API_KEY with a cheap haiku call, then exit")
    parser.add_argument("--episodes", type=int, default=0,
                        help="Show last N per-episode outcomes, then exit")
    parser.add_argument("--srt", action="store_true",
                        help="SRT parse outcome summary, then exit")
    parser.add_argument("--usage", action="store_true",
                        help="Per-email shownotes delivered this month, then exit")
    parser.add_argument("--subscribers", action="store_true",
                        help="List subscribers + MRR + by-plan, then exit")
    args = parser.parse_args()

    if args.diagnose:
        from shownotes.diagnose import main as diag_main
        sys.exit(diag_main())

    if args.probe_inputs:
        from shownotes.health import probe_inputs
        r = probe_inputs()
        color = "green" if r.get("ok") else "red"
        sn_age = f"{r['sn_inputs_newest_age_days']}d" if r['sn_inputs_newest_age_days'] is not None else "—"
        tr_age = f"{r['tr_outputs_newest_age_days']}d" if r['tr_outputs_newest_age_days'] is not None else "—"
        idle = "[yellow]idle[/yellow]" if r['tr_chain_idle'] else "[green]fresh[/green]"
        body = (f"[bold]Input triangulation[/bold]\n\n"
                f"  sn_inputs/  (owner-dropped):  {r['sn_inputs']} files  (newest {sn_age})\n"
                f"  tr_outputs/ (Transcribe chain): {r['tr_outputs']} files  (newest {tr_age})  {idle}\n"
                f"  candidates (unique by slug):  {r['candidates']}\n"
                f"  sn_outputs/ already built:    {r['sn_outputs']}\n"
                f"  Stale threshold:              {r['tr_chain_stale_days']}d\n"
                f"  Min transcript chars:         {r['min_transcript_chars']}")
        console.print(Panel(Text.from_markup(body), border_style=color))
        sys.exit(0 if r.get("ok") else 1)

    if args.probe_anthropic:
        from shownotes.health import probe_anthropic
        r = probe_anthropic()
        if not r.get("enabled"):
            color = "yellow"
            msg = f"  [yellow]disabled[/yellow] — {r.get('detail','')}"
        elif r.get("ok"):
            color = "green"
            msg = f"  [green]ok[/green] — {r.get('detail','')}"
        else:
            color = "red"
            msg = f"  [red]fail[/red] — {r.get('error','')}"
        console.print(Panel(Text.from_markup(f"[bold]Claude probe[/bold]\n\n{msg}"),
                            border_style=color))
        sys.exit(0 if (not r.get("enabled") or r.get("ok")) else 1)

    if args.episodes:
        from shownotes.health import recent_episodes, episode_outcome_summary
        for r in recent_episodes(args.episodes):
            color = "green" if r["outcome"] == "success" else "red"
            console.print(
                f"  [dim]{r['ts'][:19]}[/dim]  [{color}]{r['outcome']:<12s}[/{color}]  "
                f"src={r['source']:<10s}  {r['slug']}"
                + (f"  [dim]{(r.get('detail') or '')[:40]}[/dim]" if r.get("detail") else "")
            )
        s = episode_outcome_summary()
        by_source = "  ".join(f"{k}={v}" for k, v in s["by_source"].items())
        console.print(
            f"\n  log_total=[white]{s['total']}[/white]  "
            f"[green]success={s['success']}[/green]  "
            f"[red]too_short={s['too_short']}[/red]  "
            f"[red]build_failed={s['build_failed']}[/red]"
            + (f"  · by_source: {by_source}" if by_source else "")
        )
        return

    if args.srt:
        from shownotes.health import srt_outcome_summary
        s = srt_outcome_summary()
        body = (f"[bold]SRT parse outcomes[/bold]\n\n"
                f"  Total:     {s['total']}\n"
                f"  Parsed:    [green]{s['parsed']}[/green]\n"
                f"  No SRT:    {s['no_srt']}\n"
                f"  Malformed: [red]{s['malformed']}[/red]")
        if s["malformed_recent"]:
            body += "\n\n  [yellow]Recent malformed slugs:[/yellow]\n"
            for slug in s["malformed_recent"]:
                body += f"    {slug}\n"
        console.print(Panel(Text.from_markup(body.rstrip()), border_style="blue"))
        return

    if args.usage:
        from shownotes.health import monthly_deliveries_per_email
        from shownotes.subscribers import listing, PLANS
        usage = monthly_deliveries_per_email()
        if not usage:
            console.print("(no deliveries recorded this month)")
            return
        plan_for = {(s["email"].lower()): s.get("plan", "") for s in listing()["subscribers"]}
        console.print(f"{'EMAIL':<40s}  {'COUNT':>5s}  PLAN / CAP")
        for e, n in sorted(usage.items(), key=lambda kv: -kv[1]):
            plan = plan_for.get(e, "?")
            cap = PLANS.get(plan, {}).get("monthly_cap", 0)
            cap_s = "∞" if cap < 0 else (str(cap) if cap > 0 else "—")
            tag = ""
            if cap > 0:
                if n > cap:    tag = " [red]OVER[/red]"
                elif n == cap: tag = " [yellow]AT[/yellow]"
            console.print(f"{e:<40s}  {n:>5d}  {plan} / {cap_s}{tag}")
        return

    if args.subscribers:
        from shownotes.subscribers import listing
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
                body += f"    {p:<22s}  {n}\n"
        console.print(Panel(Text.from_markup(body.rstrip()), border_style="blue"))
        for s in out["subscribers"]:
            console.print(
                f"  [dim]{s.get('status','?'):>9s}[/dim]  "
                f"{s.get('plan',''):<22s}  {s.get('email','')}"
            )
        return

    if not paywall_prompt("shownotes"):
        return
    while True:
        cycle()
        if args.interval <= 0:
            break
        time.sleep(args.interval * 60)


if __name__ == "__main__":
    main()
