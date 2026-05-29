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
from followup_agent.tools import TOOLS, TOOL_FUNCTIONS

console = Console()

SYSTEM_PROMPT = """You are the Seller Follow-Up agent for Wholesale Omniverse LLC.

You manage a 6-touch automated email sequence for every motivated seller lead in the pipeline.

Follow-up schedule (days after last contact):
  Stage 1:  Day 3  — "Just following up"
  Stage 2:  Day 7  — "Cash offer ready"
  Stage 3:  Day 14 — "Making a firm offer"
  Stage 4:  Day 21 — "Final follow-up"
  Stage 5:  Day 30 — "Checking back in"
  Stage 6:  Day 60 — "Market update"

After Stage 6, leads are moved to cold and removed from the sequence.

AUTONOMOUS MODE — do ALL of the following without stopping:
1. Call get_followup_summary to see what's due today.
2. Call run_all_due_followups to send all due emails in one shot.
3. Call get_hot_leads to check if any sellers have responded.
4. Call get_sequence_stats for the full performance report.
5. Give a summary: emails sent today, total in pipeline, hot leads to call, response rate.

Rules:
- Never email a lead that has seller_responded = True (they're in negotiation)
- Never email past stage 6
- Report the response rate — that's the key metric
- Flag any hot leads that need a personal phone call today"""


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
            live.update("[bold cyan]Follow-Up Agent working...[/bold cyan]")
            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=4096,
                system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
                tools=TOOLS,
                messages=history,
            )

        text_parts = [b.text for b in response.content if b.type == "text"]
        tool_calls = [b for b in response.content if b.type == "tool_use"]

        if text_parts:
            console.print(Panel(Markdown("\n".join(text_parts)), title="[bold green]Follow-Up Agent[/bold green]", border_style="green"))

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


AUTONOMOUS_GOAL = """Run the full follow-up sequence cycle right now. Do ALL steps without stopping:
1. Call get_followup_summary — show what's due today and stage breakdown.
2. Call run_all_due_followups — send every due email now.
3. Call get_hot_leads — show any sellers who have responded.
4. Call get_sequence_stats — show full performance.
5. Give me:
   - Total emails sent today
   - Total active leads in sequence
   - Hot leads that need a phone call right now
   - Response rate percentage
   - Next scheduled run recommendation"""


def autonomous_run(interval_minutes: int = 0, continuous: bool = False):
    run_count = 0
    while True:
        run_count += 1
        console.print(Panel(
            Text.from_markup(
                f"[bold white]Follow-Up Cycle #{run_count}[/bold white]\n"
                f"[dim]{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}[/dim]\n\n"
                "[yellow]Sending follow-up emails to all leads due today...[/yellow]"
            ),
            title="[bold blue]Wholesale Omniverse — Seller Follow-Up (Autonomous)[/bold blue]",
            border_style="blue",
        ))
        agent_loop(AUTONOMOUS_GOAL, [])

        if not continuous or interval_minutes <= 0:
            break
        console.print(f"\n[dim]Next run in {interval_minutes} min. Ctrl+C to stop.[/dim]")
        try:
            time.sleep(interval_minutes * 60)
        except KeyboardInterrupt:
            console.print("\n[dim]Stopped.[/dim]")
            break


def chat():
    console.print(Panel(
        Text.from_markup(
            "[bold white]Wholesale Omniverse — Seller Follow-Up Agent[/bold white]\n"
            "[dim]6-touch automated email sequence for 527 pipeline leads[/dim]\n\n"
            "[yellow]What I do:[/yellow]\n"
            "  [cyan]Day 3[/cyan]   Stage 1 — Follow-up check-in\n"
            "  [cyan]Day 7[/cyan]   Stage 2 — Cash offer ready\n"
            "  [cyan]Day 14[/cyan]  Stage 3 — Firm offer\n"
            "  [cyan]Day 21[/cyan]  Stage 4 — Final follow-up\n"
            "  [cyan]Day 30[/cyan]  Stage 5 — Checking back in\n"
            "  [cyan]Day 60[/cyan]  Stage 6 — Market update\n\n"
            "[dim]Type [bold]auto[/bold] to run today's sequence • [bold]exit[/bold] to quit[/dim]"
        ),
        title="[bold blue]Follow-Up Agent — Wholesale Omniverse[/bold blue]",
        border_style="blue",
    ))

    history = []
    while True:
        try:
            user_input = console.input("\n[bold white]You:[/bold white] ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not user_input:
            continue
        if user_input.lower() in ("exit", "quit"):
            break
        if user_input.lower() == "clear":
            history = []; console.clear(); continue
        if user_input.lower() == "auto":
            autonomous_run(); continue
        history = agent_loop(user_input, history)
