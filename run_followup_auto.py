#!/usr/bin/env python3
"""
Seller follow-up automation — no API key required.
Sends follow-up emails to every lead due today and prints a report.
"""
import time
import argparse
from datetime import datetime
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from followup_agent.tools import (
    get_followup_summary, run_all_due_followups,
    get_hot_leads, get_sequence_stats,
)
from paywall.agent_paywall import paywall_prompt
from autonomous.self_healing import with_healing

console = Console()


@with_healing("followup")
def run_cycle():
    console.print(Panel(
        Text.from_markup(
            f"[bold white]Seller Follow-Up Cycle[/bold white]\n"
            f"[dim]{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}[/dim]"
        ),
        title="[bold blue]Wholesale Omniverse — Follow-Up Agent[/bold blue]",
        border_style="blue",
    ))

    # What's due today
    summary = get_followup_summary()
    console.print(f"\n[cyan]Active leads:[/cyan] {summary['total_active_leads']}")
    console.print(f"[cyan]Due for follow-up:[/cyan] {summary['due_for_followup']}")
    console.print(f"[cyan]No email on file:[/cyan] {summary['no_email_on_file']}")

    # Stage breakdown table
    stage_table = Table(title="Pipeline by Stage", border_style="blue")
    stage_table.add_column("Stage")
    stage_table.add_column("Leads")
    stage_table.add_column("Next Touch")
    schedule = summary.get("schedule_days", {})
    for stage, count in sorted(summary.get("by_stage", {}).items()):
        days = schedule.get(stage, "—")
        label = {0: "Initial sent", 1: "Day 3", 2: "Day 7", 3: "Day 14",
                 4: "Day 21", 5: "Day 30", 6: "Day 60"}.get(stage, f"Stage {stage}")
        stage_table.add_row(label, str(count), f"+{days} days" if isinstance(days, int) else "Complete")
    console.print(stage_table)

    # Send all due emails
    if summary["due_for_followup"] == 0:
        console.print("\n[dim]No follow-ups due today. Check back tomorrow.[/dim]")
    else:
        console.print(f"\n[yellow]Sending {summary['due_for_followup']} follow-up emails...[/yellow]")
        result = run_all_due_followups(limit=200)
        console.print(f"  [green]Sent:[/green]    {result['sent']}")
        console.print(f"  [dim]Skipped:[/dim] {result['skipped']}")
        if result["failed"]:
            console.print(f"  [red]Failed:[/red]  {result['failed']} (check SMTP config)")

    # Hot leads
    hot = get_hot_leads()
    if hot["count"]:
        console.print(f"\n[bold yellow]HOT LEADS — {hot['count']} sellers responded:[/bold yellow]")
        for lead in hot["hot_leads"][:5]:
            console.print(
                f"  [white]{lead.get('seller_name', 'Unknown')}[/white] — "
                f"{lead.get('address', '')} {lead.get('city', '')} — "
                f"[green]{lead.get('status', '')}[/green]"
            )
        console.print("  [bold]→ Call these leads TODAY.[/bold]")

    # Stats
    stats = get_sequence_stats()
    console.print(Panel(
        Text.from_markup(
            f"[bold green]Cycle Complete[/bold green]\n\n"
            f"  Follow-up emails sent (all time):  [white]{stats['total_followup_emails_sent']}[/white]\n"
            f"  Sellers responded:                 [white]{stats['sellers_responded']}[/white]\n"
            f"  In negotiation:                    [white]{stats['in_negotiation']}[/white]\n"
            f"  Under contract:                    [white]{stats['under_contract']}[/white]\n"
            f"  Response rate:                     [white]{stats['response_rate_pct']}%[/white]"
        ),
        title="[bold green]Summary[/bold green]",
        border_style="green",
    ))


def main():
    parser = argparse.ArgumentParser(description="Follow-up automation — no API key required")
    parser.add_argument("--interval", type=int, default=0, help="Repeat every N minutes (0 = once)")
    parser.add_argument("--diagnose", action="store_true",
                        help="Read-only preflight: SMTP + Twilio + lead reachability + distress queue")
    parser.add_argument("--sms", action="store_true",
                        help="Run the SMS sequence pass (drip every phone-reachable lead that's due)")
    parser.add_argument("--escalate", action="store_true",
                        help="Notify owner of new distress-tagged leads, mark them escalated")
    parser.add_argument("--sms-summary", action="store_true",
                        help="Print SMS queue summary without sending")
    args = parser.parse_args()

    if args.diagnose:
        from followup_agent import diagnose
        console.print("[bold]Followup agent preflight[/bold]\n")
        report = diagnose.run_diagnostics()
        diagnose.print_report(report)
        return

    if args.sms_summary:
        from followup_agent import sms
        import json as _json
        s = sms.get_sms_summary()
        console.print(_json.dumps({k: v for k, v in s.items() if k != "due_for_sms"}, indent=2))
        console.print(f"\nDue right now: {len(s['due_for_sms'])} (showing first 10)")
        for d in s["due_for_sms"][:10]:
            console.print(f"  {d['lead_id']}  stage {d['stage']}→{d['next_stage']}  "
                            f"{d['phone']}  {d['address']}")
        return

    if args.sms:
        from followup_agent import sms
        import json as _json
        if not paywall_prompt("followup"):
            return
        result = sms.run_all_due_sms()
        console.print(_json.dumps(result, indent=2))
        return

    if args.escalate:
        from followup_agent import escalation
        import json as _json
        if not paywall_prompt("followup"):
            return
        result = escalation.run_escalation()
        console.print(_json.dumps(result, indent=2))
        return

    if not paywall_prompt("followup"):
        return

    while True:
        run_cycle()
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
