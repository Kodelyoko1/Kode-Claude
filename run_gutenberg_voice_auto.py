#!/usr/bin/env python3
"""GutenbergVoice autonomous loop — produce scripts + deliver paid orders."""
import time, argparse
from datetime import datetime
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from gutenberg_voice.tools import run_full_cycle
from paywall.agent_paywall import paywall_prompt

console = Console()


def cycle():
    console.print(Panel(
        Text.from_markup(
            f"[bold white]GutenbergVoice Cycle[/bold white]\n"
            f"[dim]{datetime.now():%Y-%m-%d %H:%M:%S}[/dim]"),
        title="[bold blue]Wholesale Omniverse — GutenbergVoice[/bold blue]",
        border_style="blue"))
    r = run_full_cycle()
    console.print(f"  [cyan]Books produced:[/cyan]   {r.get('produced', 0)}")
    console.print(f"  [green]Orders delivered:[/green] {r.get('orders_delivered', 0)}")
    console.print(f"  [white]Total revenue:[/white]    ${r.get('total_paid', 0):.0f}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=int, default=0)
    args = parser.parse_args()
    if not paywall_prompt("gutenberg_voice"):
        return
    while True:
        cycle()
        if args.interval <= 0:
            break
        time.sleep(args.interval * 60)


if __name__ == "__main__":
    main()
