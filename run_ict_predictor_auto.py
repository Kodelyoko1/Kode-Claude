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
import os
import sys
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

# Load .env before anything reads os.environ. The rest of the fleet relies on
# the shell doing this (`export $(grep -v '^#' .env | xargs) && python3 ...`),
# but this agent has to run on Windows for MetaTrader5, where that idiom does
# not exist — so it loads its own config. Must happen before the ict_predictor
# modules are imported, since several read their env knobs at import time.
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass  # python-dotenv is in requirements.txt; fall back to a pre-set environment

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


def cmd_doctor():
    from ict_predictor.doctor import run_diagnostics, summarize

    console.print("\n[bold]Preflight Diagnostics[/bold]\n")
    checks = run_diagnostics()

    icons = {"ok": "[green]✓[/green]", "warn": "[yellow]![/yellow]",
             "fail": "[red]✗[/red]", "info": "[dim]·[/dim]"}
    for c in checks:
        console.print(f"  {icons[c['status']]} [bold]{c['name']}[/bold]: {c['detail']}")
        if c["fix"]:
            console.print(f"      [dim]→ {c['fix']}[/dim]")

    fails, warns = summarize(checks)
    console.print()
    if fails:
        console.print(f"[red bold]{fails} blocking issue(s)[/red bold]"
                      f"{f', {warns} warning(s)' if warns else ''} — "
                      f"fix the ✗ items above.")
    elif warns:
        console.print(f"[yellow]{warns} warning(s)[/yellow] — usable, but read the ! items.")
    else:
        console.print("[green bold]All checks passed.[/green bold] "
                      "Ready to run a cycle.")


def cmd_backtest(asset: str, bars: int):
    from ict_predictor.data_feed import get_history, FeedError
    from ict_predictor.backtest import run_backtest, format_report
    from datetime import datetime, timezone

    console.print(f"\n[dim]Loading history for {asset}…[/dim]")
    try:
        htf = get_history(asset, "15m", bars)
        ltf = get_history(asset, "5m", bars * 3)
    except FeedError as exc:
        console.print(f"[red]Cannot backtest:[/red] {exc}")
        return

    span = (f"{datetime.fromtimestamp(htf[0]['t'], tz=timezone.utc):%Y-%m-%d} → "
            f"{datetime.fromtimestamp(htf[-1]['t'], tz=timezone.utc):%Y-%m-%d}")
    console.print(f"[dim]{len(htf):,} 15M bars, {len(ltf):,} 5M bars  ({span})[/dim]")
    console.print("[dim]Replaying bar-by-bar (no look-ahead)…[/dim]\n")

    def progress(i, total):
        console.print(f"[dim]  {i:,}/{total:,} bars…[/dim]", end="\r")

    result = run_backtest(htf, ltf, asset=asset, progress=progress)
    console.print(" " * 40, end="\r")
    console.print(format_report(result, asset, span))

    out = ROOT / "data" / "ip_reports" / f"backtest_{asset}_{datetime.now(timezone.utc):%Y%m%d}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("```text\n" + format_report(result, asset, span) + "\n```\n",
                   encoding="utf-8")
    console.print(f"\n  Saved: [dim]{out}[/dim]")


def cmd_sweep(asset: str, bars: int):
    from ict_predictor.data_feed import get_history, FeedError
    from ict_predictor.sweep import run_sweep, format_report
    from datetime import datetime, timezone

    console.print(f"\n[dim]Loading history for {asset}…[/dim]")
    try:
        htf = get_history(asset, "15m", bars)
        ltf = get_history(asset, "5m", bars * 3)
    except FeedError as exc:
        console.print(f"[red]Cannot sweep:[/red] {exc}")
        return

    span = (f"{datetime.fromtimestamp(htf[0]['t'], tz=timezone.utc):%Y-%m-%d} → "
            f"{datetime.fromtimestamp(htf[-1]['t'], tz=timezone.utc):%Y-%m-%d}")
    console.print(f"[dim]{len(htf):,} 15M bars, {len(ltf):,} 5M bars  ({span})[/dim]")
    console.print("[dim]Sweeping the parameter grid — this runs a full backtest per "
                  "combination, twice (in/out of sample). Expect several minutes.[/dim]\n")

    def progress(n, total, params):
        console.print(f"[dim]  {n}/{total}  disp={params['displacement_mult']} "
                      f"look={params['sweep_lookback']} rr={params['min_rr']}   [/dim]",
                      end="\r")

    result = run_sweep(htf, ltf, asset=asset, progress=progress)
    console.print(" " * 70, end="\r")
    console.print(format_report(result, asset, span))

    out = ROOT / "data" / "ip_reports" / f"sweep_{asset}_{datetime.now(timezone.utc):%Y%m%d}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("```text\n" + format_report(result, asset, span) + "\n```\n",
                   encoding="utf-8")
    console.print(f"\n  Saved: [dim]{out}[/dim]")


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
    parser.add_argument("--doctor", action="store_true",
                        help="Run preflight checks (MT5, credentials, symbols, feed), then exit")
    parser.add_argument("--backtest", action="store_true",
                        help="Replay history through the strategy and report its edge, then exit")
    parser.add_argument("--sweep", action="store_true",
                        help="Parameter sensitivity sweep with in/out-of-sample split, then exit")
    parser.add_argument("--bars", type=int, default=5000,
                        help="15M bars of history for --backtest (default 5000, ~2.5 months)")
    args = parser.parse_args()

    if not paywall_prompt(AGENT_KEY):
        return

    _banner()

    if args.doctor:
        cmd_doctor()
        return

    if args.backtest or args.sweep:
        assets = [a.strip().upper() for a in
                  os.getenv("IP_ASSETS", "GC,CL").split(",") if a.strip()]
        target = args.asset or assets[0]
        if args.sweep:
            cmd_sweep(target, args.bars)
        else:
            cmd_backtest(target, args.bars)
        return

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
