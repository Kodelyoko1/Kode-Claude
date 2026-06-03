#!/usr/bin/env python3
"""
Deal Analyzer autonomous entry point.

Three flags do the work:
  --diagnose         read-only preflight
  --bulk-analyze     rank all hot leads by deal math, optionally email owner
  --loi LEAD-NNNN    generate + send LOI for a single lead

The original chat agent (main.py / agent.py) is unchanged — this script
adds the bulk + autonomous paths so the owner doesn't have to open the
chat to work the pipeline.
"""
import argparse
import json
import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

sys.path.insert(0, str(Path(__file__).parent))
from paywall.agent_paywall import paywall_prompt
from autonomous.self_healing import with_healing

console = Console()
AGENT_KEY = "wholesale"


@with_healing(AGENT_KEY)
def cycle(args) -> dict:
    if args.diagnose:
        from deal_analyzer import diagnose
        console.print("[bold]Deal Analyzer preflight[/bold]\n")
        report = diagnose.run_diagnostics()
        diagnose.print_report(report)
        return {"diagnose": report["summary"]}

    if args.bulk_analyze:
        from deal_analyzer import bulk_analyze
        result = bulk_analyze.analyze_all_hot(
            assignment_fee=args.assignment_fee,
            only_with_asking=args.with_asking_only,
        )
        console.print(bulk_analyze.render_digest_text(result, top_n=args.top))
        if args.send:
            delivery = bulk_analyze.email_owner_digest(result, top_n=args.top)
            console.print("\n[dim]--- delivery ---[/dim]")
            console.print(json.dumps(delivery, indent=2, default=str))
        return {"analyzed": result["analyzed"]}

    if args.loi:
        from deal_analyzer import loi
        if args.preview:
            r = loi.generate_loi(args.loi, offer_override=args.offer,
                                  assignment_fee=args.assignment_fee,
                                  close_days=args.close_days)
            if "error" in r:
                console.print(f"[red]{r['error']}[/red]")
                return {"loi": r}
            console.print(r["body_text"])
            return {"loi": "preview"}
        result = loi.send_loi(args.loi, offer_override=args.offer,
                               assignment_fee=args.assignment_fee,
                               close_days=args.close_days, dry_run=args.dry_run)
        # Drop body text/html from console — they're long
        console.print(json.dumps(
            {k: v for k, v in result.items() if k not in ("body_text", "body_html")},
            indent=2, default=str,
        ))
        return {"loi": result.get("status") or ("written" if result.get("sent") is False else "sent")}

    console.print("[yellow]Nothing to do — pass --diagnose, --bulk-analyze, or --loi LEAD-NNNN[/yellow]")
    return {}


def main():
    p = argparse.ArgumentParser(description="Deal Analyzer — autonomous deal math + LOI")
    p.add_argument("--diagnose", action="store_true",
                    help="Read-only preflight + queue audit")
    p.add_argument("--bulk-analyze", action="store_true",
                    help="Rank every hot lead by deal math")
    p.add_argument("--loi", default=None, metavar="LEAD_ID",
                    help="Generate + send LOI for a specific lead")

    # Bulk-analyze opts
    p.add_argument("--assignment-fee", type=float, default=10000.0)
    p.add_argument("--top", type=int, default=15,
                    help="Top-N for the bulk digest")
    p.add_argument("--with-asking-only", action="store_true",
                    help="Skip leads without an asking_price")
    p.add_argument("--send", action="store_true",
                    help="Email the bulk-analyze digest to the owner")

    # LOI opts
    p.add_argument("--offer", type=float, default=None,
                    help="Override the computed MAO for --loi")
    p.add_argument("--close-days", type=int, default=14,
                    help="Days to close for --loi (default 14)")
    p.add_argument("--preview", action="store_true",
                    help="With --loi: print rendered text only, don't write/send")
    p.add_argument("--dry-run", action="store_true",
                    help="With --loi: write to disk but don't email")

    args = p.parse_args()

    console.print(Panel(
        Text.from_markup(
            "[bold white]Deal Analyzer[/bold white]\n"
            "[dim]bulk deal math + LOI generation[/dim]"
        ),
        title="[bold blue]Wholesale Omniverse — Deal Analyzer[/bold blue]",
        border_style="blue",
    ))

    if not paywall_prompt(AGENT_KEY):
        return
    cycle(args)


if __name__ == "__main__":
    main()
