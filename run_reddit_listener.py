#!/usr/bin/env python3
"""
Reddit Listener — surface engagement opportunities (read-only, compliant).

Usage:
  python3 run_reddit_listener.py                    # one-shot scan, default subs
  python3 run_reddit_listener.py --hours 48         # show matches from last 48h
  python3 run_reddit_listener.py --audience sellers # filter by audience
  python3 run_reddit_listener.py --stream           # live tail (Ctrl+C to stop)
  python3 run_reddit_listener.py --digest           # email digest of recent matches
  python3 run_reddit_listener.py --mark-engaged RDT-xxxxx
  python3 run_reddit_listener.py --summary          # overall stats
"""
import argparse
import sys
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from social_agent.listeners.reddit import (
    scan_once, stream, recent_leads, mark_engaged, summary, SUBREDDITS,
)
from email_template import send_branded_email

console = Console()


def _print_leads_table(leads: list, title: str = "Reddit Leads"):
    if not leads:
        console.print(f"[dim]No leads matching filters.[/dim]")
        return
    tbl = Table(title=title, border_style="green")
    tbl.add_column("Lead ID",   style="yellow")
    tbl.add_column("Sub",       style="cyan")
    tbl.add_column("Audience")
    tbl.add_column("Score",     justify="right")
    tbl.add_column("Title")
    tbl.add_column("URL",       style="blue")
    for l in leads[:50]:
        engaged_marker = "[green]✓[/green]" if l.get("engaged") else ""
        tbl.add_row(
            f"{engaged_marker} {l['lead_id']}",
            l["subreddit"],
            l.get("audience", ""),
            str(l.get("score", 0)),
            l["title"][:80],
            l["url"][:70],
        )
    console.print(tbl)


def cmd_once(audience: str, min_score: int):
    console.print(Panel(
        Text.from_markup(
            f"[bold white]Reddit Listener — One-Shot Scan[/bold white]\n"
            f"[dim]{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  |  "
            f"{len(SUBREDDITS)} subs  |  min_score={min_score}[/dim]"
        ),
        border_style="blue",
        title="[bold blue]Wholesale Omniverse — Reddit Listener[/bold blue]",
    ))
    result = scan_once(min_score=min_score)
    console.print(
        f"\n  Scanned: [white]{result['submissions_seen']}[/white] submissions across "
        f"[white]{result['subreddits_scanned']}[/white] subreddits  →  "
        f"[green]{result['new_leads']} new lead(s)[/green]"
    )
    leads = recent_leads(hours=24, audience=audience)
    _print_leads_table(leads, "Today's Matches")


def cmd_stream(min_score: int):
    console.print(Panel(
        Text.from_markup(
            "[bold white]Reddit Listener — LIVE STREAM[/bold white]\n"
            "[dim]Press Ctrl+C to stop[/dim]"
        ),
        border_style="blue",
        title="[bold blue]Wholesale Omniverse — Reddit Listener[/bold blue]",
    ))
    try:
        for hit in stream(min_score=min_score):
            ts = datetime.now().strftime("%H:%M:%S")
            console.print(
                f"\n[dim]{ts}[/dim] [yellow]{hit['lead_id']}[/yellow] "
                f"[cyan]r/{hit['subreddit']}[/cyan]  "
                f"[white]score={hit['score']}[/white]  "
                f"[magenta]({hit['audience']})[/magenta]"
            )
            console.print(f"  {hit['title']}")
            console.print(f"  [blue]{hit['url']}[/blue]")
    except KeyboardInterrupt:
        console.print("\n[dim]Stopped.[/dim]")


def cmd_recent(hours: int, audience: str, only_open: bool):
    engaged = False if only_open else None
    leads = recent_leads(hours=hours, audience=audience, engaged=engaged)
    _print_leads_table(leads, f"Reddit Leads — last {hours}h")


