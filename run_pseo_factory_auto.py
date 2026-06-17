#!/usr/bin/env python3
"""pSEO Factory autonomous loop — city-by-city landing page generator.

Usage:
  python3 run_pseo_factory_auto.py          # one cycle (build/refresh all pages)
  python3 run_pseo_factory_auto.py --loop   # rebuild weekly

Config: data/pseo_config.json (auto-created with ME defaults on first run)
Output: data/pseo_pages/{slug}.html + {slug}.md
"""
import argparse
import time
from datetime import datetime

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from pseo_factory.tools import run_full_cycle
from paywall.agent_paywall import paywall_prompt
from autonomous.self_healing import with_healing

console = Console()

AGENT_KEY = "pseo_factory"


@with_healing(AGENT_KEY)
def cycle():
    console.print(Panel(
        Text.from_markup(
            f"[bold white]pSEO Factory Cycle[/bold white]\n"
            f"[dim]{datetime.now():%Y-%m-%d %H:%M:%S}[/dim]"),
        title="[bold green]Wholesale Omniverse — pSEO Factory[/bold green]",
        border_style="green"))

    r = run_full_cycle()

    console.print(f"  [cyan]Pages built:[/cyan]    {r.get('pages_built', 0)}")
    console.print(f"  [cyan]Total pages:[/cyan]    {r.get('total_pages', 0)}")
    console.print(f"  [dim]Output dir:[/dim]       {r.get('output_dir', '')}")
    console.print(f"  [white]MRR:[/white]             ${r.get('mrr', 0):.0f}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--loop", action="store_true",
                        help="Rebuild pages weekly (every 7 days)")
    args = parser.parse_args()

    if not paywall_prompt(AGENT_KEY):
        return

    if args.loop:
        while True:
            cycle()
            time.sleep(86400 * 7)
    else:
        cycle()


if __name__ == "__main__":
    main()
