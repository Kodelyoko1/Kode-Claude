#!/usr/bin/env python3
"""
PolyMarket Weather Trading Agent — entry point.

Usage:
  python3 run_polymarket_weather_auto.py               # full agent cycle (dry-run)
  python3 run_polymarket_weather_auto.py --live        # enable real order placement
  python3 run_polymarket_weather_auto.py --backtest    # run & print backtest report
  python3 run_polymarket_weather_auto.py --train       # force model retraining
  python3 run_polymarket_weather_auto.py --refresh     # force weather data refresh
  python3 run_polymarket_weather_auto.py --status      # show portfolio + risk state
  python3 run_polymarket_weather_auto.py --opportunities  # scan & print live opps only

Pricing: $97/mo signal feed | $297/mo live trading | $997/yr white-label
"""
import argparse
import json
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


def _banner():
    console.print(Panel(
        Text.from_markup(
            f"[bold white]PolyMarket Weather Trading Agent[/bold white]\n"
            f"[dim]{datetime.now():%Y-%m-%d %H:%M:%S}[/dim]\n"
            f"[dim]Mode: {'LIVE' if os.getenv('PW_LIVE_TRADING') == '1' else 'DRY-RUN'}[/dim]"
        ),
        title="[bold cyan]Wholesale Omniverse — PolyMarket Weather[/bold cyan]",
        border_style="cyan",
    ))


def cmd_status():
    from polymarket_weather.risk import RiskManager
    from polymarket_weather.agent import load_trade_log

    risk = RiskManager(
        starting_bankroll=float(os.getenv("PW_BANKROLL", "1000")),
    )
    s = risk.status_dict()

    console.print("\n[bold]Risk / Portfolio Status[/bold]")
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Key",   style="dim")
    table.add_column("Value", style="bold")
    for k, v in s.items():
        colour = "red" if k == "halted" and v else "green"
        table.add_row(k, f"[{colour}]{v}[/{colour}]")
    console.print(table)

    trades = load_trade_log(20)
    if trades:
        console.print("\n[bold]Recent Trade Log[/bold]")
        t = Table(show_header=True, box=None, padding=(0, 1))
        t.add_column("Time",     style="dim", width=19)
        t.add_column("Side",     style="bold", width=4)
        t.add_column("Edge",     width=6)
        t.add_column("Approved", width=8)
        t.add_column("Question", no_wrap=True)
        for rec in trades[-10:]:
            approved_str = "[green]yes[/green]" if rec.get("approved") else "[red]no[/red]"
            t.add_row(
                rec.get("timestamp", "")[:19],
                rec.get("side", ""),
                f"{rec.get('edge', 0):.3f}",
                approved_str,
                rec.get("question", "")[:60],
            )
        console.print(t)


def cmd_opportunities():
    from polymarket_weather.agent import WeatherTradingAgent

    console.print("\n[dim]Scanning PolyMarket weather markets…[/dim]")
    agent = WeatherTradingAgent(
        min_edge         = float(os.getenv("PW_MIN_EDGE", "0.07")),
        min_liquidity    = float(os.getenv("PW_MIN_LIQUIDITY", "500")),
        bankroll         = float(os.getenv("PW_BANKROLL", "1000")),
    )
    opps = agent.scan_opportunities()

    if not opps:
        console.print("[yellow]No opportunities found above edge threshold.[/yellow]")
        return

    for rank, o in enumerate(opps, 1):
        d = o.to_dict()
        console.print(
            f"  [cyan]{rank:>2}.[/cyan] [{('green' if d['side']=='YES' else 'red')}]{d['side']}[/] "
            f"edge=[bold]{d['edge']:.3f}[/bold]  "
            f"model=[green]{d['model_prob']:.3f}[/green]  "
            f"mkt=[yellow]{d['market_price']:.3f}[/yellow]  "
            f"ev=[magenta]{d['ev']:+.4f}[/magenta]  "
            f"[dim]{d['city']}[/dim]\n"
            f"      [dim]{d['question'][:80]}[/dim]"
        )


def cmd_backtest():
    from polymarket_weather.tools import run_backtest_quick

    console.print("\n[dim]Running backtest on historical data…[/dim]")
    result = run_backtest_quick()

    if "error" in result:
        console.print(f"[red]Backtest error:[/red] {result['error']}")
        return

    console.print("\n[bold]Backtest Results[/bold]")
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Metric", style="dim")
    table.add_column("Value",  style="bold")
    skip_keys = {"report_path"}
    for k, v in result.items():
        if k in skip_keys:
            continue
        colour = "green" if isinstance(v, (int, float)) and v > 0 else "white"
        table.add_row(k, f"[{colour}]{v}[/{colour}]")
    console.print(table)

    if result.get("report_path"):
        console.print(f"\n  Full report: [dim]{result['report_path']}[/dim]")


