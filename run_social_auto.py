#!/usr/bin/env python3
"""
Social Agent — autonomous multi-platform poster + paid-ads dispatcher.

Usage:
  python3 run_social_auto.py --status                 # show which platforms are live
  python3 run_social_auto.py --diagnose               # preflight: creds + platform health + pool inventory
  python3 run_social_auto.py --health-report          # per-platform failure-streak table from history
  python3 run_social_auto.py --dry-run                # show what would post on all platforms
  python3 run_social_auto.py                          # post to all live platforms
  python3 run_social_auto.py --platforms reddit,x     # only specified platforms
  python3 run_social_auto.py --audience wholesalers   # filter post pool by audience
  python3 run_social_auto.py --history                # show recent dispatches
  python3 run_social_auto.py --interval 60            # repeat every N minutes

Audiences: sellers, buyers, wholesalers.
"""
import os
import sys
import time
import argparse
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from social_agent.tools import dispatch, status_all, history, PLATFORMS

console = Console()


def cmd_status():
    rows = status_all()
    tbl = Table(title="Social Platform Status", border_style="blue")
    tbl.add_column("Platform", style="yellow")
    tbl.add_column("Kind")
    tbl.add_column("Live")
    tbl.add_column("Missing")
    for r in rows:
        live_str = "[green]✓ live[/green]" if r["live"] else "[yellow]✗ needs setup[/yellow]"
        tbl.add_row(
            r["platform"], r["kind"], live_str,
            ", ".join(r["missing_env_vars"]) or "—",
        )
    console.print(tbl)
    live_count = sum(1 for r in rows if r["live"])
    console.print(f"\n  [green]{live_count}[/green] live / "
                  f"[yellow]{len(rows) - live_count}[/yellow] need credentials")


def cmd_dispatch(audience: str, platforms: list, dry_run: bool):
    info = "(DRY RUN — no actual posts)" if dry_run else ""
    console.print(Panel(
        Text.from_markup(
            f"[bold white]Social Dispatch[/bold white] {info}\n"
            f"[dim]{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  |  "
            f"audience={audience or 'any'}  |  platforms={','.join(platforms) if platforms else 'all'}[/dim]"
        ),
        border_style="blue",
        title="[bold blue]Wholesale Omniverse — Social Agent[/bold blue]",
    ))

    out = dispatch(audience=audience, platforms=platforms or None, dry_run=dry_run)

    console.print(f"\n[bold]Post:[/bold] {out['post']['title']}")
    console.print(f"[dim]Audience: {out['post']['audience']}[/dim]\n")

    tbl = Table(title="Dispatch Results", border_style="green")
    tbl.add_column("Platform", style="yellow")
    tbl.add_column("Status")
    tbl.add_column("Detail")
    for r in out["results"]:
        status = r.get("status", "?")
        color = {"posted": "green", "dry_run": "cyan",
                 "skipped": "yellow", "failed": "red"}.get(status, "white")
        detail = (
            r.get("url") or r.get("reason") or r.get("error") or
            r.get("would_post_to") or r.get("note") or
            r.get("text", "")[:60] or "—"
        )
        tbl.add_row(r.get("platform", "?"),
                    f"[{color}]{status}[/{color}]",
                    str(detail))
    console.print(tbl)

    console.print(
        f"\n  [green]{out['posted']} posted[/green]   "
        f"[cyan]{out['dry_run']} dry-run[/cyan]   "
        f"[yellow]{out['skipped']} skipped[/yellow]   "
        f"[red]{out['failed']} failed[/red]"
    )


def cmd_history():
    rows = history(limit=10)
    if not rows:
        console.print("[dim]No dispatch history yet.[/dim]")
        return
    for entry in rows:
        when = entry["dispatched_at"][:19].replace("T", " ")
        kind = "(dry run)" if entry.get("dry_run") else ""
        console.print(f"\n[bold]{when}[/bold] {kind}")
        console.print(f"  Title: {entry['title']}")
        for r in entry["results"]:
            color = {"posted": "green", "dry_run": "cyan",
                     "skipped": "yellow", "failed": "red"}.get(r.get("status"), "white")
            console.print(f"    [{color}]{r['platform']:12}[/{color}] {r.get('status')}  "
                          f"{r.get('url') or r.get('reason') or r.get('error') or ''}")


def main():
    parser = argparse.ArgumentParser(description="Social Agent — autonomous multi-platform poster")
    parser.add_argument("--status",   action="store_true", help="Show which platforms have valid credentials")
    parser.add_argument("--diagnose", action="store_true",
                        help="Preflight: creds + per-platform health + content pool + cadence, then exit")
    parser.add_argument("--health-report", action="store_true",
                        help="Per-platform failure-streak table derived from dispatch history, then exit")
    parser.add_argument("--dry-run",  action="store_true", help="Show what would be posted without posting")
    parser.add_argument("--audience", default="", choices=["", "sellers", "buyers", "wholesalers"],
                        help="Filter post pool by audience")
    parser.add_argument("--platforms", default="", help="Comma-separated list (default: all)")
    parser.add_argument("--history",  action="store_true", help="Show recent dispatch history")
    parser.add_argument("--interval", type=int, default=0, help="Repeat every N minutes")
    args = parser.parse_args()

    if args.status:
        cmd_status()
        return
    if args.diagnose:
        from social_agent.diagnose import main as diag_main
        sys.exit(diag_main())
    if args.health_report:
        from social_agent.health import report_lines, summary as health_summary
        for line in report_lines():
            console.print(line)
        s = health_summary()
        if s["platforms_with_attempts"]:
            console.print()
            console.print(
                f"  [white]{s['healthy']}[/white] healthy / "
                f"[yellow]{s['warning']}[/yellow] warning  "
                f"(threshold ≥{s['alert_threshold']} consecutive failures)"
            )
        return
    if args.history:
        cmd_history()
        return

    platforms = [p.strip() for p in args.platforms.split(",") if p.strip()]
    for p in platforms:
        if p not in PLATFORMS:
            console.print(f"[red]Unknown platform: {p}. Choose from {', '.join(PLATFORMS)}[/red]")
            sys.exit(2)

    while True:
        cmd_dispatch(args.audience, platforms, dry_run=args.dry_run)
        if args.interval <= 0:
            break
        console.print(f"\n[dim]Next dispatch in {args.interval} minutes. Ctrl+C to stop.[/dim]")
        try:
            time.sleep(args.interval * 60)
        except KeyboardInterrupt:
            console.print("\n[dim]Stopped.[/dim]")
            break


if __name__ == "__main__":
    main()
