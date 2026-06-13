"""FBAds report — render a human-readable + email-friendly view of
the latest insights + attribution + verdicts."""
from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from fbads.monitor import (latest_insights, latest_attribution, verdicts,
                           pull_insights, compute_attribution)


def render_text(refresh: bool = True, email: bool = False) -> str:
    """Build the report. When refresh=True, pull fresh insights first."""
    if refresh:
        pull_insights()
        compute_attribution()
    snap = latest_insights()
    att  = latest_attribution()
    v    = verdicts()
    lines = [
        f"╔══════════════════════════════════════════════════════════════════════════╗",
        f"║  FBAds Performance Report — {datetime.now():%Y-%m-%d %H:%M}                       ║",
        f"╚══════════════════════════════════════════════════════════════════════════╝",
        "",
    ]
    if not snap:
        lines.append("  No Meta Insights snapshot yet.")
        lines.append("  Run: python3 run_fbads_auto.py --monitor")
        body = "\n".join(lines)
        if email:
            _email_owner(body)
        return body
    if not snap.get("ads"):
        lines.append("  No live ads in the last 7 days — nothing for Meta to report on.")
        lines.append(f"  Last Insights pull: {snap.get('ts','?')}")
        lines.append("  Run: python3 run_fbads_auto.py --launch --live  (push the latest pack)")
        body = "\n".join(lines)
        if email:
            _email_owner(body)
        return body

    totals = att.get("totals", {})
    lines.append(f"  Window: last 7 days  ·  Ads scanned: {snap['fetched']}")
    lines.append(f"  Total spend:        ${totals.get('total_spend', 0):.2f}")
    lines.append(f"  Attributed revenue: ${totals.get('total_revenue', 0):.2f}")
    blend = totals.get("blended_roas")
    lines.append(f"  Blended ROAS:       {blend if blend is not None else '—'}")
    lines.append("")

    def _block(title: str, rows: list, show_reason: bool = False):
        lines.append(f"  ─── {title} ({len(rows)}) ───")
        if not rows:
            lines.append("    (none)")
            lines.append("")
            return
        for r in rows[:10]:
            line = (f"    {r.get('ad_name','?')[:48]:<48s}  "
                    f"${r.get('spend',0):>7.2f}  "
                    f"clicks={r.get('clicks',0):>4d}  "
                    f"convo={r.get('conversations',0):>3d}  "
                    f"subs={r.get('attributed_subscribers',0):>2d}  "
                    f"rev=${r.get('attributed_revenue',0):>7.2f}  "
                    f"roas={r.get('roas') if r.get('roas') is not None else '—'}")
            lines.append(line)
            if show_reason and r.get("reason"):
                lines.append(f"      reason: {r['reason']}")
        if len(rows) > 10:
            lines.append(f"    ... +{len(rows) - 10} more")
        lines.append("")

    _block("WINNERS  (scale these — ROAS ≥ 2.0)",   v["winners"])
    _block("LOSERS   (pause these — ROAS < 0.3)",   v["losers"])
    _block("UNKNOWNS (not enough data yet)",        v["unknowns"], show_reason=True)

    body = "\n".join(lines)
    if email:
        _email_owner(body)
    return body


def _email_owner(body: str) -> None:
    """Send the report to SMTP_USER (the owner)."""
    try:
        from autonomous import mailer
    except ImportError:
        return
    user = os.environ.get("SMTP_USER", "")
    if not user:
        return
    mailer.send(
        "fbads", user,
        f"FBAds report — {datetime.now():%Y-%m-%d}",
        body, purpose="owner_digest",
    )
