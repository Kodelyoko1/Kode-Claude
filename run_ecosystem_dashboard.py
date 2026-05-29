#!/usr/bin/env python3
"""
Wholesale Omniverse Ecosystem Dashboard.
Visual real-time view of every autonomous agent's performance.

Usage:
  python3 run_ecosystem_dashboard.py            # one-shot terminal view
  python3 run_ecosystem_dashboard.py --live     # auto-refresh every 10s
  python3 run_ecosystem_dashboard.py --html     # generate HTML dashboard
  python3 run_ecosystem_dashboard.py --serve    # serve the HTML dashboard on :8765
"""
import argparse
import json
import time
import http.server
import socketserver
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.live import Live
from rich.layout import Layout
from rich.align import Align
from rich import box

from autonomous import metrics, billing
from autonomous.mailer import recent_sends
from paywall.agent_paywall import AGENT_NAMES, DEFAULT_PRICES, list_subscriptions

console = Console()

DATA_DIR = Path(__file__).parent / "data"
HTML_OUT = DATA_DIR / "ecosystem_dashboard.html"

# Display order — autonomous agents only (the 11 we just built)
AGENT_ORDER = [
    "reputation_guard", "towncrier", "gutenberg_voice", "trendscout",
    "link_mender", "careerforge", "paperbrief", "nichelens",
    "storyforge", "pantrychef", "shortsforge", "viral_recycler",
]

AGENT_EMOJI = {
    "reputation_guard": "🛡",
    "towncrier":        "📣",
    "gutenberg_voice":  "🎙",
    "trendscout":       "📈",
    "link_mender":      "🔗",
    "careerforge":      "📄",
    "paperbrief":       "📚",
    "nichelens":        "🔍",
    "storyforge":       "✍",
    "pantrychef":       "🍳",
    "shortsforge":      "🎬",
    "viral_recycler":   "♻",
}


def fmt_money(v: float) -> str:
    return f"${v:,.0f}"


def collect_state() -> dict:
    all_metrics = metrics.get_all()
    state = {}
    grand_mrr = 0
    grand_revenue = 0
    grand_subs = 0
    for key in AGENT_ORDER:
        m = all_metrics.get(key, {})
        rev = billing.revenue_summary(key)
        subs = list_subscriptions(key)
        active = subs.get("active", 0)
        latest = m.get("latest", {})
        totals = m.get("totals", {})
        # Prefer billing-derived MRR; fall back to metrics-reported MRR (in latest snapshot)
        mrr = max(rev.get("mrr", 0), latest.get("mrr", 0) or 0)
        total = max(rev.get("total_paid", 0), latest.get("total_revenue", 0) or 0)
        state[key] = {
            "name":             AGENT_NAMES.get(key, key),
            "price":            DEFAULT_PRICES.get(key, 0),
            "active_subs":      max(active, m.get("active_subs", 0)),
            "free_subs":        m.get("free_subs", 0),
            "mrr":              mrr,
            "total_revenue":    total,
            "last_run":         m.get("last_run", ""),
            "prospects_added":  totals.get("prospects_added", 0),
            "outreach_sent":    totals.get("outreach_sent", 0),
            "fulfillment_sent": totals.get("fulfillment_sent", 0),
            "products_made":    totals.get("products_produced", 0),
            "last_cycle":       latest,
            "status":           "active" if m.get("last_run") else "idle",
        }
        grand_mrr += mrr
        grand_revenue += total
        grand_subs += state[key]["active_subs"]
    state["__summary"] = {
        "total_mrr": grand_mrr,
        "total_revenue": grand_revenue,
        "total_active_subs": grand_subs,
        "agent_count": len(AGENT_ORDER),
        "ts": datetime.now().isoformat(),
    }
    return state


