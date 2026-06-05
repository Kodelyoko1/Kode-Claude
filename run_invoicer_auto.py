#!/usr/bin/env python3
"""
Invoicer — autonomous PayPal invoice generator across the agent fleet.

Walks every <agent>_subscribers.json / <agent>_clients.json, finds
active subscribers due for billing this cycle, drafts a PayPal invoice
per (email, plan), and (when INVOICER_LIVE=1) sends it.

DEFAULTS TO DRY-RUN. No real invoices are posted unless INVOICER_LIVE=1.

Usage:
  python3 run_invoicer_auto.py                       # one cycle
  python3 run_invoicer_auto.py --diagnose            # preflight
  python3 run_invoicer_auto.py --probe               # PayPal OAuth + Invoicing check
  python3 run_invoicer_auto.py --due                 # list due invoices (read-only)
  python3 run_invoicer_auto.py --invoices N          # last N attempt outcomes
  python3 run_invoicer_auto.py --send-once SLUG      # force send one (agent:email:plan)

Cron pattern (after Invoicing feature is enabled + subscribers added):
  0 9 1 * *  INVOICER_LIVE=1 python3 run_invoicer_auto.py   # monthly on the 1st
"""
import argparse
import os
import sys
from datetime import datetime

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from invoicer.tools import run_cycle, find_due_invoices, LIVE, MAX_PER_CYCLE
from paywall.agent_paywall import paywall_prompt
from autonomous.self_healing import with_healing

console = Console()


@with_healing("invoicer")
def cycle():
    mode = "[red]LIVE[/red]" if LIVE else "[green]dry-run[/green]"
    console.print(Panel(Text.from_markup(
        f"[bold white]Invoicer Cycle[/bold white] · {mode}\n"
        f"[dim]{datetime.now():%Y-%m-%d %H:%M:%S}[/dim]"),
        title="[bold blue]Wholesale Omniverse — Invoicer[/bold blue]",
        border_style="blue"))
    r = run_cycle()
    console.print(f"  [cyan]Due found:[/cyan]   {r['due_found']}")
    console.print(f"  [cyan]Capped at:[/cyan]   {r['due_capped']} (max {MAX_PER_CYCLE})")
    console.print(f"  [green]Sent:[/green]        {r['sent']}")
    console.print(f"  [red]Failed:[/red]      {r['failed']}")
    console.print(f"  [white]Mode:[/white]        {'LIVE' if r['live'] else 'dry-run'}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--diagnose", action="store_true")
    p.add_argument("--probe", action="store_true",
                   help="Probe PayPal OAuth + Invoicing endpoint, then exit")
    p.add_argument("--due", action="store_true",
                   help="List invoices that WOULD be sent this cycle (read-only)")
    p.add_argument("--invoices", type=int, default=0,
                   help="Show last N invoice attempt outcomes")
    p.add_argument("--send-once", default="",
                   help="Send a single invoice immediately, key format 'agent:email:plan'")
    a = p.parse_args()

    if a.diagnose:
        from invoicer.diagnose import main as d
        sys.exit(d())

    if a.probe:
        from invoicer.health import probe_paypal_invoicing
        r = probe_paypal_invoicing()
        color = "green" if r.get("ok") else "red"
        if r.get("ok"):
            msg = f"[{color}]ok[/{color}] {r.get('detail','')}"
        elif r.get("stage") == "oauth":
            msg = f"[{color}]fail at OAuth[/{color}] — {r.get('error','')}"
        else:
            msg = (f"[{color}]fail at Invoicing[/{color}] HTTP {r.get('status_code')}\n"
                   f"  {r.get('error','')}: {r.get('message','')}")
        console.print(Panel(Text.from_markup(f"[bold]PayPal probe[/bold]\n\n  {msg}"),
                            border_style=color))
        sys.exit(0 if r.get("ok") else 1)

    if a.due:
        due = find_due_invoices()
        if not due:
            console.print("(no invoices due this cycle)")
            return
        console.print(f"[bold]{len(due)} due[/bold] (cap per cycle: {MAX_PER_CYCLE}):\n")
        total = 0.0
        for d in due:
            console.print(f"  {d['agent']:<18s}  {d['plan']:<22s}  ${d['amount']:>7.2f}  "
                          f"{d['cycle']:<8s}  {d['email']}")
            total += d["amount"]
        console.print(f"\n  potential this cycle: ${min(len(due), MAX_PER_CYCLE) * 0 + total:.2f}")
        return

    if a.invoices:
        from invoicer.health import recent_invoices, invoice_outcome_summary
        for r in recent_invoices(a.invoices):
            color = "green" if r.get("ok") else "red"
            tag = "DRY" if r.get("dry_run") else ("LIVE" if r.get("live") else "—")
            console.print(
                f"  [dim]{r['ts'][:19]}[/dim]  [{color}]{tag:<4s}[/{color}]  "
                f"{r['invoice_number']:<14s}  {r.get('agent',''):<18s}  "
                f"{r.get('plan',''):<22s}  ${r.get('amount',0):>7.2f}  {r.get('email','')}"
            )
            if not r.get("ok"):
                console.print(f"        [red]↳ {r.get('error','')[:140]}[/red]")
        s = invoice_outcome_summary()
        console.print(f"\n  log_total={s['total']}  ok={s['ok']}  failed={s['failed']}  "
                      f"dry_run={s['dry_run']}  live={s['live']}  "
                      f"collected=${s['total_collected']:.2f}")
        return

    if a.send_once:
        parts = a.send_once.split(":")
        if len(parts) != 3:
            console.print("[red]--send-once requires 'agent:email:plan' format[/red]")
            sys.exit(1)
        # Re-discover the task
        all_due = find_due_invoices()
        match = next((t for t in all_due
                      if t["agent"] == parts[0] and t["email"] == parts[1].lower()
                      and t["plan"] == parts[2]), None)
        if not match:
            console.print(f"[red]No active matching subscription for {a.send_once}[/red]")
            console.print("(Either not active, or already invoiced this cycle. Check --due.)")
            sys.exit(1)
        from invoicer.tools import _draft_invoice_for, _post_invoice, _mark_invoiced, _append_log, _now
        draft = _draft_invoice_for(match)
        result = _post_invoice(draft)
        entry = {"ts": _now(), "invoice_number": draft["invoice_number"],
                 **match, "live": LIVE, **result}
        _append_log(entry)
        if result.get("ok"):
            _mark_invoiced(match)
            console.print(f"[green]✓[/green] {draft['invoice_number']}  ${match['amount']:.2f}")
            if result.get("hosted_url"):
                console.print(f"  hosted: {result['hosted_url']}")
        else:
            console.print(f"[red]✗ {result.get('error','')[:200]}[/red]")
            sys.exit(1)
        return

    if not paywall_prompt("invoicer"):
        return
    cycle()


if __name__ == "__main__":
    main()
