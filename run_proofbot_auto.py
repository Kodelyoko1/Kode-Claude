#!/usr/bin/env python3
"""ProofBot — proofreader + copyeditor. Per-page ($15) · Monthly 10-page ($39/mo) · Unlimited ($129/mo)."""
import argparse, sys, time
from datetime import datetime
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from proofbot.tools import run_full_cycle
from paywall.agent_paywall import paywall_prompt
from autonomous.self_healing import with_healing

console = Console()


@with_healing("proofbot")
def cycle():
    console.print(Panel(Text.from_markup(
        f"[bold white]ProofBot Cycle[/bold white]\n[dim]{datetime.now():%Y-%m-%d %H:%M:%S}[/dim]"),
        title="[bold blue]Wholesale Omniverse — ProofBot[/bold blue]", border_style="blue"))
    r = run_full_cycle()
    console.print(f"  [white]MRR:[/white] ${r.get('mrr', 0):.0f}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--interval", type=int, default=0)
    p.add_argument("--diagnose", action="store_true")
    p.add_argument("--probe-inputs", action="store_true")
    p.add_argument("--pages", type=int, default=0)
    p.add_argument("--subscribers", action="store_true")
    a = p.parse_args()
    if a.diagnose:
        from proofbot.diagnose import main as d; sys.exit(d())
    if a.probe_inputs:
        from proofbot.health import probe_inputs
        r = probe_inputs()
        print(f"pf_inputs={r['pf_inputs']}  pf_outputs={r['pf_outputs']}  newest_age={r.get('newest_age_days')}")
        sys.exit(0 if r.get("ok") else 1)
    if getattr(a, "pages", 0):
        from proofbot.health import recent_pages, page_outcome_summary
        for r in recent_pages(getattr(a, "pages")):
            print(f"  {r['ts'][:19]}  {r['outcome']:<14s}  {r['slug']}")
        s = page_outcome_summary()
        print(f"\n  {s}")
        return
    if a.subscribers:
        from proofbot.subscribers import listing
        out = listing()
        print(f"Total={out['total']}  Active={out['active']}  MRR=${out['mrr']:.0f}/mo  one-time=${out['one_time_collected']}")
        for s in out["subscribers"]:
            print(f"  {s.get('status','?'):>9s}  {s.get('plan',''):<22s}  {s.get('email','')}")
        return
    if not paywall_prompt("proofbot"): return
    while True:
        cycle()
        if a.interval <= 0: break
        time.sleep(a.interval * 60)


if __name__ == "__main__":
    main()
