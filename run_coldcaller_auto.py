#!/usr/bin/env python3
"""ColdCaller autonomous loop — daily Google Voice click-to-call queue."""
import argparse
import time
from datetime import datetime

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from coldcaller.tools import run_full_cycle
from paywall.agent_paywall import paywall_prompt
from autonomous.self_healing import with_healing

console = Console()


@with_healing("coldcaller")
def cycle():
    console.print(Panel(
        Text.from_markup(
            f"[bold white]ColdCaller Cycle[/bold white]\n"
            f"[dim]{datetime.now():%Y-%m-%d %H:%M:%S}[/dim]"),
        title="[bold blue]Wholesale Omniverse — ColdCaller[/bold blue]",
        border_style="blue"))
    r = run_full_cycle()
    console.print(f"  [cyan]Calls queued today:[/cyan]         {r.get('total', 0)}")
    console.print(f"  [dim]Skipped (already called):[/dim]   {r.get('skipped_called', 0)}")
    console.print(f"  [dim]Skipped (DNC list):[/dim]         {r.get('skipped_dnc', 0)}")
    console.print(f"  [dim]Skipped (toll-free/invalid):[/dim] {r.get('skipped_invalid', 0)}")
    console.print(f"  [green]Owner digest sent:[/green]          {r.get('digest_sent', 0)}")
    console.print(f"  [white]Queue HTML:[/white] {r.get('html_path', '')}")
    console.print(f"  [white]MRR:[/white] ${r.get('mrr', 0):.0f}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=int, default=0,
                        help="Loop every N minutes (0 = one-shot)")
    args = parser.parse_args()
    if not paywall_prompt("coldcaller"):
        return
    while True:
        cycle()
        if args.interval <= 0:
            break
        time.sleep(args.interval * 60)


if __name__ == "__main__":
    main()
