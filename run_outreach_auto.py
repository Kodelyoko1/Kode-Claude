#!/usr/bin/env python3
"""
Outreach-as-a-Service automation — no API key required.
Runs prospecting campaigns for all retainer clients directly, then emails each client their report.
"""
import json
import time
import argparse
from datetime import datetime
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from outreach_service.tools import (
    get_outreach_clients,
    run_client_campaign,
    get_service_revenue,
    send_campaign_report_email,
    get_campaign_report,
)
from paywall.agent_paywall import paywall_prompt

console = Console()

RECORD_TYPES = ["tax_delinquent", "code_violations", "foreclosure", "probate", "vacant"]


def run_outreach_cycle(record_type: str = "tax_delinquent", max_prospects: int = 15, auto_email: bool = True):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    console.print(Panel(
        Text.from_markup(
            f"[bold white]Outreach Campaign Cycle[/bold white]\n"
            f"[dim]{now}[/dim]\n"
            f"[dim]Record type: {record_type} | Max prospects/market: {max_prospects} | Auto-email: {auto_email}[/dim]"
        ),
        title="[bold blue]Wholesale Omniverse — Outreach-as-a-Service[/bold blue]",
        border_style="blue",
    ))

    # Step 1: Get all active clients
    clients_data = get_outreach_clients(status="active")
    clients = clients_data.get("clients", [])

    if not clients:
        console.print("[yellow]No active retainer clients found. Register clients first.[/yellow]")
        console.print("[dim]Run: python3 outreach_service_main.py[/dim]")
        return

    console.print(f"\n[cyan]Active retainer clients:[/cyan] {len(clients)}")

    total_leads = 0
    total_emailed = 0
    campaign_results = []

    # Step 2: Run campaign for each client
    for c in clients:
        markets = c.get("target_markets", [])
        market_str = ", ".join(f"{m['city']} {m['state']}" for m in markets) or "no markets configured"
        console.print(f"\n[bold white]Running campaign for {c['name']}[/bold white] [{market_str}]")

        if not markets:
            console.print(f"  [yellow]Skipping — no markets configured for {c['name']}[/yellow]")
            continue

        result = run_client_campaign(
            client_id=c["client_id"],
            record_type=record_type,
            max_prospects=max_prospects,
            auto_email=auto_email,
        )

        if "error" in result:
            console.print(f"  [red]Error: {result['error']}[/red]")
            continue

        leads = result.get("total_leads_found", 0)
        emailed = result.get("total_emails_sent", 0)
        total_leads += leads
        total_emailed += emailed

        # Per-market breakdown
        for mkt in result.get("breakdown", []):
            console.print(
                f"  [cyan]{mkt['city']}, {mkt['state']}[/cyan] — "
                f"[white]{mkt['gov_records_leads']} gov records + {mkt['redfin_leads']} Redfin leads[/white], "
                f"[green]{mkt['emails_sent']} emails sent[/green]"
            )

        campaign_results.append({
            "client": c["name"],
            "leads": leads,
            "emailed": emailed,
            "campaign_id": result.get("campaign_id", ""),
        })

        # Step 3: Send client their results report
        console.print(f"  Sending results report to {c['email']}...")
        report_result = send_campaign_report_email(c["client_id"])
        if report_result.get("status") == "sent":
            console.print(f"  [green]Report emailed to {c['email']}[/green]")
        elif report_result.get("status") == "smtp_not_configured":
            console.print(f"  [yellow]SMTP not configured — report not sent (set SMTP_HOST, SMTP_USER, SMTP_PASS)[/yellow]")
        else:
            console.print(f"  [red]Report email failed: {report_result.get('error', 'unknown')}[/red]")

    # Step 4: Revenue report
    revenue = get_service_revenue()

    # Step 5: Campaign results table
    if campaign_results:
        results_table = Table(title="Campaign Results", border_style="green")
        results_table.add_column("Client", style="white")
        results_table.add_column("Leads Found", style="cyan")
        results_table.add_column("Emails Sent", style="green")
        results_table.add_column("Campaign ID", style="dim")
        for r in campaign_results:
            results_table.add_row(r["client"], str(r["leads"]), str(r["emailed"]), r["campaign_id"])
        console.print(results_table)

    # Step 6: Summary
    console.print(Panel(
        Text.from_markup(
            f"[bold green]Cycle Complete[/bold green]\n\n"
            f"  Clients served:      [white]{len(campaign_results)}[/white]\n"
            f"  Total leads found:   [white]{total_leads}[/white]\n"
            f"  Total emails sent:   [white]{total_emailed}[/white]\n"
            f"  MRR:                 [white]${revenue['mrr']:,.2f}/mo[/white]\n"
            f"  ARR:                 [white]${revenue['arr']:,.2f}/yr[/white]\n"
            f"  All-time leads:      [white]{revenue['total_leads_generated_all_time']}[/white]\n"
            f"  All-time emails:     [white]{revenue['total_emails_sent_all_time']}[/white]"
        ),
        title="[bold green]Summary[/bold green]",
        border_style="green",
    ))


def main():
    parser = argparse.ArgumentParser(description="Outreach-as-a-Service automation — no API key required")
    parser.add_argument("--record-type", default="tax_delinquent",
                        choices=RECORD_TYPES,
                        help="Type of leads to prospect for")
    parser.add_argument("--max-prospects", type=int, default=15,
                        help="Max leads per market per client")
    parser.add_argument("--no-email", action="store_true",
                        help="Skip auto-emailing sellers (find leads only)")
    parser.add_argument("--interval", type=int, default=0,
                        help="Repeat every N minutes (0 = run once)")
    args = parser.parse_args()

    if not paywall_prompt("outreach"):
        return

    run_count = 0
    while True:
        run_count += 1
        run_outreach_cycle(
            record_type=args.record_type,
            max_prospects=args.max_prospects,
            auto_email=not args.no_email,
        )

        if args.interval <= 0:
            break

        console.print(f"\n[dim]Next run in {args.interval} minutes. Ctrl+C to stop.[/dim]")
        try:
            time.sleep(args.interval * 60)
        except KeyboardInterrupt:
            console.print("\n[dim]Stopped.[/dim]")
            break


if __name__ == "__main__":
    main()
