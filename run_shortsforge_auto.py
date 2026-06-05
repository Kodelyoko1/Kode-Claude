#!/usr/bin/env python3
"""ShortsForge — YouTube Shorts content architect. Revenue: AdSense, Substack premium, affiliates."""
import argparse, sys, time
from datetime import datetime
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from shortsforge.tools import run_full_cycle
from paywall.agent_paywall import paywall_prompt
from autonomous.self_healing import with_healing

console = Console()


@with_healing("shortsforge")
def cycle():
    console.print(Panel(Text.from_markup(
        f"[bold white]ShortsForge Cycle[/bold white]\n[dim]{datetime.now():%Y-%m-%d %H:%M:%S}[/dim]"),
        title="[bold blue]Wholesale Omniverse — ShortsForge[/bold blue]", border_style="blue"))
    r = run_full_cycle()
    console.print(f"  [white]MRR:[/white] ${r.get('mrr', 0):.0f}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--interval", type=int, default=0)
    p.add_argument("--diagnose", action="store_true")
    p.add_argument("--probe-inputs", action="store_true")
    p.add_argument("--briefs", type=int, default=0)
    a = p.parse_args()
    if a.diagnose:
        from shortsforge.diagnose import main as d
        sys.exit(d())
    if a.probe_inputs:
        from shortsforge.health import probe_inputs
        r = probe_inputs()
        print(f"transcripts={r['transcripts']}  briefs={r['briefs']}  newsletters={r['newsletters']}")
        print(f"transcripts_newest_age={r.get('transcripts_newest_age')}d")
        sys.exit(0 if r.get("ok") else 1)
    if a.briefs:
        from shortsforge.health import recent_briefs, brief_outcome_summary
        for r in recent_briefs(a.briefs):
            print(f"  {r['ts'][:19]}  {r['outcome']:<18s}  niche={r.get('niche','?'):<14s}  {r['slug']}")
        s = brief_outcome_summary()
        print(f"\n  {s}")
        return
    if not paywall_prompt("shortsforge"): return
    while True:
        cycle()
        if a.interval <= 0: break
        time.sleep(a.interval * 60)


if __name__ == "__main__":
    main()
