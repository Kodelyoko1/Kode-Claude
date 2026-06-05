#!/usr/bin/env python3
"""DomainScout — domain candidate generator. Per-list ($29) · Weekly lists ($79/mo) · Done-for-you ($297)."""
import argparse, sys, time
from datetime import datetime
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from domainscout.tools import run_full_cycle
from paywall.agent_paywall import paywall_prompt
from autonomous.self_healing import with_healing

console = Console()


@with_healing("domainscout")
def cycle():
    console.print(Panel(Text.from_markup(
        f"[bold white]DomainScout Cycle[/bold white]\n[dim]{datetime.now():%Y-%m-%d %H:%M:%S}[/dim]"),
        title="[bold blue]Wholesale Omniverse — DomainScout[/bold blue]", border_style="blue"))
    r = run_full_cycle()
    console.print(f"  [white]MRR:[/white] ${r.get('mrr', 0):.0f}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--interval", type=int, default=0)
    p.add_argument("--diagnose", action="store_true")
    p.add_argument("--probe-inputs", action="store_true")
    p.add_argument("--lists", type=int, default=0)
    p.add_argument("--subscribers", action="store_true")
    a = p.parse_args()
    if a.diagnose:
        from domainscout.diagnose import main as d; sys.exit(d())
    if a.probe_inputs:
        from domainscout.health import probe_inputs
        r = probe_inputs()
        print(f"dm_inputs={r['dm_inputs']}  dm_outputs={r['dm_outputs']}  newest_age={r.get('newest_age_days')}")
        sys.exit(0 if r.get("ok") else 1)
    if getattr(a, "lists", 0):
        from domainscout.health import recent_lists, list_outcome_summary
        for r in recent_lists(getattr(a, "lists")):
            print(f"  {r['ts'][:19]}  {r['outcome']:<14s}  {r['slug']}")
        s = list_outcome_summary()
        print(f"\n  {s}")
        return
    if a.subscribers:
        from domainscout.subscribers import listing
        out = listing()
        print(f"Total={out['total']}  Active={out['active']}  MRR=${out['mrr']:.0f}/mo  one-time=${out['one_time_collected']}")
        for s in out["subscribers"]:
            print(f"  {s.get('status','?'):>9s}  {s.get('plan',''):<22s}  {s.get('email','')}")
        return
    if not paywall_prompt("domainscout"): return
    while True:
        cycle()
        if a.interval <= 0: break
        time.sleep(a.interval * 60)


if __name__ == "__main__":
    main()
