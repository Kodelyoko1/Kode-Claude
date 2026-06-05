#!/usr/bin/env python3
"""BentoForge — link-in-bio landing pages. $19 one-time, $9/mo hosting, $49 white-label."""
import argparse, sys, time
from datetime import datetime
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from bentoforge.tools import run_full_cycle
from paywall.agent_paywall import paywall_prompt
from autonomous.self_healing import with_healing

console = Console()


@with_healing("bentoforge")
def cycle():
    console.print(Panel(Text.from_markup(
        f"[bold white]BentoForge Cycle[/bold white]\n[dim]{datetime.now():%Y-%m-%d %H:%M:%S}[/dim]"),
        title="[bold blue]Wholesale Omniverse — BentoForge[/bold blue]", border_style="blue"))
    r = run_full_cycle()
    console.print(f"  [cyan]Pages built:[/cyan]      {r.get('pages_produced', 0)}")
    console.print(f"  [green]Subs delivered:[/green]   {r.get('fulfillment_sent', 0)}")
    console.print(f"  [white]MRR:[/white]              ${r.get('mrr', 0):.0f}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--interval", type=int, default=0)
    p.add_argument("--diagnose", action="store_true")
    p.add_argument("--probe-inputs", action="store_true")
    p.add_argument("--pages", type=int, default=0)
    p.add_argument("--subscribers", action="store_true")
    a = p.parse_args()
    if a.diagnose:
        from bentoforge.diagnose import main as d; sys.exit(d())
    if a.probe_inputs:
        from bentoforge.health import probe_inputs
        r = probe_inputs()
        print(f"bf_inputs={r['bf_inputs']}  bf_outputs={r['bf_outputs']}  newest_age={r.get('newest_age_days')}")
        sys.exit(0 if r.get("ok") else 1)
    if a.pages:
        from bentoforge.health import recent_pages, page_outcome_summary
        for r in recent_pages(a.pages):
            print(f"  {r['ts'][:19]}  {r['outcome']:<14s}  theme={r.get('theme','?'):<8s}  {r['slug']}")
        s = page_outcome_summary()
        print(f"\n  total={s['total']}  success={s['success']}  spec_invalid={s['spec_invalid']}  no_links={s['no_links']}  build_failed={s['build_failed']}")
        return
    if a.subscribers:
        from bentoforge.subscribers import listing
        out = listing()
        print(f"Total={out['total']}  Active={out['active']}  MRR=${out['mrr']:.0f}/mo  one-time=${out['one_time_collected']}")
        for s in out["subscribers"]:
            print(f"  {s.get('status','?'):>9s}  {s.get('plan',''):<18s}  {s.get('email','')}")
        return
    if not paywall_prompt("bentoforge"): return
    while True:
        cycle()
        if a.interval <= 0: break
        time.sleep(a.interval * 60)


if __name__ == "__main__":
    main()
