#!/usr/bin/env python3
"""
SalesPageDoctor — heuristic audit of creator sales pages (Gumroad,
Payhip, Sellfy, Ko-fi, Lemon Squeezy). Lead-magnet preview + paid full
audits + monthly monitoring.

Usage:
  python3 run_salespage_doctor_auto.py                       # one cycle
  python3 run_salespage_doctor_auto.py --interval 60         # loop every N min
  python3 run_salespage_doctor_auto.py --diagnose            # preflight: SMTP, scraper, egress, Bing
  python3 run_salespage_doctor_auto.py --probe-egress        # HTTP probe only, then exit
  python3 run_salespage_doctor_auto.py --probe-bing          # consume one Bing query, then exit
  python3 run_salespage_doctor_auto.py --health-report       # per-query yield + audit outcomes
  python3 run_salespage_doctor_auto.py --audits 50           # last 50 per-URL audit outcomes
  python3 run_salespage_doctor_auto.py --clients             # client ledger + MRR + one-time collected
  python3 run_salespage_doctor_auto.py --audit-now URL       # run an ad-hoc audit on demand
"""
import argparse
import json
import sys
import time
from datetime import datetime

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from salespage_doctor.tools import run_full_cycle
from paywall.agent_paywall import paywall_prompt
from autonomous.self_healing import with_healing

console = Console()


