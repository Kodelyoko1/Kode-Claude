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
from media_buyer import controller, generator, meta_api, monitor
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
    args = p.parse_args()

    if not paywall_prompt(AGENT_KEY):
        return
    run_with_healing(
        AGENT_KEY,
        lambda: run_once(args.kind, skip_controller=args.skip_controller, skip_generator=args.skip_generator),
    )


if __name__ == "__main__":
    main()