def render_terminal(state: dict) -> Layout:
    layout = Layout()
    layout.split(
        Layout(name="header", size=5),
        Layout(name="body"),
        Layout(name="footer", size=8),
    )

    s = state["__summary"]
    header_text = Text.from_markup(
        f"[bold yellow]WHOLESALE OMNIVERSE[/bold yellow]  [white]·[/white]  "
        f"[bold white]AUTONOMOUS AGENT ECOSYSTEM[/bold white]\n"
        f"[dim]{datetime.now():%A, %B %d, %Y · %H:%M:%S}[/dim]   "
        f"[bold green]MRR ${s['total_mrr']:,.0f}[/bold green]   "
        f"[bold cyan]Active Subs {s['total_active_subs']}[/bold cyan]   "
        f"[bold magenta]Total Revenue ${s['total_revenue']:,.0f}[/bold magenta]"
    )
    layout["header"].update(Panel(Align.center(header_text),
                                  border_style="yellow", box=box.DOUBLE))

    # Per-agent table
    t = Table(box=box.ROUNDED, border_style="blue", expand=True,
              title="[bold]Agent Performance[/bold]")
    t.add_column("Agent", style="bold white", width=22)
    t.add_column("Price", justify="right", style="dim", width=8)
    t.add_column("Active", justify="right", style="cyan", width=7)
    t.add_column("MRR", justify="right", style="green", width=9)
    t.add_column("Revenue", justify="right", style="magenta", width=10)
    t.add_column("Prospects", justify="right", style="yellow", width=10)
    t.add_column("Outreach", justify="right", style="yellow", width=9)
    t.add_column("Delivered", justify="right", style="white", width=10)
    t.add_column("Status", style="bold", width=10)
    t.add_column("Last Run", style="dim", width=16)

    for key in AGENT_ORDER:
        a = state[key]
        emoji = AGENT_EMOJI.get(key, "·")
        status_color = "[green]●[/green] active" if a["status"] == "active" else "[dim]○ idle[/dim]"
        last_run = a["last_run"][:16].replace("T", " ") if a["last_run"] else "—"
        name_short = a["name"].split(" — ")[0]
        t.add_row(
            f"{emoji} {name_short}",
            f"${a['price']:.0f}",
            str(a["active_subs"]),
            fmt_money(a["mrr"]),
            fmt_money(a["total_revenue"]),
            str(a["prospects_added"]),
            str(a["outreach_sent"]),
            str(a["fulfillment_sent"]),
            status_color,
            last_run,
        )
    layout["body"].update(t)

    # Footer — recent activity feed
    feed = Table(box=box.SIMPLE, show_header=True, border_style="dim")
    feed.add_column("Time", style="dim", width=8)
    feed.add_column("Agent", style="cyan", width=18)
    feed.add_column("Purpose", style="yellow", width=12)
    feed.add_column("To", style="white", width=28)
    feed.add_column("Subject", style="white", overflow="ellipsis")
    feed.add_column("Status", justify="right", width=8)
    for s in recent_sends(limit=8):
        ts = s.get("ts", "")[11:16]
        status = s.get("status", "?")
        status_color = "[green]sent[/green]" if status == "sent" else f"[red]{status}[/red]"
        feed.add_row(ts, s.get("agent", "?"), s.get("purpose", ""),
                     s.get("to", "")[:28], s.get("subject", "")[:50], status_color)
    layout["footer"].update(Panel(feed, title="[bold]Live Email Feed (last 8)[/bold]",
                                  border_style="cyan"))
    return layout


