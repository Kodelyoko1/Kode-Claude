#!/usr/bin/env python3
"""
FBAds — Facebook ad pack generator + Meta-importable CSV + launcher.

Usage:
  python3 run_fbads_auto.py --diagnose          # preflight
  python3 run_fbads_auto.py --build             # generate today's pack (JSON + CSV)
  python3 run_fbads_auto.py --build --audience creators
  python3 run_fbads_auto.py --show              # print latest pack summary
  python3 run_fbads_auto.py --launch            # push to Meta (dry by default)
  python3 run_fbads_auto.py --launch --live     # actually create on Meta (PAUSED)
  python3 run_fbads_auto.py --launch --max 3    # cap how many to push
  python3 run_fbads_auto.py --higgsfield        # emit Higgsfield video prompts
"""
import argparse, sys
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
            console.print("\n  [dim]Would have created:[/dim]")
            for c in result["campaigns"][:8]:
                console.print(f"    {c['ad_name']:<48s}  obj={c['objective']:<10s}  "
                              f"${c['daily_budget']}/day  → {c['destination']}")
        return

    # Default: show diagnose
    from fbads.diagnose import main as d; sys.exit(d())


if __name__ == "__main__":
    main()
