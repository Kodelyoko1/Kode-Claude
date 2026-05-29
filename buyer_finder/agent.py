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
from buyer_finder.tools import TOOLS, TOOL_FUNCTIONS

console = Console()

SYSTEM_PROMPT = """You are the Cash Buyer Recruitment agent for Wholesale Omniverse LLC.

Your only job is to find active cash buyers and real estate investors, add them to the buyers list, and email them an intro so they know we have deals.

Without a buyers list you cannot close deals. The pipeline has 527 leads with 0 cash buyers to assign to. You fix that.

Sources you search:
  1. Bing   — "we buy houses cash" + "real estate investor" in target cities
  2. Craigslist — real-estate-wanted section
  3. Redfin cash sales — recent buyers paying all-cash

AUTONOMOUS MODE — do ALL of the following without stopping:
1. Call get_buyers_summary to see where we stand.
2. Call run_all_markets with auto_email=True — hit every top market in the pipeline.
3. Call get_buyers_summary again to show the new total.
4. Give me a summary:
   - Total buyers before and after
   - Buyers added per market
   - Emails sent
   - Which markets still need more buyers
   - Recommendation: run again in how many days?

Be aggressive. More buyers = more deals closed = more assignment fees."""


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
            live.update("[bold cyan]Buyer Finder Agent working...[/bold cyan]")
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
            console.print(Panel(Markdown("\n".join(text_parts)), title="[bold green]Buyer Finder Agent[/bold green]", border_style="green"))

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


AUTONOMOUS_GOAL = """Run the full buyer recruitment cycle right now. Do ALL steps without stopping:
1. Call get_buyers_summary — show starting count.
2. Call run_all_markets with auto_email=True — recruit buyers across all top pipeline markets.
3. Call get_buyers_summary again — show new total.
4. Give me:
   - Buyers added (before vs after)
   - Breakdown by market
   - Total intro emails sent
   - Top 3 buyers to follow up with personally
   - Recommend: run again in how many days?"""


def autonomous_run(interval_minutes: int = 0, continuous: bool = False):
    run_count = 0
    while True:
        run_count += 1
        console.print(Panel(
            Text.from_markup(
                f"[bold white]Buyer Recruitment Cycle #{run_count}[/bold white]\n"
                f"[dim]{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}[/dim]\n\n"
                "[yellow]Searching Bing + Craigslist + Redfin for cash buyers...[/yellow]"
            ),
            title="[bold blue]Wholesale Omniverse — Cash Buyer Finder (Autonomous)[/bold blue]",
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
            "[bold white]Wholesale Omniverse — Cash Buyer Finder[/bold white]\n"
            "[dim]Searches Bing + Craigslist + Redfin for active investors[/dim]\n\n"
            "[yellow]Sources:[/yellow]\n"
            "  [cyan]Bing[/cyan]       — 'we buy houses cash' + investor searches\n"
            "  [cyan]Craigslist[/cyan] — Real estate wanted section\n"
            "  [cyan]Redfin[/cyan]     — Recent cash sales in target markets\n\n"
            "[dim]Type [bold]auto[/bold] to run full recruitment • [bold]exit[/bold] to quit[/dim]"
        ),
        title="[bold blue]Cash Buyer Finder — Wholesale Omniverse[/bold blue]",
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
