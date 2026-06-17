#!/usr/bin/env python3
"""Chrome Extension Forge autonomous runner — builds the Deal Analyzer extension.

Usage:
  python3 run_chrome_ext_forge_auto.py          # build/rebuild the extension package
  python3 run_chrome_ext_forge_auto.py --loop   # rebuild weekly

Output:
  data/cef_packages/src/           — loadable extension source
  data/cef_packages/deal-analyzer-ext-{version}.zip — distributable package
"""
import argparse
import time
from datetime import datetime

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from chrome_ext_forge.tools import run_full_cycle
from paywall.agent_paywall import paywall_prompt
from autonomous.self_healing import with_healing

console = Console()

AGENT_KEY = "chrome_ext_forge"


@with_healing(AGENT_KEY)
def cycle():
    console.print(Panel(
        Text.from_markup(
            f"[bold white]Chrome Extension Forge Cycle[/bold white]\n"
            f"[dim]{datetime.now():%Y-%m-%d %H:%M:%S}[/dim]"),
        title="[bold magenta]Wholesale Omniverse — Chrome Extension Forge[/bold magenta]",
        border_style="magenta"))

    r = run_full_cycle()

    console.print(f"  [cyan]Version:[/cyan]       {r.get('version', '—')}")
    console.print(f"  [cyan]Files built:[/cyan]   {r.get('files_built', 0)}")
    console.print(f"  [green]ZIP package:[/green]  {r.get('zip_path', '—')}")
    console.print(f"  [dim]Src dir:[/dim]       {r.get('src_dir', '—')}")
    console.print(f"  [white]MRR:[/white]           ${r.get('mrr', 0):.0f}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--loop", action="store_true",
                        help="Rebuild extension weekly (every 7 days)")
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
