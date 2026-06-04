#!/usr/bin/env python3
"""
Transcribe — bulk audio/video → .txt + .srt for creators.
$19/episode  ·  $79/mo (10 hrs)  ·  $297 bulk 30-episode pack.

Outputs feed the ShowNotes agent via data/tr_outputs/.

Usage:
  python3 run_transcribe_auto.py                       # one cycle
  python3 run_transcribe_auto.py --interval 60         # loop every N min
  python3 run_transcribe_auto.py --diagnose            # preflight
  python3 run_transcribe_auto.py --probe-ffmpeg        # ffmpeg in PATH + version
  python3 run_transcribe_auto.py --probe-whisper       # faster-whisper importable
  python3 run_transcribe_auto.py --probe-inputs        # queue + by-ext + unsupported files
  python3 run_transcribe_auto.py --files 50            # last 50 per-file outcomes
  python3 run_transcribe_auto.py --stuck               # slugs with ≥3 transcription failures
  python3 run_transcribe_auto.py --usage               # per-email hours-this-month vs 10hr cap
  python3 run_transcribe_auto.py --subscribers         # subscriber ledger + MRR
"""
import argparse
import sys
import time
from datetime import datetime

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from transcribe.tools import run_full_cycle
from paywall.agent_paywall import paywall_prompt
from autonomous.self_healing import with_healing

console = Console()


