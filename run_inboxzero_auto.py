#!/usr/bin/env python3
"""InboxZero autonomous loop — triage owner inbox + pitch leads."""
import time, argparse
from datetime import datetime
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from inboxzero.tools import run_full_cycle
from paywall.agent_paywall import paywall_prompt
from autonomous.self_healing import with_healing

console = Console()


@with_healing("inboxzero")
def cycle():
    console.print(Panel(
        Text.from_markup(
            f"[bold white]InboxZero Cycle[/bold white]\n"
            f"[dim]{datetime.now():%Y-%m-%d %H:%M:%S}[/dim]"),
        title="[bold blue]Wholesale Omniverse — InboxZero[/bold blue]",
        border_style="blue"))
    r = run_full_cycle()
    if r.get("skipped_reason"):
        console.print(f"  [yellow]Skipped:[/yellow] {r['skipped_reason']}")
    else:
        console.print(f"  [cyan]Inboxes triaged:[/cyan] {r.get('triaged_inboxes', 0)}")
        console.print(f"  [cyan]Unread scanned:[/cyan]  {r.get('scanned', 0)}")
    console.print(f"  [cyan]Lead pitches:[/cyan]    {r.get('outreach_sent', 0)}")
    console.print(f"  [white]MRR:[/white]             ${r.get('mrr', 0):.0f}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=int, default=0)
    args = parser.parse_args()
    if not paywall_prompt("inboxzero"):
        return
    while True:
        cycle()
        if args.interval <= 0:
            break
        time.sleep(args.interval * 60)


if __name__ == "__main__":
    main()
