#!/usr/bin/env python3
"""
Batman — autonomous self-healing agent fleet manager.
Run:    python3 run_batman_auto.py            (dry-run; reports only)
        BATMAN_LIVE=1 python3 run_batman_auto.py   (quarantines + restores corrupted JSON)
"""
from datetime import datetime

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from batman.tools import run_full_cycle
from paywall.agent_paywall import paywall_prompt

console = Console()


def main():
    if not paywall_prompt("batman"):
        return
    console.print(Panel(
        Text.from_markup(
            f"[bold white]Batman Cycle[/bold white]\n"
            f"[dim]{datetime.now():%Y-%m-%d %H:%M:%S}[/dim]"
        ),
        title="[bold red]Wholesale Omniverse — Batman[/bold red]",
        border_style="red",
    ))
    result = run_full_cycle()
    console.print(
        f"  Mode:                {result['mode']}\n"
        f"  Run logs scanned:    {result['logs_scanned']}\n"
        f"  Failure lines:       {result['failures']}\n"
        f"  Tracebacks:          {result['tracebacks']}\n"
        f"  JSON files checked:  {result['json_checked']}\n"
        f"  Corrupted files:     {result['corrupted']}\n"
        f"  Quarantined:         {result['quarantined']}\n"
        f"  Restored to default: {result['restored']}\n"
        f"  Stale agents:        {result['stale_agents']}\n"
        f"  Owner email sent:    {result['report_sent']}\n"
        f"  Report:              {result['report_path']}"
    )


if __name__ == "__main__":
    main()
