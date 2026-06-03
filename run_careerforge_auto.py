#!/usr/bin/env python3
"""CareerForge autonomous loop — free score + paid tailored resumes."""
import time, argparse
from datetime import datetime
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from careerforge.tools import run_full_cycle
from paywall.agent_paywall import paywall_prompt
from autonomous.self_healing import with_healing

console = Console()


@with_healing("careerforge")
def cycle():
    console.print(Panel(
        Text.from_markup(
            f"[bold white]CareerForge Cycle[/bold white]\n"
            f"[dim]{datetime.now():%Y-%m-%d %H:%M:%S}[/dim]"),
        title="[bold blue]Wholesale Omniverse — CareerForge[/bold blue]",
        border_style="blue"))
    r = run_full_cycle()
    console.print(f"  [cyan]Free scores sent:[/cyan] {r.get('outreach_sent', 0)}")
    console.print(f"  [green]Resumes shipped:[/green]  {r.get('fulfillment_sent', 0)}")
    console.print(f"  [white]Total revenue:[/white]    ${r.get('total_paid', 0):.0f}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=int, default=0)
    args = parser.parse_args()
    if not paywall_prompt("careerforge"):
        return
    while True:
        cycle()
        if args.interval <= 0:
            break
        time.sleep(args.interval * 60)


if __name__ == "__main__":
    main()
