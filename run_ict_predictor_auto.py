#!/usr/bin/env python3
"""
ICT Gold & Crude Prediction Agent — entry point.

Runs the ICT (Inner Circle Trader) killzone/liquidity-sweep/MSS/FVG
strategy against Gold (GC) and WTI Crude Oil (CL) and prints/logs a
prediction report per asset, formatted for MetaTrader 5. Every LONG/SHORT
signal is also turned into an MT5 order plan (see ict_predictor/mt5_execution.py).

Usage:
  python3 run_ict_predictor_auto.py             # full cycle (all configured assets)
  python3 run_ict_predictor_auto.py --asset GC  # single-asset scan
  python3 run_ict_predictor_auto.py --status    # show recent predictions

Pricing: $147/mo signal feed | $397/mo priority alerts | $997/yr white-label

MT5 order submission is DRY-RUN by default — every signal is priced,
sized, and logged as a "simulated" order but nothing is sent to a broker.
Set IP_MT5_LIVE=1 (with MT5_LOGIN/MT5_PASSWORD/MT5_SERVER in .env and a
running MT5 terminal) to actually place orders. Not financial advice —
futures trading carries substantial risk of loss.
"""
import argparse
import sys
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from paywall.agent_paywall import paywall_prompt
from autonomous.self_healing import run_with_healing
from autonomous import storage

console = Console()
AGENT_KEY = "ict_predictor"


def _banner():
    from ict_predictor import killzone, mt5_execution
    kz = killzone.current_killzone()
    mt5_mode = "[bold red]LIVE[/bold red]" if mt5_execution.LIVE else "[green]DRY-RUN[/green]"
    console.print(Panel(
        Text.from_markup(
            f"[bold white]ICT Gold & Crude Prediction Agent[/bold white]\n"
            f"[dim]{datetime.now():%Y-%m-%d %H:%M:%S}[/dim]\n"
            f"[dim]Active Killzone: {kz or 'None (outside all killzones)'}[/dim]\n"
            f"[dim]MT5 Order Mode:  {mt5_mode}[/dim]"
        ),
        title="[bold cyan]Wholesale Omniverse — ICT Predictor[/bold cyan]",
        border_style="cyan",
    ))


def cmd_status():
    log = storage.load("ip_predictions.json", [])
    if not log:
        console.print("[yellow]No predictions logged yet.[/yellow]")
        return

    console.print("\n[bold]Recent Predictions[/bold]")
    table = Table(show_header=True, box=None, padding=(0, 1))
    table.add_column("Time",       style="dim", width=19)
    table.add_column("Asset",      width=6)
    table.add_column("Direction",  width=10)
    table.add_column("Confidence", width=14)
    table.add_column("R:R",        width=6)
    table.add_column("Reason",     no_wrap=True)
    for rec in log[-20:]:
        direction = rec.get("direction", "")
        colour = "green" if direction == "LONG" else "red" if direction == "SHORT" else "dim"
        table.add_row(
            (rec.get("generated_at") or "")[:19],
            rec.get("asset", ""),
            f"[{colour}]{direction}[/{colour}]",
            rec.get("confidence", ""),
            str(rec.get("risk_reward", "")),
            rec.get("reason", "")[:60],
        )
    console.print(table)


def cmd_scan(asset: str):
    from ict_predictor.tools import analyze_asset
    from ict_predictor.report import format_report, format_mt5_status
    from ict_predictor import mt5_execution

    console.print(f"\n[dim]Scanning {asset}…[/dim]\n")
    pred = analyze_asset(asset)
    console.print(format_report(pred))
    if pred["direction"] in ("LONG", "SHORT"):
        console.print()
        console.print(format_mt5_status(mt5_execution.submit(pred)))


def main():
    parser = argparse.ArgumentParser(description="ICT Gold & Crude Prediction Agent")
    parser.add_argument("--asset", choices=["GC", "CL"],
                        help="Scan a single asset and print its report, then exit")
    parser.add_argument("--status", action="store_true",
                        help="Show recently logged predictions, then exit")
    args = parser.parse_args()

    if not paywall_prompt(AGENT_KEY):
        return

    _banner()

    if args.status:
        cmd_status()
        return

    if args.asset:
        cmd_scan(args.asset)
        return

    from ict_predictor.tools import run_full_cycle

    result = run_with_healing(AGENT_KEY, run_full_cycle)

    for pred in result["predictions"]:
        console.print()
        console.print(pred["report"])
        if pred.get("mt5_status_text"):
            console.print()
            console.print(pred["mt5_status_text"])

    console.print("\n[bold]Cycle Complete[/bold]")
    console.print(f"  Killzone:        {result.get('killzone') or 'None'}")
    console.print(f"  Trade signals:   {result.get('trade_signals', 0)}")
    console.print(f"  Orders sent:     {result.get('orders_submitted', 0)}")
    console.print(f"  Orders simulated:{result.get('orders_simulated', 0):>2}")
    console.print(f"  Digest:          [dim]{result.get('digest_path')}[/dim]")
    console.print(f"  Email sent:      {result.get('email_sent')}")


if __name__ == "__main__":
    main()
