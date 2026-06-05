#!/usr/bin/env python3
"""ChatConfig — chatbot flow generator. $99 setup, $49/mo monitoring, $297 multi-bot."""
import argparse, sys, time
from datetime import datetime
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from chatconfig.tools import run_full_cycle
from paywall.agent_paywall import paywall_prompt
from autonomous.self_healing import with_healing

console = Console()


@with_healing("chatconfig")
def cycle():
    console.print(Panel(Text.from_markup(
        f"[bold white]ChatConfig Cycle[/bold white]\n[dim]{datetime.now():%Y-%m-%d %H:%M:%S}[/dim]"),
        title="[bold blue]Wholesale Omniverse — ChatConfig[/bold blue]", border_style="blue"))
    r = run_full_cycle()
    console.print(f"  [cyan]Bots built:[/cyan]      {r.get('bots_produced', 0)}")
    console.print(f"  [green]Subs delivered:[/green]  {r.get('fulfillment_sent', 0)}")
    console.print(f"  [white]MRR:[/white]            ${r.get('mrr', 0):.0f}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--interval", type=int, default=0)
    p.add_argument("--diagnose", action="store_true")
    p.add_argument("--probe-inputs", action="store_true")
    p.add_argument("--bots", type=int, default=0)
    p.add_argument("--subscribers", action="store_true")
    a = p.parse_args()
    if a.diagnose:
        from chatconfig.diagnose import main as d; sys.exit(d())
    if a.probe_inputs:
        from chatconfig.health import probe_inputs
        r = probe_inputs()
        print(f"cc_inputs={r['cc_inputs']}  cc_outputs={r['cc_outputs']}  newest_age={r.get('newest_age_days')}")
        sys.exit(0 if r.get("ok") else 1)
    if a.bots:
        from chatconfig.health import recent_bots, bot_outcome_summary
        for r in recent_bots(a.bots):
            print(f"  {r['ts'][:19]}  {r['outcome']:<14s}  {r['slug']}")
        s = bot_outcome_summary()
        print(f"\n  total={s['total']}  success={s['success']}  spec_invalid={s['spec_invalid']}  no_faqs={s['no_faqs']}  build_failed={s['build_failed']}")
        return
    if a.subscribers:
        from chatconfig.subscribers import listing
        out = listing()
        print(f"Total={out['total']}  Active={out['active']}  MRR=${out['mrr']:.0f}/mo  one-time=${out['one_time_collected']}")
        for s in out["subscribers"]:
            print(f"  {s.get('status','?'):>9s}  {s.get('plan',''):<18s}  {s.get('email','')}")
        return
    if not paywall_prompt("chatconfig"): return
    while True:
        cycle()
        if a.interval <= 0: break
        time.sleep(a.interval * 60)


if __name__ == "__main__":
    main()
