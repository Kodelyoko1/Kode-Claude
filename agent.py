import json
import os
import time
import argparse
from groq import Groq
from datetime import datetime
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.markdown import Markdown
from rich.live import Live
from tools import TOOLS, TOOL_FUNCTIONS

console = Console()

COMPANY_NAME = "Wholesale Omniverse LLC"
COMPANY_EMAIL = "info@wholesaleomniverse.com"

SYSTEM_PROMPT = f"""You are the autonomous AI agent for {COMPANY_NAME} — an elite wholesale real estate company. You represent {COMPANY_NAME} in all outreach, analysis, and deal-making.

Your expertise:
- Finding motivated sellers (foreclosure, probate, tax delinquent, vacant, divorce)
- Analyzing deals using the 70% rule: MAO = (ARV × 0.70) - Repairs - Assignment Fee
- Building and managing a cash buyers list
- Getting properties under contract and assigning them to investors
- Skip tracing and outreach strategies
- Reading markets and finding profitable cities/niches

Key formulas:
- MAO (Maximum Allowable Offer) = (ARV × 0.70) - Repair Costs - Assignment Fee
- Equity % = (ARV - All-In Cost) / ARV × 100
- A deal works when: Purchase Price ≤ MAO and Equity ≥ 30%
- Assignment Fee: typically $5,000–$25,000 per deal

Branding: Always use "{COMPANY_NAME}" as the company name in emails and outreach. The company email is {COMPANY_EMAIL}.

You operate AUTONOMOUSLY — when given a goal you break it down and execute tool calls in sequence to complete it fully. You don't wait to be told each step.

Your process:
1. Research the market first
2. Find/qualify leads
3. Analyze deals with the numbers
4. Save good leads to the pipeline
5. Look up property records and find owner contact info
6. Send outreach emails automatically for qualified leads
7. Track everything

Be direct. Give real numbers. Tell the user clearly if a deal works or not and exactly why.
Flag deals where the numbers don't make sense — protecting capital is priority #1."""


def run_tool(tool_name: str, tool_input: dict) -> str:
    fn = TOOL_FUNCTIONS.get(tool_name)
    if not fn:
        return json.dumps({"error": f"Unknown tool: {tool_name}"})
    try:
        result = fn(**tool_input)
        return json.dumps(result, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


def _anthropic_tools_to_groq(tools: list) -> list:
    """Convert Anthropic tool schema format to Groq/OpenAI format."""
    groq_tools = []
    for t in tools:
        groq_tools.append({
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("input_schema", {"type": "object", "properties": {}}),
            }
        })
    return groq_tools


MAX_TOOL_ROUNDS = 20  # stop after this many tool-call rounds to prevent runaway loops


