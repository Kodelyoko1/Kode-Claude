#!/usr/bin/env python3
"""
FBAds — Facebook ad pack generator + Meta-importable CSV + launcher + monitor.

Usage:
  python3 run_fbads_auto.py --diagnose          # preflight
  python3 run_fbads_auto.py --build             # generate today's pack (JSON + CSV)
  python3 run_fbads_auto.py --build --audience creators
  python3 run_fbads_auto.py --show              # print latest pack summary
  python3 run_fbads_auto.py --launch            # push to Meta (dry by default)
  python3 run_fbads_auto.py --launch --live     # actually create on Meta (PAUSED)
  python3 run_fbads_auto.py --launch --max 3    # cap how many to push
  python3 run_fbads_auto.py --higgsfield        # emit Higgsfield video prompts
  python3 run_fbads_auto.py --monitor           # pull Meta Insights + compute attribution
  python3 run_fbads_auto.py --report            # print perf report (winners/losers/unknowns)
  python3 run_fbads_auto.py --email-report      # send report to SMTP_USER
  python3 run_fbads_auto.py --push-conversions  # fire CAPI Lead+Purchase to Meta
"""
import argparse, os, sys
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from fbads.tools import (build_pack, save_pack_json, save_pack_csv,
                         latest_pack, render_summary, AUDIENCE_TARGETING)
from fbads.launcher import launch_pack