def render_html(state: dict) -> str:
    s = state["__summary"]
    rows = []
    for key in AGENT_ORDER:
        a = state[key]
        bar_width = min(100, (a["mrr"] / max(1, max(state[k]["mrr"] for k in AGENT_ORDER))) * 100)
        revenue_bar = min(100, (a["total_revenue"] / max(1, max(state[k]["total_revenue"] for k in AGENT_ORDER))) * 100)
        status_dot = "🟢" if a["status"] == "active" else "⚪"
        name_short = a["name"].split(" — ")[0]
        rows.append(f"""
        <div class="card">
          <div class="card-head">
            <div class="agent-name">{AGENT_EMOJI.get(key, '·')} {name_short}</div>
            <div class="agent-price">${a['price']}/mo</div>
          </div>
          <div class="status">{status_dot} {a['status']}</div>
          <div class="metrics">
            <div class="metric"><span class="label">MRR</span><span class="value">{fmt_money(a['mrr'])}</span></div>
            <div class="metric"><span class="label">Active</span><span class="value">{a['active_subs']}</span></div>
            <div class="metric"><span class="label">Revenue</span><span class="value">{fmt_money(a['total_revenue'])}</span></div>
          </div>
          <div class="bar-row"><div class="bar-label">MRR share</div>
            <div class="bar-track"><div class="bar mrr" style="width:{bar_width}%"></div></div></div>
          <div class="bar-row"><div class="bar-label">Total revenue share</div>
            <div class="bar-track"><div class="bar rev" style="width:{revenue_bar}%"></div></div></div>
          <div class="funnel">
            <div class="funnel-step"><div class="big">{a['prospects_added']}</div><div class="lab">prospects</div></div>
            <div class="arrow">→</div>
            <div class="funnel-step"><div class="big">{a['outreach_sent']}</div><div class="lab">outreach</div></div>
            <div class="arrow">→</div>
            <div class="funnel-step"><div class="big">{a['fulfillment_sent']}</div><div class="lab">delivered</div></div>
          </div>
          <div class="last">Last run: {a['last_run'][:16].replace('T',' ') if a['last_run'] else '—'}</div>
        </div>""")

    feed_rows = ""
    for r in recent_sends(limit=20):
        color = "#10b981" if r.get("status") == "sent" else "#ef4444"
        feed_rows += f"""<tr>
            <td>{r.get('ts','')[11:19]}</td>
            <td>{r.get('agent','?')}</td>
            <td>{r.get('purpose','')}</td>
            <td>{r.get('to','')}</td>
            <td>{r.get('subject','')[:60]}</td>
            <td style="color:{color}">{r.get('status','?')}</td>
        </tr>"""

    return f"""<!DOCTYPE html>
<html><head>
<meta charset="UTF-8">
<meta http-equiv="refresh" content="30">
<title>Wholesale Omniverse · Ecosystem Dashboard</title>
<style>
  body {{ margin:0; font-family:-apple-system,BlinkMacSystemFont,sans-serif;
         background:#0f172a; color:#e2e8f0; }}
  header {{ background:linear-gradient(135deg,#0f172a,#1e293b);
            padding:24px 32px; border-bottom:3px solid #f59e0b; }}
  h1 {{ margin:0; color:#fff; font-size:28px; letter-spacing:1px; }}
  h1 span {{ color:#f59e0b; }}
  .sub {{ color:#94a3b8; font-size:13px; margin-top:4px; }}
  .topstats {{ display:grid; grid-template-columns:repeat(4,1fr); gap:16px;
               padding:24px 32px; }}
  .topstat {{ background:#1e293b; border-radius:8px; padding:20px;
              border-left:4px solid #f59e0b; }}
  .topstat .v {{ font-size:32px; font-weight:800; color:#fff; }}
  .topstat .l {{ font-size:12px; color:#94a3b8; text-transform:uppercase;
                 letter-spacing:1.5px; margin-top:4px; }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(360px,1fr));
           gap:16px; padding:0 32px 32px; }}
  .card {{ background:#1e293b; border-radius:8px; padding:18px;
           border:1px solid #334155; }}
  .card-head {{ display:flex; justify-content:space-between; align-items:baseline; }}
  .agent-name {{ font-size:17px; font-weight:700; color:#fff; }}
  .agent-price {{ color:#f59e0b; font-size:13px; font-weight:600; }}
  .status {{ color:#94a3b8; font-size:12px; margin:4px 0 14px; }}
  .metrics {{ display:grid; grid-template-columns:repeat(3,1fr); gap:8px;
              margin-bottom:14px; }}
  .metric {{ background:#0f172a; padding:10px; border-radius:6px;
             display:flex; flex-direction:column; }}
  .metric .label {{ font-size:10px; color:#64748b; text-transform:uppercase;
                    letter-spacing:1px; }}
  .metric .value {{ font-size:18px; font-weight:700; color:#fff; margin-top:2px; }}
  .bar-row {{ margin:6px 0; }}
  .bar-label {{ font-size:10px; color:#64748b; }}
  .bar-track {{ background:#0f172a; height:6px; border-radius:3px;
                overflow:hidden; margin-top:3px; }}
  .bar {{ height:100%; transition:width .4s; }}
  .bar.mrr {{ background:linear-gradient(90deg,#10b981,#34d399); }}
  .bar.rev {{ background:linear-gradient(90deg,#8b5cf6,#a78bfa); }}
  .funnel {{ display:flex; align-items:center; gap:6px; margin:14px 0 4px;
             font-size:11px; }}
  .funnel-step {{ flex:1; text-align:center; background:#0f172a;
                  padding:6px 4px; border-radius:4px; }}
  .funnel-step .big {{ font-size:16px; font-weight:700; color:#fff; }}
  .funnel-step .lab {{ color:#64748b; font-size:9px; text-transform:uppercase; }}
  .arrow {{ color:#475569; }}
  .last {{ font-size:11px; color:#64748b; margin-top:10px; }}
  .feed {{ padding:0 32px 32px; }}
  .feed h2 {{ color:#fff; font-size:18px; margin:0 0 12px; }}
  table {{ width:100%; background:#1e293b; border-radius:8px; border-collapse:separate;
           border-spacing:0; overflow:hidden; }}
  th, td {{ padding:10px 14px; text-align:left; font-size:13px;
            border-bottom:1px solid #334155; }}
  th {{ background:#0f172a; color:#94a3b8; text-transform:uppercase;
        font-size:11px; letter-spacing:1px; }}
</style>
</head>
<body>
<header>
  <h1>Wholesale <span>Omniverse</span></h1>
  <div class="sub">Autonomous Agent Ecosystem · Auto-refresh every 30s · {datetime.now():%Y-%m-%d %H:%M:%S}</div>
</header>
<section class="topstats">
  <div class="topstat"><div class="v">{fmt_money(s['total_mrr'])}</div><div class="l">Monthly Recurring</div></div>
  <div class="topstat"><div class="v">{fmt_money(s['total_revenue'])}</div><div class="l">Total Revenue</div></div>
  <div class="topstat"><div class="v">{s['total_active_subs']}</div><div class="l">Active Subscribers</div></div>
  <div class="topstat"><div class="v">{s['agent_count']}</div><div class="l">Autonomous Agents</div></div>
</section>
<section class="grid">{''.join(rows)}</section>
<section class="feed">
  <h2>Live Email Feed</h2>
  <table>
    <thead><tr><th>Time</th><th>Agent</th><th>Purpose</th><th>Recipient</th><th>Subject</th><th>Status</th></tr></thead>
    <tbody>{feed_rows}</tbody>
  </table>
</section>
</body></html>"""


