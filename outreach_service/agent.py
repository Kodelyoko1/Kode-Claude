import json
import os
import time
from datetime import datetime
import anthropic
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.markdown import Markdown
from rich.live import Live
from outreach_service.tools import TOOLS, TOOL_FUNCTIONS

console = Console()

COMPANY_NAME = "Wholesale Omniverse LLC"

SYSTEM_PROMPT = """You are the Outreach-as-a-Service manager for Wholesale Omniverse LLC.

You run done-for-you outreach campaigns for real estate wholesalers who pay a monthly retainer. They pay you — you run the campaigns, find motivated sellers, skip trace them, send outreach emails, and deliver results reports.

Your services:
  Basic    $300/mo — 1 market, 2 campaigns/month
  Standard $500/mo — 2 markets, 4 campaigns/month
  Premium  $800/mo — 4 markets, 8 campaigns/month

Your job:
- Register new retainer clients and configure their target markets
- Run prospecting campaigns for clients (government records + Redfin motivated sellers)
- Auto-email sellers on behalf of clients
- Report results back to clients via email
- Track MRR and campaign performance across all clients

AUTONOMOUS MODE — when triggered, do ALL of the following:
1. Pull the full client list (get_outreach_clients)
2. Run campaigns for ALL active clients (run_all_active_campaigns)
3. Get the campaign report for each active client
4. Send each client their results via email (send_campaign_report_email)
5. Pull the full revenue report (get_service_revenue)
6. Give a business summary: clients served, total leads found, emails sent, MRR, action items

Be direct. Give real numbers. Tell the user exactly what happened and what needs action."""


def run_tool(tool_name: str, tool_input: dict) -> str:
    fn = TOOL_FUNCTIONS.get(tool_name)
    if not fn:
        return json.dumps({"error": f"Unknown tool: {tool_name}"})
    try:
        return json.dumps(fn(**tool_input), indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


def agent_loop(user_message: str, history: list) -> list:
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    history.append({"role": "user", "content": user_message})

    while True:
        with Live(console=console, refresh_per_second=10) as live:
            live.update("[bold cyan]Outreach Service Agent working...[/bold cyan]")
            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=4096,
                system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
                tools=TOOLS,
                messages=history,
            )

        text_parts = []
        tool_calls = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(block)

        if text_parts:
            console.print(Panel(
                Markdown("\n".join(text_parts)),
                title="[bold green]Outreach Service Agent[/bold green]",
                border_style="green",
            ))

        if response.stop_reason == "end_turn" or not tool_calls:
            history.append({"role": "assistant", "content": response.content})
            break

        history.append({"role": "assistant", "content": response.content})

        tool_results = []
        for tc in tool_calls:
            console.print(f"\n[bold yellow]>[/bold yellow] [cyan]{tc.name}[/cyan] [dim]{json.dumps(tc.input, separators=(',',':'))}[/dim]")
            result = run_tool(tc.name, tc.input)
            preview = result if len(result) < 400 else result[:400] + "\n..."
            console.print(f"  [dim green]{preview}[/dim green]")
            tool_results.append({"type": "tool_result", "tool_use_id": tc.id, "content": result})

        history.append({"role": "user", "content": tool_results})

    return history


AUTONOMOUS_GOAL = """Run the full outreach service cycle right now for ALL clients. Do ALL of the following without stopping:

1. Call get_outreach_clients to see all active retainer clients.
2. Call run_all_active_campaigns with auto_email=True — run prospecting + outreach for every active client simultaneously.
3. Call get_campaign_report (no client_id) to see all recent campaign results.
4. For every active client, call send_campaign_report_email to send them their results.
5. Call get_service_revenue for the full revenue report.
6. Give me a complete summary:
   - Total clients served this run
   - Total leads found and emails sent
   - MRR and ARR
   - Any clients due for renewal this week
   - Top 3 action items to grow the service

Work through this end-to-end. Do not stop between steps."""


def autonomous_run(interval_minutes: int = 0, continuous: bool = False):
    run_count = 0
    while True:
        run_count += 1
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        console.print(Panel(
            Text.from_markup(
                f"[bold white]Autonomous Run #{run_count}[/bold white]\n"
                f"[dim]Started at {now}[/dim]\n\n"
                "[yellow]Running campaigns for all retainer clients...[/yellow]\n"
                "[dim]Finding motivated sellers, skip tracing, sending outreach emails...[/dim]"
            ),
            title="[bold blue]Outreach-as-a-Service — Autonomous Mode[/bold blue]",
            border_style="blue",
        ))

        agent_loop(AUTONOMOUS_GOAL, [])

        if not continuous or interval_minutes <= 0:
            break

        console.print(Panel(
            Text.from_markup(f"[bold green]Run #{run_count} complete.[/bold green]\n[dim]Next run in {interval_minutes} min.[/dim]"),
            border_style="dim",
        ))
        try:
            time.sleep(interval_minutes * 60)
        except KeyboardInterrupt:
            console.print("\n[dim]Autonomous loop stopped.[/dim]")
            break


def chat():
    console.print(Panel(
        Text.from_markup(
            "[bold white]Wholesale Omniverse — Outreach-as-a-Service[/bold white]\n"
            "[dim]Run done-for-you outreach campaigns for your retainer clients[/dim]\n\n"
            "[yellow]What I can do:[/yellow]\n"
            "  [cyan]Register Clients[/cyan]    — Add retainer clients with their target markets\n"
            "  [cyan]Run Campaigns[/cyan]       — Prospect + email sellers for any client\n"
            "  [cyan]Run All Campaigns[/cyan]   — One shot for every active client\n"
            "  [cyan]Send Reports[/cyan]        — Email clients their campaign results\n"
            "  [cyan]Revenue Report[/cyan]      — MRR, ARR, clients by tier\n"
            "  [cyan]Record Payments[/cyan]     — Log payments + extend billing\n\n"
            "[dim]Examples:\n"
            '  "Register Jane Doe jane@email.com on standard plan for Memphis TN and Atlanta GA"\n'
            '  "Run campaigns for all active clients now"\n'
            '  "Send OAS-0001 their results report"\n'
            '  "Show me my revenue"\n'
            '  "Record $500 payment from OAS-0002"[/dim]\n\n'
            "[dim]Type [bold]auto[/bold] to run autonomous campaign cycle • [bold]exit[/bold] to quit[/dim]"
        ),
        title="[bold blue]Outreach-as-a-Service — Wholesale Omniverse[/bold blue]",
        border_style="blue",
    ))

    history = []

    while True:
        try:
            user_input = console.input("\n[bold white]You:[/bold white] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]Closing.[/dim]")
            break

        if not user_input:
            continue
        if user_input.lower() in ("exit", "quit"):
            console.print("[dim]Closing.[/dim]")
            break
        if user_input.lower() == "clear":
            history = []
            console.clear()
            continue
        if user_input.lower() == "auto":
            autonomous_run()
            continue

        history = agent_loop(user_input, history)