def cmd_digest(hours: int, audience: str):
    leads = recent_leads(hours=hours, audience=audience, engaged=False)
    if not leads:
        console.print(f"[dim]No new unengaged leads in the last {hours}h — skipping digest.[/dim]")
        return

    import os
    to_email = os.environ.get("DIGEST_EMAIL") or os.environ.get("SMTP_USER", "")
    if not to_email:
        console.print("[red]Set DIGEST_EMAIL or SMTP_USER in .env to send the digest.[/red]")
        return

    rows_text = []
    rows_html = []
    for l in leads[:30]:
        rows_text.append(
            f"- [{l['audience']:11}] score={l['score']:>2}  "
            f"r/{l['subreddit']}\n  {l['title']}\n  {l['url']}"
        )
        rows_html.append(
            f'<tr>'
            f'<td style="padding:6px;border-bottom:1px solid #eee;"><strong>{l["audience"]}</strong></td>'
            f'<td style="padding:6px;border-bottom:1px solid #eee;">{l["score"]}</td>'
            f'<td style="padding:6px;border-bottom:1px solid #eee;">r/{l["subreddit"]}</td>'
            f'<td style="padding:6px;border-bottom:1px solid #eee;">'
            f'<a href="{l["url"]}" style="color:#f59e0b;">{l["title"][:100]}</a></td>'
            f'</tr>'
        )

    subject = f"Reddit Listener — {len(leads)} new threads worth engaging"
    body_text = (
        f"Top Reddit threads matching your keywords in the last {hours}h:\n\n"
        + "\n\n".join(rows_text)
        + "\n\nReply to a thread on Reddit, then mark it engaged:\n"
        + "  python3 run_reddit_listener.py --mark-engaged LEAD_ID"
    )
    body_html = (
        f"<p>Top Reddit threads matching your keywords in the last <strong>{hours}h</strong>:</p>"
        f'<table cellpadding="0" cellspacing="0" style="width:100%;border-collapse:collapse;">'
        f'<thead><tr style="background:#f3f4f6;">'
        f'<th style="text-align:left;padding:6px;">Audience</th>'
        f'<th style="text-align:left;padding:6px;">Score</th>'
        f'<th style="text-align:left;padding:6px;">Subreddit</th>'
        f'<th style="text-align:left;padding:6px;">Thread</th>'
        f'</tr></thead><tbody>'
        + "".join(rows_html)
        + '</tbody></table>'
        + '<p style="margin-top:16px;color:#6b7280;font-size:13px;">'
        + 'Engage on Reddit with a value-first reply, then mark the lead engaged: '
        + '<code>python3 run_reddit_listener.py --mark-engaged LEAD_ID</code></p>'
    )

    result = send_branded_email(
        to_email=to_email,
        subject=subject,
        body_text=body_text,
        body_html_inner=body_html,
    )
    if result.get("status") == "sent":
        console.print(f"[green]✓ Digest sent to {to_email}[/green] — {len(leads)} threads")
    else:
        console.print(f"[red]Digest failed: {result}[/red]")


def cmd_summary():
    s = summary()
    console.print(Panel(
        Text.from_markup(
            f"[bold]Reddit Lead Summary[/bold]\n\n"
            f"  Total leads:       [white]{s['total_leads']}[/white]\n"
            f"  Open (unengaged):  [yellow]{s['open']}[/yellow]\n"
            f"  Engaged:           [green]{s['engaged']}[/green]\n\n"
            f"  Sellers:           [white]{s['by_audience']['sellers']}[/white]\n"
            f"  Wholesalers:       [white]{s['by_audience']['wholesalers']}[/white]\n"
            f"  Buyers:            [white]{s['by_audience']['buyers']}[/white]"
        ),
        border_style="blue",
    ))


def main():
    parser = argparse.ArgumentParser(description="Reddit Listener — read-only opportunity finder")
    parser.add_argument("--once",      action="store_true", help="One-shot scan + show today's matches (default)")
    parser.add_argument("--stream",    action="store_true", help="Live tail — yields matches as they're posted")
    parser.add_argument("--hours",     type=int, default=24, help="Window for --once / --digest / --recent")
    parser.add_argument("--audience",  default="", choices=["", "sellers", "buyers", "wholesalers"])
    parser.add_argument("--min-score", type=int, default=3, help="Drop matches under this score")
    parser.add_argument("--digest",    action="store_true", help="Email a digest of unengaged matches")
    parser.add_argument("--recent",    action="store_true", help="Show stored leads from last N hours")
    parser.add_argument("--only-open", action="store_true", help="With --recent: hide engaged leads")
    parser.add_argument("--mark-engaged", metavar="LEAD_ID", help="Mark a lead engaged")
    parser.add_argument("--notes",     default="", help="Notes to attach when marking engaged")
    parser.add_argument("--summary",   action="store_true", help="Show overall stats")
    args = parser.parse_args()

    if args.mark_engaged:
        out = mark_engaged(args.mark_engaged, args.notes)
        if out.get("error"):
            console.print(f"[red]{out['error']}[/red]")
        else:
            console.print(f"[green]✓ Marked {args.mark_engaged} engaged.[/green]")
        return
    if args.summary:
        cmd_summary(); return
    if args.digest:
        cmd_digest(args.hours, args.audience); return
    if args.stream:
        cmd_stream(args.min_score); return
    if args.recent:
        cmd_recent(args.hours, args.audience, args.only_open); return

    # default
    cmd_once(args.audience, args.min_score)


if __name__ == "__main__":
    main()
