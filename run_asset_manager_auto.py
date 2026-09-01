#!/usr/bin/env python3
"""
AI Asset Manager Agent — entry point.

Usage:
  python3 run_asset_manager_auto.py                # one autonomous cycle (paper trading by default)
  python3 run_asset_manager_auto.py --loop          # run continuously on AM_CYCLE_INTERVAL_SECONDS
  python3 run_asset_manager_auto.py --status        # show portfolio + recent orders, no trading
  python3 run_asset_manager_auto.py --history        # show recent order/signal history

Safety: paper trading (dry-run simulation) is ON by default. Real orders are
only ever placed when AM_PAPER_TRADING=0 is set in .env AND AM_CONNECTOR is
set to a live-capable connector (ccxt or yfinance/Alpaca) — see
asset_manager/README.md.

Pricing: $147/mo signal + paper-trading feed | $397/mo live execution tier
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

from paywall.agent_paywall import paywall_prompt
from autonomous.self_healing import run_with_healing

console = Console()
AGENT_KEY = "asset_manager"


def _banner(mode: str):
    console.print(Panel(
        Text.from_markup(
            f"[bold white]AI Asset Manager Agent[/bold white]\n"
            f"[dim]{datetime.now():%Y-%m-%d %H:%M:%S}[/dim]\n"
            f"[dim]Mode: {mode}[/dim]"
        ),
        title="[bold cyan]Wholesale Omniverse — AI Asset Manager[/bold cyan]",
        border_style="cyan",
    ))


def cmd_status():
    from asset_manager import storage
    from asset_manager.config.settings import get_settings

    settings = get_settings()
    portfolio = storage.load_latest_portfolio()

    console.print("\n[bold]Portfolio Status[/bold]")
    if portfolio is None:
        console.print("  [dim]No portfolio snapshot yet — run a cycle first.[/dim]")
    else:
        table = Table(show_header=False, box=None, padding=(0, 2))
        table.add_column("Key", style="dim")
        table.add_column("Value", style="bold")
        table.add_row("Total equity", f"${portfolio.total_equity:,.2f}")
        table.add_row("Cash", f"${portfolio.cash:,.2f}")
        table.add_row("Positions", str(len(portfolio.positions)))
        daily = portfolio.daily_pnl_pct()
        colour = "green" if (daily or 0) >= 0 else "red"
        table.add_row("Daily P&L", f"[{colour}]{daily:+.2%}[/{colour}]" if daily is not None else "n/a")
        console.print(table)

        if portfolio.positions:
            pt = Table(show_header=True, box=None, padding=(0, 1))
            pt.add_column("Symbol")
            pt.add_column("Qty", justify="right")
            pt.add_column("Avg Entry", justify="right")
            pt.add_column("Price", justify="right")
            pt.add_column("Unrealized P&L", justify="right")
            for symbol, pos in portfolio.positions.items():
                colour = "green" if pos.unrealized_pnl >= 0 else "red"
                pt.add_row(
                    symbol, f"{pos.quantity:.6g}", f"${pos.avg_entry_price:.4f}",
                    f"${pos.current_price:.4f}",
                    f"[{colour}]${pos.unrealized_pnl:+,.2f} ({pos.unrealized_pnl_pct:+.1%})[/{colour}]",
                )
            console.print(pt)

    console.print(f"\n  Connector: [bold]{settings.connector}[/bold]  "
                   f"Strategy: [bold]{settings.strategy}[/bold]  "
                   f"Mode: [bold]{'PAPER' if settings.paper_trading else 'LIVE'}[/bold]")


def cmd_history():
    from asset_manager import storage

    orders = storage.load_recent_orders(20)
    console.print("\n[bold]Recent Orders[/bold]")
    if not orders:
        console.print("  [dim]No orders yet.[/dim]")
    else:
        t = Table(show_header=True, box=None, padding=(0, 1))
        for col in ("Time", "Side", "Symbol", "Qty", "Status", "Fill Price"):
            t.add_column(col)
        for o in orders:
            t.add_row(
                o["created_at"][:19], o["side"], o["symbol"],
                f"{o['quantity']:.6g}", o["status"],
                f"${o['filled_price']:.4f}" if o["filled_price"] else "-",
            )
        console.print(t)


def main():
    parser = argparse.ArgumentParser(description="AI Asset Manager Agent")
    parser.add_argument("--loop", action="store_true", help="run continuously instead of one cycle")
    parser.add_argument("--status", action="store_true", help="show portfolio status and exit")
    parser.add_argument("--history", action="store_true", help="show recent order history and exit")
    parser.add_argument("--live", action="store_true", help="force live trading for this run (overrides AM_PAPER_TRADING)")
    args = parser.parse_args()

    if args.live:
        os.environ["AM_PAPER_TRADING"] = "0"

    if not paywall_prompt(AGENT_KEY):
        return

    from asset_manager.config.settings import get_settings
    settings = get_settings()
    _banner("LIVE" if not settings.paper_trading else "PAPER (dry-run)")

    if args.status:
        cmd_status()
        return
    if args.history:
        cmd_history()
        return

    from asset_manager.tools import run_full_cycle
    from asset_manager.agent import AssetManagerAgent

    if args.loop:
        console.print(f"  Running continuously every {settings.cycle_interval_seconds}s (ctrl-C to stop)\n")
        AssetManagerAgent().run_forever()
        return

    result = run_with_healing(AGENT_KEY, run_full_cycle)

    console.print("\n[bold]Cycle Complete[/bold]")
    console.print(f"  Signals evaluated:  {result.get('signals', 0)}")
    console.print(f"  Orders placed:      {result.get('orders_placed', 0)}")
    console.print(f"  Orders rejected:    {result.get('orders_rejected', 0)}")
    console.print(f"  Total equity:       ${result.get('total_equity', 0):,.2f}")
    daily = result.get("daily_pnl_pct")
    if daily is not None:
        colour = "green" if daily >= 0 else "red"
        console.print(f"  Daily P&L:          [{colour}]{daily:+.2%}[/{colour}]")
    if result.get("halted"):
        console.print("  [red bold]TRADING HALTED:[/red bold] daily loss limit breached")


if __name__ == "__main__":
    main()