@with_healing("transcribe")
def cycle():
    console.print(Panel(
        Text.from_markup(
            f"[bold white]Transcribe Cycle[/bold white]\n"
            f"[dim]{datetime.now():%Y-%m-%d %H:%M:%S}[/dim]"),
        title="[bold blue]Wholesale Omniverse — Transcribe[/bold blue]",
        border_style="blue"))
    r = run_full_cycle()
    console.print(f"  [cyan]Transcripts produced:[/cyan] {r.get('transcripts_produced', 0)}")
    console.print(f"  [yellow]Failures:[/yellow]            {r.get('failures', 0)}")
    console.print(f"  [cyan]Sample pitches:[/cyan]       {r.get('outreach_sent', 0)}")
    console.print(f"  [green]Subs delivered:[/green]       {r.get('fulfillment_sent', 0)}")
    console.print(f"  [white]MRR:[/white]                  ${r.get('mrr', 0):.0f}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=int, default=0,
                        help="Loop interval in minutes (0 = single cycle)")
    parser.add_argument("--diagnose", action="store_true",
                        help="Preflight: SMTP, ffmpeg, whisper, inputs, outcomes; then exit")
    parser.add_argument("--probe-ffmpeg", action="store_true",
                        help="Check ffmpeg in PATH + version, then exit")
    parser.add_argument("--probe-whisper", action="store_true",
                        help="Check faster-whisper importable + version, then exit")
    parser.add_argument("--probe-inputs", action="store_true",
                        help="Inventory tr_inputs/ + by-extension + unsupported, then exit")
    parser.add_argument("--files", type=int, default=0,
                        help="Show last N per-file outcomes, then exit")
    parser.add_argument("--stuck", action="store_true",
                        help="Slugs with ≥3 transcription failures, then exit")
    parser.add_argument("--usage", action="store_true",
                        help="Per-email hours delivered this month vs 10hr cap, then exit")
    parser.add_argument("--subscribers", action="store_true",
                        help="List subscribers + MRR + by-plan, then exit")
    args = parser.parse_args()

    if args.diagnose:
        from transcribe.diagnose import main as diag_main
        sys.exit(diag_main())

    if args.probe_ffmpeg:
        from transcribe.health import probe_ffmpeg
        r = probe_ffmpeg()
        color = "green" if r.get("ok") else "red"
        if r.get("ok"):
            msg = f"  [green]ok[/green] — {r.get('path')}\n  {r.get('version','')[:80]}"
        else:
            msg = f"  [red]fail[/red] — {r.get('error','')}"
        console.print(Panel(Text.from_markup(f"[bold]ffmpeg probe[/bold]\n\n{msg}"),
                            border_style=color))
        sys.exit(0 if r.get("ok") else 1)

    if args.probe_whisper:
        from transcribe.health import probe_whisper
        r = probe_whisper()
        color = "green" if r.get("ok") else "red"
        msg = (f"  [green]ok[/green] — faster-whisper {r.get('version','?')}"
               if r.get("ok") else f"  [red]fail[/red] — {r.get('error','')}")
        console.print(Panel(Text.from_markup(f"[bold]faster-whisper probe[/bold]\n\n{msg}"),
                            border_style=color))
        sys.exit(0 if r.get("ok") else 1)

    if args.probe_inputs:
        from transcribe.health import probe_inputs
        r = probe_inputs()
        color = "green" if r.get("ok") else "red"
        age = f"{r['newest_age_days']}d" if r['newest_age_days'] is not None else "—"
        body = (f"[bold]Input inventory[/bold]\n\n"
                f"  tr_inputs:       {r['tr_inputs']} files  (newest {age})\n"
                f"  tr_outputs:      {r['tr_outputs']} .meta.json built\n")
        if r["by_ext"]:
            body += "\n  By extension:\n"
            for ext, n in sorted(r["by_ext"].items(), key=lambda kv: -kv[1]):
                body += f"    {ext:<10s}  {n}\n"
        if r["unsupported"]:
            body += f"\n  [yellow]Unsupported (silently skipped) — {len(r['unsupported'])}:[/yellow]\n"
            for u in r["unsupported"][:6]:
                body += f"    {u}\n"
            if len(r["unsupported"]) > 6:
                body += f"    ... +{len(r['unsupported']) - 6}\n"
        if r.get("error"):
            body += f"\n  [red]{r['error']}[/red]"
        console.print(Panel(Text.from_markup(body.rstrip()), border_style=color))
        sys.exit(0 if r.get("ok") else 1)

    if args.files:
        from transcribe.health import recent_files, file_outcome_summary
        for r in recent_files(args.files):
            color = "green" if r["outcome"] == "success" else "red"
            dur = f"{r['duration_seconds']:.1f}s" if r["duration_seconds"] else "—"
            console.print(
                f"  [dim]{r['ts'][:19]}[/dim]  [{color}]{r['outcome']:<16s}[/{color}]  "
                f"dur={dur:>9s}  {r['slug']}"
                + (f"  [dim]{(r.get('detail') or '')[:40]}[/dim]" if r.get("detail") else "")
            )
        s = file_outcome_summary()
        console.print(
            f"\n  log_total={s['total']}  "
            f"[green]success={s['success']}[/green]  "
            f"[red]ffmpeg_failed={s['ffmpeg_failed']}[/red]  "
            f"[red]whisper_failed={s['whisper_failed']}[/red]  "
            f"[red]whisper_missing={s['whisper_missing']}[/red]"
        )
        if s["success"]:
            console.print(f"  total_duration: [white]{s['total_duration_seconds']/3600:.1f}h[/white]"
                          f" across {s['success']} successful transcription(s)")
        return

    if args.stuck:
        from transcribe.health import stuck_files
        stuck = stuck_files(min_attempts=3)
        if not stuck:
            console.print("(no slugs with ≥3 transcription failures)")
            return
        console.print(f"[bold]Stuck slugs ({len(stuck)}):[/bold]\n")
        for r in stuck:
            console.print(
                f"  {r['slug']}  [red]{r['attempts']}× attempts[/red]  "
                f"last=[red]{r['last_outcome']}[/red]\n"
                f"    [dim]{r['last_detail'][:80]}[/dim]"
            )
        return

    if args.usage:
        from transcribe.health import (
            monthly_duration_per_email, MONTHLY_CAP_SECONDS, OVER_CAP_WARN_SECONDS,
        )
        from transcribe.subscribers import listing, PLANS
        usage = monthly_duration_per_email()
        if not usage:
            console.print("(no deliveries with duration recorded this month)")
            return
        plan_for = {(s["email"].lower()): s.get("plan", "") for s in listing()["subscribers"]}
        console.print(f"{'EMAIL':<40s}  {'HOURS':>6s} / 10h  PLAN")
        for e, dur in sorted(usage.items(), key=lambda kv: -kv[1]):
            hours = dur / 3600
            plan = plan_for.get(e, "?")
            if dur > MONTHLY_CAP_SECONDS:
                tag = "[red]OVER[/red]"
            elif dur >= OVER_CAP_WARN_SECONDS:
                tag = "[yellow]warn[/yellow]"
            else:
                tag = "[green]ok[/green]"
            console.print(f"  {e:<40s}  {hours:>6.2f}        {plan} {tag}")
        return

    if args.subscribers:
        from transcribe.subscribers import listing
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
                body += f"    {p:<18s}  {n}\n"
        console.print(Panel(Text.from_markup(body.rstrip()), border_style="blue"))
        for s in out["subscribers"]:
            console.print(
                f"  [dim]{s.get('status','?'):>9s}[/dim]  "
                f"{s.get('plan',''):<18s}  {s.get('email','')}"
            )
        return

    if not paywall_prompt("transcribe"):
        return
    while True:
        cycle()
        if args.interval <= 0:
            break
        time.sleep(args.interval * 60)


if __name__ == "__main__":
    main()
