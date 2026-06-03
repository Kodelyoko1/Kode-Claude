#!/usr/bin/env python3
"""InboxZero autonomous loop — triage owner inbox + pitch leads.

Usage:
  python3 run_inboxzero_auto.py                     # one triage cycle
  python3 run_inboxzero_auto.py --interval 60       # every 60 min
  python3 run_inboxzero_auto.py --diagnose          # IMAP + SMTP + config + recent triage
  python3 run_inboxzero_auto.py --dry-run           # triage preview, NO archive/flag changes
  python3 run_inboxzero_auto.py --subscribers       # list subscribers + MRR
  python3 run_inboxzero_auto.py --history           # last 5 triage cycles from iz_log.json
"""
import os
import sys
import time, argparse
from datetime import datetime
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from inboxzero.tools import run_full_cycle
from paywall.agent_paywall import paywall_prompt
from autonomous.self_healing import with_healing

console = Console()


@with_healing("inboxzero")
def cycle():
    console.print(Panel(
        Text.from_markup(
            f"[bold white]InboxZero Cycle[/bold white]\n"
            f"[dim]{datetime.now():%Y-%m-%d %H:%M:%S}[/dim]"),
        title="[bold blue]Wholesale Omniverse — InboxZero[/bold blue]",
        border_style="blue"))
    r = run_full_cycle()
    if r.get("skipped_reason"):
        console.print(f"  [yellow]Skipped:[/yellow] {r['skipped_reason']}")
    else:
        console.print(f"  [cyan]Inboxes triaged:[/cyan] {r.get('triaged_inboxes', 0)}")
        console.print(f"  [cyan]Unread scanned:[/cyan]  {r.get('scanned', 0)}")
    console.print(f"  [cyan]Lead pitches:[/cyan]    {r.get('outreach_sent', 0)}")
    console.print(f"  [white]MRR:[/white]             ${r.get('mrr', 0):.0f}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=int, default=0)
    parser.add_argument("--diagnose", action="store_true",
                        help="Preflight: SMTP + IMAP + recent triage + subscribers, then exit")
    parser.add_argument("--dry-run", action="store_true",
                        help="Triage preview against the live inbox — categorizes but does NOT archive/flag, then exit")
    parser.add_argument("--subscribers", action="store_true",
                        help="List subscribers + MRR, then exit")
    parser.add_argument("--history", action="store_true",
                        help="Last 5 triage cycles from iz_log.json, then exit")
    args = parser.parse_args()

    if args.diagnose:
        from inboxzero.diagnose import main as diag_main
        sys.exit(diag_main())
    if args.dry_run:
        from inboxzero.tools import triage_inbox
        user = os.environ.get("SMTP_USER")
        pwd  = os.environ.get("SMTP_PASS")
        if not (user and pwd):
            console.print("[red]SMTP_USER / SMTP_PASS not set — can't connect to IMAP[/red]")
            sys.exit(1)
        fetch_limit = int(os.environ.get("IZ_FETCH_LIMIT", "50"))
        result = triage_inbox(user, pwd, fetch_limit=fetch_limit, dry_run=True)
        if "error" in result:
            console.print(f"[red]Triage error: {result['error']}[/red]")
            sys.exit(1)
        summ = result["summary"]
        scanned = result["scanned"]
        tbl = Table(title=f"Dry-run triage — scanned {scanned} unread (NO changes made)",
                     border_style="cyan")
        tbl.add_column("Category", style="yellow")
        tbl.add_column("Count")
        tbl.add_column("Sample (first 3)")
        for cat in ("urgent", "important", "promo", "newsletter", "social", "other"):
            items = summ.get(cat, [])
            sample = "; ".join(f"{it['from'][:30]}: {it['subject'][:40]}" for it in items[:3])
            tbl.add_row(cat, str(len(items)), sample or "—")
        console.print(tbl)
        return
    if args.subscribers:
        from inboxzero.subscription import listing
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
            border_style="blue",
        ))
        for s in out["subscribers"]:
            console.print(
                f"  [dim]{s.get('status','?'):>8s}[/dim]  "
                f"{s.get('plan',''):<16s}  {s.get('email','')}"
            )
        return
    if args.history:
        from autonomous import storage
        log = storage.load("iz_log.json", [])
        if not log:
            console.print("[yellow]iz_log.json is empty — no triage cycles yet.[/yellow]")
            return
        for entry in log[-5:][::-1]:
            ts = entry.get("at", "")[:19]
            res = entry.get("result", {})
            if "error" in res:
                console.print(f"[red]{ts}  error: {res['error']}[/red]")
                continue
            actions = res.get("actions", {})
            summ = res.get("summary", {})
            console.print(
                f"[white]{ts}[/white]  "
                f"scanned={res.get('scanned',0)}  "
                f"archived={actions.get('archived',0)}  "
                f"flagged={actions.get('flagged',0)}  "
                f"left={actions.get('left',0)}  "
                f"·  urgent={len(summ.get('urgent',[]))} "
                f"important={len(summ.get('important',[]))}"
            )
        return

    if not paywall_prompt("inboxzero"):
        return
    while True:
        cycle()
        if args.interval <= 0:
            break
        time.sleep(args.interval * 60)


if __name__ == "__main__":
    main()
