#!/usr/bin/env python3
"""StoryForge autonomous loop — daily prompts + weekly consistency + bible orders."""
import time, argparse
from datetime import datetime
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from storyforge.tools import run_full_cycle
from paywall.agent_paywall import paywall_prompt

console = Console()


def cycle():
    console.print(Panel(
        Text.from_markup(
            f"[bold white]StoryForge Cycle[/bold white]\n"
            f"[dim]{datetime.now():%Y-%m-%d %H:%M:%S}[/dim]"),
        title="[bold blue]Wholesale Omniverse — StoryForge[/bold blue]",
        border_style="blue"))
    r = run_full_cycle()
    console.print(f"  [cyan]Daily prompts:[/cyan]      {r.get('prompts_sent', 0)}")
    console.print(f"  [cyan]Consistency reports:[/cyan] {r.get('consistency_sent', 0)}")
    console.print(f"  [green]Bibles delivered:[/green]   {r.get('bibles_delivered', 0)}")
    console.print(f"  [white]MRR:[/white]                ${r.get('mrr', 0):.0f}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=int, default=0)
    args = parser.parse_args()
    if not paywall_prompt("storyforge"):
        return
    while True:
        cycle()
        if args.interval <= 0:
            break
        time.sleep(args.interval * 60)


if __name__ == "__main__":
    main()
