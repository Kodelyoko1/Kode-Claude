"""
VideoEditor — autonomous video polish + reels cutter.

Usage
-----
# Owner (bypass paywall with AGENT_PASSWORD env var)
python3 run_videoeditor_auto.py                          # scan data/ve_inputs/
python3 run_videoeditor_auto.py --input /path/to/vid.mp4 # process one file
python3 run_videoeditor_auto.py --interval 5             # re-scan every 5 min

What it does
------------
1. Polishes the video: color grade, sharpen, audio denoise + EBU R128 normalize
2. Detects the most energetic 30-second and 60-second windows
3. Exports vertical 9:16 reels (1080×1920) with fade in/out for:
   • YouTube Shorts / Instagram Reels (30 s)
   • YouTube Reels / Instagram Reels (60 s)

Outputs land in  data/ve_outputs/{slug}/
Processed inputs move to  data/ve_processed/
Failed inputs move to     data/ve_failed/
"""

import argparse
import time

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from autonomous.self_healing import with_healing
from paywall.agent_paywall import paywall_prompt
from videoeditor.tools import run_full_cycle

console = Console()
AGENT_KEY = "videoeditor"


@with_healing(AGENT_KEY)
def cycle(input_path: str | None = None) -> None:
    console.print(
        Panel(
            "[bold cyan]VideoEditor[/bold cyan]  —  Polish + Reels Cutter\n"
            "[dim]Drop videos in  data/ve_inputs/  or pass --input <path>[/dim]",
            border_style="cyan",
        )
    )

    r = run_full_cycle(input_path=input_path)

    if r["processed"] == 0 and r["errors"] == 0:
        console.print("  [dim]No videos found.[/dim]")
        return

    console.print(
        f"  [green]Processed:[/green] {r['processed']}   "
        f"[red]Errors:[/red] {r['errors']}"
    )

    for meta in r.get("results", []):
        tbl = Table(show_header=False, box=None, padding=(0, 2))
        tbl.add_column(style="dim")
        tbl.add_column()

        tbl.add_row("slug", meta["slug"])
        tbl.add_row("source duration", f"{meta['source_duration_s']} s")
        tbl.add_row("source resolution", meta["source_resolution"])
        tbl.add_row("master", meta["master"])

        for reel in meta.get("reels", []):
            label = f"{reel['duration_s']}s reel (starts {reel['source_start_s']}s)"
            tbl.add_row(label, reel["file"])

        tbl.add_row("processing time", f"{meta['processing_time_s']} s")
        console.print(tbl)
        console.print()


def main() -> None:
    p = argparse.ArgumentParser(
        description="VideoEditor — autonomous video polish + reels cutter"
    )
    p.add_argument(
        "--input", "-i",
        metavar="PATH",
        help="Process a single video file (skips data/ve_inputs/ scan)",
    )
    p.add_argument(
        "--interval",
        type=int,
        default=0,
        metavar="MINUTES",
        help="Re-scan data/ve_inputs/ every N minutes (0 = run once and exit)",
    )
    a = p.parse_args()

    if not paywall_prompt(AGENT_KEY):
        return

    while True:
        cycle(input_path=a.input)
        if a.interval <= 0 or a.input:
            break
        console.print(f"  [dim]Next scan in {a.interval} min…[/dim]")
        time.sleep(a.interval * 60)


if __name__ == "__main__":
    main()