def write_html(state: dict) -> Path:
    DATA_DIR.mkdir(exist_ok=True)
    HTML_OUT.write_text(render_html(state))
    return HTML_OUT


def serve_html(port: int = 8765):
    class Handler(http.server.SimpleHTTPRequestHandler):
        def do_GET(self):
            if self.path in ("/", "/index.html"):
                state = collect_state()
                html = render_html(state)
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(html.encode())
                return
            super().do_GET()
    with socketserver.TCPServer(("", port), Handler) as httpd:
        console.print(f"[green]Dashboard serving at http://localhost:{port}[/green]")
        httpd.serve_forever()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="Auto-refresh terminal view")
    parser.add_argument("--html", action="store_true", help="Generate HTML and exit")
    parser.add_argument("--serve", action="store_true", help="Serve HTML dashboard on localhost")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--interval", type=int, default=10, help="Refresh seconds (live mode)")
    args = parser.parse_args()

    if args.serve:
        serve_html(args.port)
        return

    if args.html:
        state = collect_state()
        path = write_html(state)
        console.print(f"[green]✓ Dashboard written to {path}[/green]")
        console.print(f"[dim]Open with: xdg-open {path}[/dim]")
        return

    if args.live:
        with Live(render_terminal(collect_state()), refresh_per_second=0.5, screen=True) as live:
            try:
                while True:
                    time.sleep(args.interval)
                    live.update(render_terminal(collect_state()))
            except KeyboardInterrupt:
                pass
        return

    console.print(render_terminal(collect_state()))


if __name__ == "__main__":
    main()