def cmd_train():
    from polymarket_weather.tools import refresh_data, retrain_models

    console.print("\n[dim]Refreshing weather data…[/dim]")
    data_result = refresh_data(force=True)
    console.print(f"  Weather records: {data_result.get('weather_records', 0)}")
    console.print(f"  Markets found:   {data_result.get('markets_found', 0)}")

    console.print("\n[dim]Training models…[/dim]")
    train_result = retrain_models(force=True)
    table = Table(show_header=True, box=None, padding=(0, 2))
    table.add_column("Event Type",    style="dim")
    table.add_column("Accuracy",      style="bold")
    table.add_column("Brier Score",   style="cyan")
    table.add_column("Brier Skill",   style="green")
    table.add_column("N samples",     style="dim")
    for event, m in train_result.items():
        if "error" in m:
            table.add_row(event, "[red]error[/red]", m.get("error", ""), "", "")
        else:
            skill_colour = "green" if m.get("brier_skill", 0) > 0 else "red"
            table.add_row(
                event,
                f"{m.get('accuracy', 0):.4f}",
                f"{m.get('brier_score', 0):.4f}",
                f"[{skill_colour}]{m.get('brier_skill', 0):.4f}[/{skill_colour}]",
                str(m.get("n", 0)),
            )
    console.print(table)


def cmd_refresh():
    from polymarket_weather.tools import refresh_data

    console.print("\n[dim]Force-refreshing weather and market data…[/dim]")
    result = refresh_data(force=True)
    console.print(f"  Cities processed:  {result.get('weather_cities', 0)}")
    console.print(f"  Weather records:   {result.get('weather_records', 0)}")
    console.print(f"  Markets found:     {result.get('markets_found', 0)}")
    console.print(f"  Price histories:   {result.get('price_histories', 0)}")


def main():
    parser = argparse.ArgumentParser(
        description="PolyMarket Weather Trading Agent"
    )
    parser.add_argument("--live",          action="store_true",
                        help="Enable real order placement (overrides PW_LIVE_TRADING=1)")
    parser.add_argument("--backtest",      action="store_true",
                        help="Run backtest and print results, then exit")
    parser.add_argument("--train",         action="store_true",
                        help="Force data refresh + model retraining, then exit")
    parser.add_argument("--refresh",       action="store_true",
                        help="Force weather + market data refresh, then exit")
    parser.add_argument("--status",        action="store_true",
                        help="Show portfolio status and recent trades, then exit")
    parser.add_argument("--opportunities", action="store_true",
                        help="Scan live markets and print opportunities, then exit")
    args = parser.parse_args()

    if args.live:
        os.environ["PW_LIVE_TRADING"] = "1"

    if not paywall_prompt("polymarket_weather"):
        return

    _banner()

    if args.status:
        cmd_status()
        return

    if args.opportunities:
        cmd_opportunities()
        return

    if args.backtest:
        cmd_backtest()
        return

    if args.train:
        cmd_train()
        return

    if args.refresh:
        cmd_refresh()
        return

    # Default: full autonomous cycle
    from polymarket_weather.tools import run_full_cycle

    result = run_with_healing(AGENT_KEY, run_full_cycle)

    console.print("\n[bold]Cycle Complete[/bold]")
    console.print(f"  Opportunities found:  {result.get('opportunities', 0)}")
    console.print(f"  Trades placed:        {result.get('trades_placed', 0)}")
    console.print(f"  Data refreshed:       {result.get('data_refreshed', False)}")
    console.print(f"  Models retrained:     {result.get('models_retrained', False)}")
    console.print(f"  Live trading:         {result.get('live_trading', False)}")

    risk = result.get("risk", {})
    if risk:
        pnl_colour = "green" if risk.get("total_pnl", 0) >= 0 else "red"
        console.print(f"  Bankroll:             ${risk.get('bankroll', 0):.2f}")
        console.print(
            f"  Total P&L:            [{pnl_colour}]${risk.get('total_pnl', 0):+.2f}[/{pnl_colour}]"
        )
        if risk.get("halted"):
            console.print(
                f"  [red bold]TRADING HALTED:[/red bold] {risk.get('halt_reason', '')}"
            )

    if result.get("digest_path"):
        console.print(f"  Digest:               [dim]{result['digest_path']}[/dim]")


AGENT_KEY = "polymarket_weather"

if __name__ == "__main__":
    main()
