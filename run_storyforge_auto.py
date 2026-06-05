#!/usr/bin/env python3
"""StoryForge — writer's coaching agent. Daily prompts ($19/mo) · Weekly tracker ($49/mo) · Story bible ($197)."""
import argparse, sys, time
from datetime import datetime
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from storyforge.tools import run_full_cycle
from paywall.agent_paywall import paywall_prompt
from autonomous.self_healing import with_healing

console = Console()


@with_healing("storyforge")
def cycle():
    console.print(Panel(Text.from_markup(
        f"[bold white]StoryForge Cycle[/bold white]\n[dim]{datetime.now():%Y-%m-%d %H:%M:%S}[/dim]"),
        title="[bold blue]Wholesale Omniverse — StoryForge[/bold blue]", border_style="blue"))
    r = run_full_cycle()
    console.print(f"  [white]MRR:[/white] ${r.get('mrr', 0):.0f}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--interval", type=int, default=0)
    p.add_argument("--diagnose", action="store_true")
    p.add_argument("--probe-inputs", action="store_true")
    p.add_argument("--prompts", type=int, default=0)
    p.add_argument("--subscribers", action="store_true")
    a = p.parse_args()
    if a.diagnose:
        from storyforge.diagnose import main as d; sys.exit(d())
    if a.probe_inputs:
        from storyforge.health import probe_inputs
        r = probe_inputs()
        print(f"sf_inputs={r['sf_inputs']}  sf_outputs={r['sf_outputs']}  newest_age={r.get('newest_age_days')}")
        sys.exit(0 if r.get("ok") else 1)
    if getattr(a, "prompts", 0):
        from storyforge.health import recent_prompts, prompt_outcome_summary
        for r in recent_prompts(getattr(a, "prompts")):
            print(f"  {r['ts'][:19]}  {r['outcome']:<14s}  {r['slug']}")
        s = prompt_outcome_summary()
        print(f"\n  {s}")
        return
    if a.subscribers:
        from storyforge.subscribers import listing
        out = listing()
        print(f"Total={out['total']}  Active={out['active']}  MRR=${out['mrr']:.0f}/mo  one-time=${out['one_time_collected']}")
        for s in out["subscribers"]:
            print(f"  {s.get('status','?'):>9s}  {s.get('plan',''):<22s}  {s.get('email','')}")
        return
    if not paywall_prompt("storyforge"): return
    while True:
        cycle()
        if a.interval <= 0: break
        time.sleep(a.interval * 60)


if __name__ == "__main__":
    main()
