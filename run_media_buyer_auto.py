#!/usr/bin/env python3
"""
Media Buyer daily cron — monitor → controller → generator → report.

Designed to run once every 24-48h via cron. Idempotent: every action goes
through controller.execute() which respects MB_LIVE/DRY_RUN.

Flags:
  --kind {lead_gen,ecom}    which profile to evaluate (default: lead_gen)
  --skip-controller         monitor + report only (no rule application)
  --skip-generator          don't draft new creative variations
"""
import argparse
import json
import sys
from datetime import datetime, UTC
from pathlib import Path

from rich.console import Console
from rich.panel import Panel

sys.path.insert(0, str(Path(__file__).parent))
from autonomous import mailer
from media_buyer import controller, diagnose, generator, launcher, meta_api, monitor
from media_buyer.config import DRY_RUN, profile_for
from paywall.agent_paywall import paywall_prompt
from autonomous.self_healing import run_with_healing

AGENT_KEY = "media_buyer"
console = Console()


def run_once(kind: str, *, skip_controller: bool, skip_generator: bool) -> dict:
    profile = profile_for(kind=kind)
    summary: dict = {
        "profile": kind, "dry_run": DRY_RUN,
        "started_at": datetime.now(UTC).isoformat(),
    }

    console.print(Panel.fit(
        f"[bold white]Media Buyer cycle — {kind}[/bold white]\n"
        f"[dim]dry_run={DRY_RUN}  ad_account={profile.ad_account_id}[/dim]",
        title="Wholesale Omniverse — Media Buyer", border_style="blue",
    ))

    # One sweep, shared by controller + generator — Insights calls are the
    # most expensive part of the cycle (rate-limited and slow at scale).
    sweep = monitor.daily_sweep(kind)
    summary["sweep_counts"] = {k: len(v) for k, v in sweep.items()}

    if not skip_controller:
        summary["controller"] = controller.evaluate_and_apply(kind, sweep=sweep)

    if not skip_generator and sweep["ads"]:
        copies = _load_copies_for(sweep["ads"], profile.ad_account_id)
        summary["creative_refresh"] = generator.refresh_batch_for(
            sweep["ads"], copies, kind=kind, top_k=5,
        )

    _email_report(summary, profile)
    console.print(json.dumps(summary, indent=2, default=str))
    return summary


def _load_copies_for(ads, ad_account_id: str) -> dict[str, dict]:
    """Pull creative copy for every ad id in the sweep. One Graph call per ad
    (the creative endpoint doesn't bulk; this is fine for typical account sizes)."""
    copies: dict[str, dict] = {}
    for m in ads:
        if not m.object_id:
            continue
        try:
            ad = meta_api._request(  # noqa: SLF001
                "GET", f"/{m.object_id}",
                params={"fields": "creative{title,body,object_story_spec}"},
            )
            cre = ad.get("creative") or {}
            spec = (cre.get("object_story_spec") or {}).get("link_data") or {}
            copies[m.object_id] = {
                "hook": spec.get("message", "")[:200],
                "primary_text": spec.get("message", ""),
                "headline": spec.get("name", ""),
            }
        except Exception:
            copies[m.object_id] = {}
    return copies


def _email_report(summary: dict, profile) -> None:
    """Daily report email — same pattern as the other autonomous agents."""
    if not profile.alert_email:
        return
    body = "Media Buyer daily report\n\n" + json.dumps(summary, indent=2, default=str)
    mailer.send(
        AGENT_KEY, profile.alert_email,
        f"Media Buyer daily — {summary['profile']} — {datetime.now(UTC):%Y-%m-%d}",
        body, purpose="report",
    )


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--kind", choices=["lead_gen", "ecom"], default="lead_gen")
    p.add_argument("--skip-controller", action="store_true")
    p.add_argument("--skip-generator", action="store_true")
    p.add_argument("--diagnose", action="store_true",
                   help="Run read-only preflight checks and exit (no spend, no mutations)")
    p.add_argument("--launch", action="store_true",
                   help="Bootstrap a first paused lead-gen campaign (form + adset + creative + ad)")
    p.add_argument("--budget", type=float, default=20.0,
                   help="Daily budget USD for --launch (default 20)")
    p.add_argument("--locations", default=None,
                   help='Comma-separated US states for --launch targeting (default $MB_LAUNCH_LOCATIONS or Maine)')
    p.add_argument("--form-id", default=None,
                   help="Reuse an existing leadgen form id with --launch")
    p.add_argument("--activate", action="store_true",
                   help="With --launch, create objects ACTIVE instead of PAUSED (requires MB_LIVE=1)")
    args = p.parse_args()

    if args.diagnose:
        console.print(Panel.fit(
            f"[bold white]Media Buyer preflight — {args.kind}[/bold white]",
            title="Wholesale Omniverse — Media Buyer", border_style="cyan",
        ))
        report = diagnose.run_diagnostics(args.kind)
        diagnose.print_report(report)
        sys.exit(0 if report["summary"]["ready_to_launch"] else 1)

    if args.launch:
        if args.kind != "lead_gen":
            console.print("[yellow]--launch currently only supports --kind lead_gen[/yellow]")
            sys.exit(2)
        # Hard gate: refuse --activate unless MB_LIVE is set
        if args.activate and DRY_RUN:
            console.print("[red]--activate requires MB_LIVE=1 to avoid creating an ACTIVE "
                          "campaign that will silently fail to be created.[/red]")
            sys.exit(2)
        console.print(Panel.fit(
            f"[bold white]Media Buyer launch — {args.kind} — "
            f"{'ACTIVE' if args.activate else 'PAUSED'} — dry_run={DRY_RUN}[/bold white]",
            title="Wholesale Omniverse — Media Buyer", border_style=("red" if args.activate else "yellow"),
        ))
        result = launcher.launch_lead_gen(
            daily_budget_usd=args.budget,
            locations=launcher._parse_locations(args.locations) if args.locations else None,
            form_id=args.form_id,
            paused=not args.activate,
        )
        console.print(json.dumps(result, indent=2, default=str))
        if DRY_RUN:
            console.print("\n[yellow]DRY-RUN — no Meta objects were created. "
                          "Set MB_LIVE=1 and re-run to actually launch.[/yellow]")
        sys.exit(0)

    if not paywall_prompt(AGENT_KEY):
        return
    run_with_healing(
        AGENT_KEY,
        lambda: run_once(args.kind, skip_controller=args.skip_controller, skip_generator=args.skip_generator),
    )


if __name__ == "__main__":
    main()
