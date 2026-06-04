#!/usr/bin/env python3
"""
CarouselForge — LinkedIn/IG/Pinterest carousel designer.
$29/carousel · $99/mo (4) · $297/mo unlimited.

Usage:
  python3 run_carouselforge_auto.py                       # one cycle
  python3 run_carouselforge_auto.py --interval 60         # loop
  python3 run_carouselforge_auto.py --diagnose
  python3 run_carouselforge_auto.py --probe-pillow
  python3 run_carouselforge_auto.py --probe-fonts
  python3 run_carouselforge_auto.py --probe-inputs
  python3 run_carouselforge_auto.py --carousels 50
  python3 run_carouselforge_auto.py --usage
  python3 run_carouselforge_auto.py --subscribers
"""
import argparse, sys, time
from datetime import datetime
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from carouselforge.tools import run_full_cycle
from paywall.agent_paywall import paywall_prompt
from autonomous.self_healing import with_healing

console = Console()


@with_healing("carouselforge")
def cycle():
    console.print(Panel(Text.from_markup(
        f"[bold white]CarouselForge Cycle[/bold white]\n[dim]{datetime.now():%Y-%m-%d %H:%M:%S}[/dim]"),
        title="[bold blue]Wholesale Omniverse — CarouselForge[/bold blue]", border_style="blue"))
    r = run_full_cycle()
    console.print(f"  [cyan]Carousels rendered:[/cyan] {r.get('carousels_produced', 0)}")
    console.print(f"  [yellow]Failures:[/yellow]          {r.get('failures', 0)}")
    console.print(f"  [cyan]Sample pitches:[/cyan]     {r.get('outreach_sent', 0)}")
    console.print(f"  [green]Subs delivered:[/green]     {r.get('fulfillment_sent', 0)}")
    console.print(f"  [white]MRR:[/white]                ${r.get('mrr', 0):.0f}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--interval", type=int, default=0)
    p.add_argument("--diagnose", action="store_true")
    p.add_argument("--probe-pillow", action="store_true")
    p.add_argument("--probe-fonts", action="store_true")
    p.add_argument("--probe-inputs", action="store_true")
    p.add_argument("--carousels", type=int, default=0)
    p.add_argument("--usage", action="store_true")
    p.add_argument("--subscribers", action="store_true")
    a = p.parse_args()

    if a.diagnose:
        from carouselforge.diagnose import main as diag
        sys.exit(diag())
    if a.probe_pillow:
        from carouselforge.health import probe_pillow
        r = probe_pillow()
        console.print(Panel(Text.from_markup(
            "[bold]Pillow[/bold]\n\n  " +
            (f"[green]ok[/green] {r.get('version','?')}" if r.get("ok") else f"[red]fail[/red] {r.get('error','')}")),
            border_style="green" if r.get("ok") else "red"))
        sys.exit(0 if r.get("ok") else 1)
    if a.probe_fonts:
        from carouselforge.health import probe_fonts
        r = probe_fonts()
        body = f"[bold]Fonts[/bold]\n\n  bold_found={len(r['bold_found'])}  regular_found={len(r['regular_found'])}\n"
        if r.get("bold_found"): body += f"  [green]bold:[/green] {', '.join(p.split('/')[-1] for p in r['bold_found'])}\n"
        if r.get("regular_found"): body += f"  [green]regular:[/green] {', '.join(p.split('/')[-1] for p in r['regular_found'])}\n"
        console.print(Panel(Text.from_markup(body.rstrip()), border_style="green" if r.get("ok") else "yellow"))
        sys.exit(0 if r.get("ok") else 1)
    if a.probe_inputs:
        from carouselforge.health import probe_inputs
        r = probe_inputs()
        body = (f"[bold]Inputs[/bold]\n\n"
                f"  cr_inputs:       {r['cr_inputs']}\n"
                f"  sn_outputs:      {r['sn_outputs']} (skips: {r['sn_skip_markers']})\n"
                f"  candidates:      {r['candidates']}\n"
                f"  cr_outputs (already built): {r['cr_outputs']}")
        console.print(Panel(Text.from_markup(body), border_style="green" if r.get("ok") else "red"))
        sys.exit(0 if r.get("ok") else 1)
    if a.carousels:
        from carouselforge.health import recent_carousels, carousel_outcome_summary
        for r in recent_carousels(a.carousels):
            color = "green" if r["outcome"] == "success" else "red"
            console.print(f"  [dim]{r['ts'][:19]}[/dim]  [{color}]{r['outcome']:<14s}[/{color}]  "
                          f"src={r['source']:<11s}  plat={r['platform']:<10s}  {r['slug']}")
        s = carousel_outcome_summary()
        console.print(f"\n  total={s['total']}  [green]success={s['success']}[/green]  "
                      f"[red]spec_invalid={s['spec_invalid']}[/red]  "
                      f"[red]no_slides={s['no_slides']}[/red]  "
                      f"[red]build_failed={s['build_failed']}[/red]")
        return
    if a.usage:
        from carouselforge.health import monthly_deliveries_per_email
        from carouselforge.subscribers import listing, PLANS
        usage = monthly_deliveries_per_email()
        if not usage: console.print("(no deliveries this month)"); return
        plan_for = {(s["email"].lower()): s.get("plan", "") for s in listing()["subscribers"]}
        for e, n in sorted(usage.items(), key=lambda kv: -kv[1]):
            plan = plan_for.get(e, "?")
            cap = PLANS.get(plan, {}).get("monthly_cap", 0)
            cap_s = "∞" if cap < 0 else (str(cap) if cap > 0 else "—")
            tag = ""
            if cap > 0:
                if n > cap: tag = " [red]OVER[/red]"
                elif n == cap: tag = " [yellow]AT[/yellow]"
            console.print(f"  {e}  {n} / {cap_s}{tag}  ({plan})")
        return
    if a.subscribers:
        from carouselforge.subscribers import listing
        out = listing()
        body = (f"[bold]Subscribers[/bold]\n\n"
                f"  Total: {out['total']}  Active: [green]{out['active']}[/green]  "
                f"Pending: [yellow]{out['pending']}[/yellow]  Churned: {out['churned']}\n"
                f"  MRR: [green]${out['mrr']:.0f}/mo[/green]  "
                f"One-time: [green]${out['one_time_collected']}[/green]")
        console.print(Panel(Text.from_markup(body), border_style="blue"))
        for s in out["subscribers"]:
            console.print(f"  [dim]{s.get('status','?'):>9s}[/dim]  {s.get('plan',''):<22s}  {s.get('email','')}")
        return
    if not paywall_prompt("carouselforge"): return
    while True:
        cycle()
        if a.interval <= 0: break
        time.sleep(a.interval * 60)


if __name__ == "__main__":
    main()