def agent_loop(user_message: str, history: list) -> list:
    client = Groq(api_key=os.environ.get("GROQ_API_KEY"))
    groq_tools = _anthropic_tools_to_groq(TOOLS)
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history + [{"role": "user", "content": user_message}]
    rounds = 0

    rate_limit_retries = 0
    MAX_RATE_RETRIES = 3
    tool_format_retries = 0
    MAX_TOOL_FORMAT_RETRIES = 2

    while rounds < MAX_TOOL_ROUNDS:
        try:
            with Live(console=console, refresh_per_second=10) as live:
                live.update("[bold cyan]Agent working...[/bold cyan]")
                response = client.chat.completions.create(
                    model="moonshotai/kimi-k2-instruct",
                    max_tokens=2048,
                    tools=groq_tools,
                    tool_choice="auto",
                    messages=messages,
                )
            rate_limit_retries = 0  # reset on success
        except Exception as e:
            err = str(e)
            if "429" in err or "rate_limit" in err:
                rate_limit_retries += 1
                if rate_limit_retries > MAX_RATE_RETRIES:
                    console.print("[red]Daily token limit exhausted — stopping. Groq resets at midnight UTC.[/red]")
                    break
                wait = 60
                console.print(f"[yellow]Rate limit hit (attempt {rate_limit_retries}/{MAX_RATE_RETRIES}) — waiting {wait}s...[/yellow]")
                time.sleep(wait)
                continue
            if "tool_use_failed" in err:
                tool_format_retries += 1
                if tool_format_retries > MAX_TOOL_FORMAT_RETRIES:
                    console.print("[red]Model kept emitting malformed tool calls — skipping rest of run.[/red]")
                    break
                console.print(f"[yellow]Malformed tool call (attempt {tool_format_retries}/{MAX_TOOL_FORMAT_RETRIES}) — nudging model to use proper format...[/yellow]")
                messages.append({
                    "role": "user",
                    "content": "Your previous tool call was rejected as malformed. Emit the tool call using the standard tool_calls JSON format — do NOT wrap it in <function=...> tags, do NOT wrap arguments in a list. Retry now.",
                })
                continue
            raise

        msg = response.choices[0].message
        tool_calls = msg.tool_calls or []
        text = msg.content or ""

        if text:
            console.print(Panel(
                Markdown(text),
                title="[bold green]Agent[/bold green]",
                border_style="green",
            ))

        messages.append({"role": "assistant", "content": text, "tool_calls": [
            {"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
            for tc in tool_calls
        ] if tool_calls else None})

        if not tool_calls:
            break

        rounds += 1
        for tc in tool_calls:
            tool_input = json.loads(tc.function.arguments)
            console.print(f"\n[bold yellow]>[/bold yellow] [cyan]{tc.function.name}[/cyan] [dim]{tc.function.arguments}[/dim]")
            result = run_tool(tc.function.name, tool_input)
            preview = result if len(result) < 500 else result[:500] + "\n..."
            console.print(f"  [dim green]{preview}[/dim green]")
            stored = result if len(result) <= 400 else result[:400] + "\n... [truncated]"
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": stored})

        # Sliding window: keep system + goal + last 10 messages to stay under TPM limit
        system_msg = messages[0]
        goal_msg   = messages[1]
        tail       = messages[2:]
        if len(tail) > 10:
            tail = tail[-10:]
        messages = [system_msg, goal_msg] + tail

    if rounds >= MAX_TOOL_ROUNDS:
        console.print("[yellow]Max tool rounds reached — stopping agent loop.[/yellow]")

    history.clear()
    history.extend(messages[1:])
    return history


AUTONOMOUS_GOAL = """Run a complete wholesale prospecting cycle right now. Do ALL of the following steps without stopping to ask me anything:

1. Call scan_and_import_csv_leads with auto_email=True first — import any CSV files dropped in data/import/ from PropStream, BatchLeads, or any source.
2. For cities with verified open data (Chicago IL, Kansas City MO, Norfolk VA), call prospect_from_government_records with auto_email=True to pull live records.
3. For all target markets (Detroit MI, Baltimore MD, Memphis TN, Cleveland OH, Chicago IL, Kansas City MO), call scrape_craigslist_leads with auto_email=True — pulls below-market listings sorted by days on market from Redfin, imports them as pipeline leads. Contact info is found in step 4 via skip trace.
4. Call skip_trace_and_email_all with no filters (city="", state="", limit=100) — find contact info for every pipeline lead missing an email and immediately send them outreach. This is critical: every lead we can reach gets a cold outreach email right now.
5. Call notify_cash_buyers with no filters — email ALL cash buyers in our database about active deals in their target markets. Any buyer interested in our active pipeline cities gets a deal sheet in their inbox today.
6. Call get_leads to show the full pipeline.
7. Call get_email_log to show all outreach sent this session.
8. Give me a crisp business summary: CSV files imported, government leads found, seller emails sent, buyer notifications sent, pipeline total, and top follow-up priorities.

Work through this end-to-end. Do not stop between steps."""


def autonomous_run(cities: list = None, interval_minutes: int = 0, continuous: bool = False):
    """Run the agent autonomously — no user input required."""
    goal = AUTONOMOUS_GOAL
    if cities:
        city_list = ", ".join(cities)
        goal = goal.replace(
            "Pick 2-3 high-distress cities (e.g. Detroit MI, Baltimore MD, Memphis TN, Cleveland OH, or similar markets with strong investor activity).",
            f"Use these cities the user specified: {city_list}."
        )

    run_count = 0
    while True:
        run_count += 1
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        console.print(Panel(
            Text.from_markup(
                f"[bold white]Autonomous Run #{run_count}[/bold white]\n"
                f"[dim]Started at {now}[/dim]\n\n"
                "[yellow]Agent is running the full prospecting cycle...[/yellow]\n"
                "[dim]No input needed — sit back.[/dim]"
            ),
            title=f"[bold blue]{COMPANY_NAME} — Autonomous Mode[/bold blue]",
            border_style="blue",
        ))

        history = []
        agent_loop(goal, history)

        if not continuous or interval_minutes <= 0:
            break

        next_run = datetime.now().strftime("%H:%M:%S")
        console.print(Panel(
            Text.from_markup(
                f"[bold green]Run #{run_count} complete.[/bold green]\n"
                f"[dim]Next run in {interval_minutes} minutes. Press Ctrl+C to stop.[/dim]"
            ),
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
            f"[bold white]{COMPANY_NAME}[/bold white]\n"
            f"[dim]{COMPANY_EMAIL}[/dim]\n"
            "[dim]Find deals • Analyze numbers • Email prospects • Manage pipeline[/dim]\n\n"
            "[yellow]What I can do for you:[/yellow]\n"
            "  [cyan]Deal Analysis[/cyan]     — ARV, MAO, equity, assignment fee calculation\n"
            "  [cyan]Market Research[/cyan]   — Comps, trends, investor activity\n"
            "  [cyan]Find Leads[/cyan]        — Motivated seller strategies by market\n"
            "  [cyan]Property Lookup[/cyan]   — Owner name, tax records, assessed value\n"
            "  [cyan]Email Outreach[/cyan]    — Auto-email prospects with branded templates\n"
            "  [cyan]Pipeline Mgmt[/cyan]     — Track leads from new → assigned\n"
            "  [cyan]Cash Buyers[/cyan]       — Build and manage your buyers list\n"
            "  [cyan]Contracts[/cyan]         — Record and assign wholesale deals\n\n"
            "[dim]Example prompts:\n"
            '  "Run the full prospecting cycle now"\n'
            '  "Find leads in Baton Rouge LA and email the good ones"\n'
            '  "Look up owner of 123 Main St and send an outreach email"\n'
            '  "Analyze this deal: 123 Main St, ARV $200k, repairs $30k, asking $110k"\n'
            '  "Show me all emails sent so far"\n'
            '  "Show me my full pipeline"[/dim]\n\n'
            "[dim]Type [bold]auto[/bold] to run autonomous cycle • [bold]exit[/bold] to quit • [bold]clear[/bold] to reset[/dim]"
        ),
        title=f"[bold blue]{COMPANY_NAME} — AI Agent[/bold blue]",
        border_style="blue",
    ))

    history = []

    while True:
        try:
            user_input = console.input("\n[bold white]You:[/bold white] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]Closing. Go close some deals.[/dim]")
            break

        if not user_input:
            continue
        if user_input.lower() in ("exit", "quit"):
            console.print("[dim]Closing. Go close some deals.[/dim]")
            break
        if user_input.lower() == "clear":
            history = []
            console.clear()
            console.print("[dim]Conversation cleared.[/dim]")
            continue
        if user_input.lower() == "auto":
            autonomous_run()
            continue

        history = agent_loop(user_input, history)