console = Console()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--diagnose", action="store_true")
    p.add_argument("--build", action="store_true",
                   help="Generate today's pack (JSON + Meta-importable CSV)")
    p.add_argument("--audience", action="append", default=None,
                   help="Limit --build to specific audience(s); pass multiple times")
    p.add_argument("--ads-per-audience", type=int, default=3)
    p.add_argument("--show", action="store_true",
                   help="Print summary of latest saved pack")
    p.add_argument("--launch", action="store_true",
                   help="Push latest pack to Meta Marketing API")
    p.add_argument("--live", action="store_true",
                   help="With --launch: actually create on Meta (default is dry-run)")
    p.add_argument("--max", type=int, default=0,
                   help="With --launch: cap how many ads to push")
    p.add_argument("--higgsfield", action="store_true",
                   help="Emit Higgsfield video prompts for the latest pack")
    p.add_argument("--higgsfield-status", action="store_true",
                   help="Show MCP render progress (rendered vs remaining) for the latest pack")
    p.add_argument("--monitor", action="store_true",
                   help="Pull Meta Insights + compute attribution (no email)")
    p.add_argument("--report", action="store_true",
                   help="Render performance report (refreshes data first)")
    p.add_argument("--email-report", action="store_true",
                   help="Send the report to SMTP_USER")
    p.add_argument("--push-conversions", action="store_true",
                   help="Fire CAPI Lead+Purchase events for unsent activations + invoices")
    p.add_argument("--conversions-dry", action="store_true",
                   help="With --push-conversions: classify but don't actually POST")
    a = p.parse_args()

    if a.diagnose:
        from fbads.diagnose import main as d; sys.exit(d())

    if a.build:
        audiences = a.audience or list(AUDIENCE_TARGETING.keys())
        pack = build_pack(audiences=audiences, ads_per_audience=a.ads_per_audience)
        jp = save_pack_json(pack)
        cp = save_pack_csv(pack)
        console.print(Panel(Text.from_markup(
            f"[bold]Pack built[/bold]\n\n"
            f"  JSON: {jp}\n"
            f"  CSV:  {cp}\n\n"
            f"  Ads: {pack['total']}\n"
            f"  Audiences: {len(pack['audiences'])}\n"
            f"  Potential spend: ${pack['potential_daily_spend']:.0f}/day"
        ), border_style="green"))
        return

    if a.show:
        pack = latest_pack()
        if not pack:
            console.print("(no packs saved — run --build first)")
            return
        console.print(render_summary(pack))
        return

    if a.higgsfield:
        from fbads.higgsfield import emit_prompts
        pack = latest_pack()
        if not pack:
            console.print("[red]no pack — run --build first[/red]")
            sys.exit(1)
        path = emit_prompts(pack)
        console.print(f"[green]Higgsfield prompts written:[/green] {path}")
        return

    if a.higgsfield_status:
        from fbads.higgsfield import render_status
        s = render_status()
        if not s["total_ads"]:
            console.print("[yellow]No prompts emitted yet.[/yellow] "
                          "Run [white]--higgsfield[/white] after building a pack.")
            return
        console.print(Panel(Text.from_markup(
            f"[bold]Higgsfield MCP render status[/bold]\n\n"
            f"  Pack date:       {s.get('pack_date','?')}\n"
            f"  Rendered:        {s['rendered']} / {s['total_ads']}\n"
            f"  Remaining:       {s['remaining']}\n"
            f"  Credits spent:   {s['credits_spent_total']}\n\n"
            + (f"  [dim]Next ad to render: {s['next_ad_name']}[/dim]\n"
               f"  [dim]Prompt: {(s['next_prompt'] or '')[:120]}…[/dim]\n"
               f"  [dim]Duration {s['next_duration']}s · aspect {s['next_aspect']}[/dim]"
               if s["next_ad_name"] else
               "  [green]All ads rendered.[/green]")
        ), border_style="cyan"))
        return

    if a.monitor:
        from fbads.monitor import pull_insights, compute_attribution
        r1 = pull_insights()
        if not r1.get("ok"):
            console.print(f"[red]Insights fetch failed:[/red] {r1.get('error','?')}")
            sys.exit(1)
        console.print(f"  [green]Insights fetched:[/green] {r1['fetched']} ads")
        r2 = compute_attribution()
        if not r2.get("ok"):
            console.print(f"[yellow]Attribution skipped:[/yellow] {r2.get('error','?')}")
            return
        t = r2.get("totals", {})
        console.print(f"  [white]Spend:[/white] ${t.get('total_spend',0):.2f}  "
                      f"[white]Revenue:[/white] ${t.get('total_revenue',0):.2f}  "
                      f"[white]Blended ROAS:[/white] {t.get('blended_roas','—')}")
        if r2.get("note"):
            console.print(f"  [dim]{r2['note']}[/dim]")
        return

    if a.push_conversions:
        from fbads.conversions import push_pending, probe
        pre = probe()
        if not pre["ok"]:
            console.print(f"[red]CAPI not ready:[/red] missing {','.join(pre['missing_creds'])}")
            sys.exit(1)
        console.print(
            f"  [white]Pending:[/white] leads={pre['pending_leads']}  "
            f"purchases={pre['pending_purchases']}  "
            f"[dim](already_sent={pre['already_sent']})[/dim]"
        )
        r = push_pending(dry=a.conversions_dry)
        tag = "[yellow]DRY[/yellow] " if a.conversions_dry else ""
        too_old = r.get("skipped_too_old", 0)
        console.print(f"  {tag}[green]Sent:[/green] {r['sent']}  "
                      f"[yellow]Skipped:[/yellow] {r['skipped']}  "
                      + (f"[red]TooOld:[/red] {too_old}  " if too_old else "")
                      + f"[dim]ledger={r.get('ledger_size','?')}[/dim]")
        for e in r.get("errors", [])[:5]:
            console.print(f"    [red]{e.get('event','?')}[/red] "
                          f"{e.get('agent','')} {e.get('email','')} — "
                          f"{e.get('reason','')[:140]}")
        return

    if a.report or a.email_report:
        from fbads.report import render_text
        body = render_text(refresh=True, email=a.email_report)
        console.print(body)
        if a.email_report:
            console.print(f"\n  [green]Emailed to[/green] {os.environ.get('SMTP_USER','?')}")
        return

    if a.launch:
        pack = latest_pack()
        if not pack:
            console.print("[red]no pack — run --build first[/red]")
            sys.exit(1)
        dry = not a.live
        if dry:
            console.print(Panel(Text.from_markup(
                "[yellow]DRY-RUN[/yellow] — pass --live to actually create on Meta"),
                border_style="yellow"))
        result = launch_pack(pack, dry=dry, max_ads=a.max)
        console.print(f"  Launched:  {result['launched']}")
        console.print(f"  Skipped:   {result['skipped']}")
        if result["errors"]:
            console.print(f"  [red]Errors ({len(result['errors'])}):[/red]")
            for e in result["errors"][:5]:
                console.print(f"    [red]{e.get('reason','')[:140]}[/red]")
        if dry and result["campaigns"]:
            console.print("\n  [dim]Would have created (PAUSED):[/dim]")
            for c in result["campaigns"][:8]:
                console.print(
                    f"    {c['ad_name']:<48s}  "
                    f"obj={c.get('meta_obj','?'):<20s}  "
                    f"opt={c.get('opt_goal','?'):<14s}  "
                    f"cta={c.get('cta','?'):<13s}  "
                    f"${c['daily_budget']}/day  →  {c['destination']}"
                )
            if len(result["campaigns"]) > 8:
                console.print(f"    [dim]... +{len(result['campaigns']) - 8} more[/dim]")
        return

    # Default: show diagnose
    from fbads.diagnose import main as d; sys.exit(d())


if __name__ == "__main__":
    main()
