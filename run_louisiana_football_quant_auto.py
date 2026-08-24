#!/usr/bin/env python3
"""
Louisiana Football Quant Agent — entry point.

Scans licensed Louisiana sportsbook consensus odds (DraftKings/FanDuel/
BetMGM via The Odds API) for +EV bets and cross-book arbitrage, runs a
heuristic PrizePicks demon/goblin value scan, and reports Kalshi's open
CFTC-compliant macro markets (rates/CPI/weather — never sports) as a
separate, unrelated strategy sleeve.

Usage:
  python3 run_louisiana_football_quant_auto.py            # full cycle
  python3 run_louisiana_football_quant_auto.py --status    # recent hits
  python3 run_louisiana_football_quant_auto.py --scan-only # print report, don't log/email

Pricing: $197/mo signal feed (see paywall/agent_paywall.py)

IMPORTANT — execution model: this agent NEVER places bets on DraftKings,
FanDuel, BetMGM, or PrizePicks. None of those platforms offers a vendor API
for submitting a wager/entry into a personal account, and automating one via
browser/session scraping would violate each platform's terms of service —
so every sportsbook/PrizePicks output here is signal-only, for the owner (or
subscriber) to act on manually through their own licensed account. Kalshi
order submission (macro markets only) is dry-run by default; set
LQ_KALSHI_LIVE=1 with KALSHI_API_KEY_ID/KALSHI_PRIVATE_KEY_PATH to arm it.

Not financial advice. Sports wagering carries substantial risk of loss and
is subject to Louisiana gaming law and each platform's terms of service.
"""
import argparse
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from paywall.agent_paywall import paywall_prompt
from autonomous.self_healing import with_healing
from autonomous import storage

console = Console()
AGENT_KEY = "louisiana_football_quant"


def _banner():
    from louisiana_football_quant import kalshi_client
    kalshi_mode = "[bold red]LIVE[/bold red]" if kalshi_client.KalshiTrader().live else "[green]DRY-RUN[/green]"
    console.print(Panel(
        Text.from_markup(
            f"[bold white]Louisiana Football Quant Agent[/bold white]\n"
            f"[dim]{datetime.now():%Y-%m-%d %H:%M:%S}[/dim]\n"
            f"[dim]Sportsbooks/PrizePicks: signal-only (no execution path exists)[/dim]\n"
            f"[dim]Kalshi order mode: {kalshi_mode}[/dim]"
        ),
        title="[bold cyan]Wholesale Omniverse — LA Football Quant[/bold cyan]",
        border_style="cyan",
    ))


def cmd_status():
    log = storage.load("lq_predictions.json", [])
    if not log:
        console.print("[yellow]No cycles logged yet.[/yellow]")
        return
    console.print("\n[bold]Recent Cycles[/bold]")
    table = Table(show_header=True, box=None, padding=(0, 1))
    table.add_column("Time", style="dim", width=19)
    table.add_column("+EV bets", width=10)
    table.add_column("Arbs", width=6)
    table.add_column("PrizePicks flags", width=16)
    for rec in log[-20:]:
        table.add_row(
            (rec.get("generated_at") or "")[:19],
            str(len(rec.get("ev_bets", []))),
            str(len(rec.get("arbitrage", []))),
            str(len(rec.get("prizepicks_value", []))),
        )
    console.print(table)


@with_healing(AGENT_KEY)
def cycle():
    from louisiana_football_quant.tools import run_full_cycle
    result = run_full_cycle()
    console.print()
    console.print(result["report"])
    console.print("\n[bold]Cycle Complete[/bold]")
    console.print(f"  +EV bets found:    {len(result['ev_bets'])}")
    console.print(f"  Arbitrage found:   {len(result['arbitrage'])}")
    console.print(f"  PrizePicks flags:  {len(result['prizepicks_value'])}")
    console.print(f"  Kalshi markets:    {len(result['kalshi_markets'])}")
    console.print(f"  Digest:            [dim]{result.get('digest_path')}[/dim]")
    console.print(f"  Email sent:        {result.get('email_sent')}")


def main():
    parser = argparse.ArgumentParser(description="Louisiana Football Quant Agent")
    parser.add_argument("--status", action="store_true",
                        help="Show recently logged cycles, then exit")
    parser.add_argument("--scan-only", action="store_true",
                        help="Run one scan and print the report without logging/emailing")
    args = parser.parse_args()

    if not paywall_prompt(AGENT_KEY):
        return

    _banner()

    if args.status:
        cmd_status()
        return

    if args.scan_only:
        from louisiana_football_quant.tools import run_cycle
        console.print(run_cycle()["report"])
        return

    cycle()


if __name__ == "__main__":
    main()