@with_healing("salespage_doctor")
def cycle():
    console.print(Panel(
        Text.from_markup(
            f"[bold white]SalesPageDoctor Cycle[/bold white]\n"
            f"[dim]{datetime.now():%Y-%m-%d %H:%M:%S}[/dim]"),
        title="[bold blue]Wholesale Omniverse — SalesPageDoctor[/bold blue]",
        border_style="blue"))
    r = run_full_cycle()
    console.print(f"  [cyan]Prospects discovered:[/cyan] {r.get('discovered', 0)}")
    console.print(f"  [cyan]Audit previews sent:[/cyan]  {r.get('outreach_sent', 0)}")
    console.print(f"  [green]Client audits sent:[/green]   {r.get('fulfillment_sent', 0)}")
    console.print(f"  [white]MRR:[/white]                  ${r.get('mrr', 0):.0f}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=int, default=0,
                        help="Loop interval in minutes (0 = single cycle)")
    parser.add_argument("--diagnose", action="store_true",
                        help="Preflight: SMTP, scraper deps, egress, Bing; then exit")
    parser.add_argument("--probe-egress", action="store_true",
                        help="HTTP egress probe only, then exit")
    parser.add_argument("--probe-bing", action="store_true",
                        help="Consume one Bing query (no persistence), then exit")
    parser.add_argument("--health-report", action="store_true",
                        help="Per-query yield + audit outcome summary, then exit")
    parser.add_argument("--audits", type=int, default=0,
                        help="Show last N per-URL audit outcomes, then exit")
    parser.add_argument("--clients", action="store_true",
                        help="List clients + MRR + one-time collected, then exit")
    parser.add_argument("--audit-now", default="",
                        help="Run an ad-hoc audit on the given URL, print report path, then exit")
    args = parser.parse_args()

    if args.diagnose:
        from salespage_doctor.diagnose import main as diag_main
        sys.exit(diag_main())

    if args.probe_egress:
        from salespage_doctor.health import probe_egress
        r = probe_egress()
        color = "green" if r.get("ok") else "red"
        console.print(Panel(Text.from_markup(
            f"[bold]HTTP egress probe[/bold]\n\n"
            + (f"  [{color}]ok[/{color}] {r.get('probe')} → "
               f"HTTP {r.get('status')} ({r.get('bytes')} bytes)"
               if r.get("ok") else
               f"  [{color}]fail[/{color}] {r.get('probe')} — {r.get('error','')}")
        ), border_style=color))
        sys.exit(0 if r.get("ok") else 1)

    if args.probe_bing:
        from salespage_doctor.health import probe_bing
        r = probe_bing()
        color = "green" if r.get("ok") else "red"
        body = (f"[bold]Bing handshake probe[/bold]\n\n"
                f"  query: {r.get('query','')[:64]}\n")
        if r.get("ok"):
            body += f"  [{color}]ok[/{color}]  {r['results']} result(s) parsed"
        elif r.get("error"):
            body += f"  [{color}]fail[/{color}]  {r.get('error')}"
        else:
            body += f"  [{color}]fail[/{color}]  0 results parsed (dork rot or layout drift)"
        console.print(Panel(Text.from_markup(body), border_style=color))
        sys.exit(0 if r.get("ok") else 1)

    if args.health_report:
        from salespage_doctor.health import (
            query_report_lines, query_summary, audit_outcome_summary,
        )
        for line in query_report_lines():
            console.print(line)
        s = query_summary()
        if s["queries"]:
            console.print()
            console.print(
                f"  [white]{s['healthy']}[/white] healthy / "
                f"[yellow]{s['warning']}[/yellow] warning  "
                f"(threshold ≥{s['alert_threshold']} consecutive zeros)  "
                f"all-time discovered: [white]{s['total_discovered_all_time']}[/white]"
            )
        a = audit_outcome_summary()
        if a["total"]:
            console.print()
            console.print(
                f"  audits log: total=[white]{a['total']}[/white]  "
                f"[green]success={a['success']}[/green]  "
                f"[red]fetch_failed={a['fetch_failed']}[/red]  "
                f"[red]bs4_missing={a['bs4_missing']}[/red]  "
                f"[dim]high_score_skip={a['high_score_skip']}[/dim]  "
                f"avg_score=[white]{a['avg_score']}[/white]"
            )
            console.print(
                "  score dist: " +
                "  ".join(f"{k}=[white]{v}[/white]" for k, v in a["score_dist"].items())
            )
        return

    if args.audits:
        from salespage_doctor.health import recent_audits, audit_outcome_summary
        for r in recent_audits(args.audits):
            color = {"success": "green"}.get(r["outcome"], "red")
            console.print(
                f"  [dim]{r['ts'][:19]}[/dim]  [{color}]{r['outcome']:<15s}[/{color}]  "
                f"score={r['score']:>3d}  issues={r['issue_count']:>2d}  {r['url'][:60]}"
            )
        s = audit_outcome_summary()
        console.print(
            f"\n  log_total={s['total']}  "
            f"[green]success={s['success']}[/green]  "
            f"[red]fetch_failed={s['fetch_failed']}[/red]  "
            f"avg_score=[white]{s['avg_score']}[/white]"
        )
        return

    if args.clients:
        from salespage_doctor.clients import listing
        out = listing()
        body = (f"[bold]Clients[/bold]\n\n"
                f"  Total:               {out['total']}\n"
                f"  Active:              [green]{out['active']}[/green]\n"
                f"  Pending:             [yellow]{out['pending']}[/yellow]\n"
                f"  Fulfilled:           {out['fulfilled']}\n"
                f"  Churned:             {out['churned']}\n"
                f"  MRR:                 [green]${out['mrr']:.0f}/mo[/green]\n"
                f"  One-time collected:  [green]${out['one_time_collected']}[/green]")
        console.print(Panel(Text.from_markup(body), border_style="blue"))
        for c in out["clients"]:
            console.print(
                f"  [dim]{c.get('status','?'):>9s}[/dim]  "
                f"{c.get('plan',''):<14s}  "
                f"{(c.get('contact_email','')):<30s}  {c.get('slug','')}"
            )
        return

    if args.audit_now:
        from salespage_doctor.tools import audit_salespage, build_report, _page_slug
        url = args.audit_now.strip()
        result = audit_salespage(url)
        if "error" in result:
            console.print(f"[red]audit failed:[/red] {result['error']}")
            sys.exit(1)
        slug = _page_slug(url)
        report = build_report(slug, result, is_preview=False)
        body = (f"[bold]Ad-hoc audit[/bold]\n\n"
                f"  URL:    {url}\n"
                f"  Score:  [{'green' if result['score'] >= 75 else 'yellow' if result['score'] >= 50 else 'red'}]"
                f"{result['score']}/100[/]\n"
                f"  Issues: {result['issue_count']}\n"
                f"  Words:  {result['word_count']}\n"
                f"  Report: {report}")
        console.print(Panel(Text.from_markup(body), border_style="blue"))
        for i in result["issues"][:5]:
            console.print(f"  [{'red' if i['severity']=='high' else 'yellow' if i['severity']=='med' else 'white'}]"
                          f"[{i['severity'].upper():<4s}][/] {i['title']}")
        return

    if not paywall_prompt("salespage_doctor"):
        return
    while True:
        cycle()
        if args.interval <= 0:
            break
        time.sleep(args.interval * 60)


if __name__ == "__main__":
    main()
