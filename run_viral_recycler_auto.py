#!/usr/bin/env python3
"""ViralRecycler autonomous loop — download, transform, post.

Usage:
  python3 run_viral_recycler_auto.py                  # one cycle (uploads up to --max-uploads)
  python3 run_viral_recycler_auto.py --interval 60    # every 60 min
  python3 run_viral_recycler_auto.py --diagnose       # preflight: ffmpeg + yt-dlp + YT auth + queue
  python3 run_viral_recycler_auto.py --health-report  # derived per-niche + per-stage stats
  python3 run_viral_recycler_auto.py --queue          # show queue contents (processed + pending + errored)
  python3 run_viral_recycler_auto.py --cap-status     # today's uploads vs daily cap
"""
import sys
import time, argparse
from datetime import datetime
from pathlib import Path
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from viral_recycler.tools import run_full_cycle, DAILY_UPLOAD_CAP, _today_uploads
from paywall.agent_paywall import paywall_prompt
from autonomous.self_healing import with_healing

console = Console()


@with_healing("viral_recycler")
def cycle(max_uploads: int):
    console.print(Panel(
        Text.from_markup(
            f"[bold white]ViralRecycler Cycle[/bold white]\n"
            f"[dim]{datetime.now():%Y-%m-%d %H:%M:%S}[/dim]"),
        title="[bold blue]Wholesale Omniverse — ViralRecycler[/bold blue]",
        border_style="blue"))
    r = run_full_cycle(max_uploads=max_uploads)
    console.print(f"  [green]Uploaded:[/green]        {r.get('uploaded', 0)}")
    console.print(f"  [yellow]Skipped (cap):[/yellow]   {r.get('skipped', 0)}")
    if r.get("errors"):
        console.print(f"  [red]Errors:[/red]")
        for e in r["errors"]:
            console.print(f"    [dim]{e.get('stage', '?')}:[/dim] {e.get('error', '')}")
    for s in r.get("successes", []):
        yt_url = s.get("youtube", {}).get("shorts_url", "")
        console.print(f"  [white]→[/white] [cyan]{s['hook'][:60]}[/cyan]")
        if yt_url:
            console.print(f"     YouTube: {yt_url}")
        tt = s.get("tiktok", {})
        if tt.get("status") == "uploaded":
            console.print(f"     TikTok:  posted (publish_id={tt.get('publish_id', '?')})")
        elif tt.get("status") == "handed_off":
            console.print(f"     TikTok:  emailed to {tt.get('to', '')} for manual post")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=int, default=0)
    parser.add_argument("--max-uploads", type=int, default=1,
                        help="Max uploads per run (daily safety cap also applies)")
    parser.add_argument("--diagnose", action="store_true",
                        help="Preflight: ffmpeg + yt-dlp + YT auth + queue + cap + disk, then exit")
    parser.add_argument("--health-report", action="store_true",
                        help="Derived per-niche + per-stage stats from upload log, then exit")
    parser.add_argument("--queue", action="store_true",
                        help="Show queue contents grouped by status, then exit")
    parser.add_argument("--cap-status", action="store_true",
                        help="Today's uploads vs DAILY_UPLOAD_CAP, then exit")
    args = parser.parse_args()

    if args.diagnose:
        from viral_recycler.diagnose import main as diag_main
        sys.exit(diag_main())
    if args.health_report:
        from viral_recycler.health import report_lines
        for line in report_lines():
            console.print(line)
        return
    if args.queue:
        from autonomous import storage
        queue = storage.load("vr_sources.json", [])
        if not queue:
            console.print("[yellow]Queue is empty.[/yellow]")
            return
        processed = [s for s in queue if s.get("processed")]
        errored   = [s for s in queue if s.get("last_error")]
        pending   = [s for s in queue if not s.get("processed") and not s.get("last_error")]
        tbl = Table(title=f"vr_sources.json — {len(queue)} item(s)", border_style="blue")
        tbl.add_column("Status", style="yellow")
        tbl.add_column("Niche")
        tbl.add_column("URL")
        tbl.add_column("Detail")
        for s in pending[:10]:
            tbl.add_row("pending", s.get("niche", ""), s.get("url", "")[:60], "")
        for s in errored[:10]:
            tbl.add_row("[red]errored[/red]", s.get("niche", ""),
                        s.get("url", "")[:60], (s.get("last_error", "") or "")[:80])
        for s in processed[:5]:
            tbl.add_row("[green]processed[/green]", s.get("niche", ""),
                        s.get("url", "")[:60], s.get("youtube_url", "")[:60])
        console.print(tbl)
        console.print(f"\n  pending=[white]{len(pending)}[/white]   "
                      f"errored=[red]{len(errored)}[/red]   "
                      f"processed=[green]{len(processed)}[/green]")
        return
    if args.cap_status:
        used = _today_uploads()
        remaining = max(0, DAILY_UPLOAD_CAP - used)
        color = "yellow" if used >= DAILY_UPLOAD_CAP else "green"
        console.print(Panel(
            Text.from_markup(
                f"[bold]Daily cap status[/bold]\n\n"
                f"  Used today:  [{color}]{used}[/{color}] / {DAILY_UPLOAD_CAP}\n"
                f"  Remaining:   {remaining}\n"
                f"  Date:        {datetime.now():%Y-%m-%d}"
            ),
            border_style=color,
        ))
        return

    if not paywall_prompt("viral_recycler"):
        return
    while True:
        cycle(args.max_uploads)
        if args.interval <= 0:
            break
        time.sleep(args.interval * 60)


if __name__ == "__main__":
    main()
