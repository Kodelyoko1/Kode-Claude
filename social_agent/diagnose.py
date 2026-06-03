"""
Social Agent preflight.

The existing `--status` flag already reports per-adapter credential state,
which is most of a diagnose. This module adds three things on top:

  1. Per-platform health derived from data/social_posts.json — a platform can
     have valid credentials but still be quietly failing (rate-limit, banned
     account, account locked). `--status` would still show "live" for those.
  2. Content pool inventory per audience — pick_post() draws randomly from
     SELLER_POSTS / BUYER_POSTS / WHOLESALER_POSTS. Small pools mean the same
     copy gets recycled over the same followers — Reddit's spam-detection
     hates that.
  3. Cadence — when did the agent last successfully post anything at all?

Plus the usual aggregation into P0/P1/info checks.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from social_agent.tools import status_all, PLATFORMS
from social_agent.content import SELLER_POSTS, BUYER_POSTS, WHOLESALER_POSTS, ALL_POSTS
from social_agent.health import (
    derive_health, unhealthy_platforms, summary as health_summary, ALERT_AFTER_FAILS,
)

DATA_DIR = Path(__file__).parent.parent / "data"
LOG_FILE = DATA_DIR / "social_posts.json"

# Minimum healthy pool size per audience — below this and we'll recycle copy
# fast enough that Reddit/X flag it as repetitive.
MIN_POOL_PER_AUDIENCE = int(os.environ.get("SA_MIN_POOL", "5"))

# How stale the most recent successful dispatch can be before we warn.
SUCCESS_STALENESS_HOURS = int(os.environ.get("SA_SUCCESS_STALENESS_HOURS", "72"))


@dataclass
class Check:
    name: str
    severity: str
    status: str
    detail: str = ""
    fix_hint: str = ""


def _load(path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return default


# ─────────────────────────── Credentials ───────────────────────────

def check_credentials() -> Check:
    rows = status_all()
    live = [r for r in rows if r["live"]]
    if not live:
        return Check(
            name="Platform credentials",
            severity="P1", status="warn",
            detail=f"0/{len(rows)} platforms have credentials — agent has nowhere to post",
            fix_hint=("Set REDDIT_*/X_*/LINKEDIN_* env vars for the platforms you want to use. "
                      "Helpers: setup_meta.py, setup_pinterest.py, setup_reddit.py."),
        )
    names = ", ".join(r["platform"] for r in live)
    return Check(
        name="Platform credentials",
        severity="info", status="info",
        detail=f"{len(live)}/{len(rows)} live  ({names})",
    )


# ─────────────────────────── Per-platform health ───────────────────────────

def check_platform_health() -> Check:
    s = health_summary()
    if s["platforms_with_attempts"] == 0:
        return Check(
            name="Platform health",
            severity="info", status="info",
            detail="no real dispatches in the log yet (dry-run only)",
        )
    bad = unhealthy_platforms()
    if bad:
        names = ", ".join(f"{c['platform']}(-{c['consecutive_failures']})" for c in bad[:5])
        return Check(
            name="Platform health",
            severity="P1", status="warn",
            detail=(f"{s['healthy']}/{s['platforms_with_attempts']} healthy  ·  "
                    f"{s['warning']} platform(s) with ≥{ALERT_AFTER_FAILS} failures: {names}"),
            fix_hint=("Likely rate-limit, account ban, or expired token. Check the platform's "
                      "account page directly — credentials_ok() can still report 'live' "
                      "even when the actual post API rejects."),
        )
    return Check(
        name="Platform health",
        severity="info", status="info",
        detail=f"{s['healthy']}/{s['platforms_with_attempts']} healthy",
    )


# ─────────────────────────── Content pool ───────────────────────────

def check_content_pool() -> Check:
    pools = {
        "sellers":     len(SELLER_POSTS),
        "buyers":      len(BUYER_POSTS),
        "wholesalers": len(WHOLESALER_POSTS),
    }
    small = [(a, n) for a, n in pools.items() if n < MIN_POOL_PER_AUDIENCE]
    detail = "  ".join(f"{a}={n}" for a, n in pools.items()) + f"  total={len(ALL_POSTS)}"
    if small:
        names = ", ".join(f"{a}({n})" for a, n in small)
        return Check(
            name="Content pool",
            severity="P1", status="warn",
            detail=detail,
            fix_hint=(f"Audience(s) below {MIN_POOL_PER_AUDIENCE} posts: {names}. "
                      "pick_post recycles fast — same copy hits the same followers on Reddit/X. "
                      "Add 2-3 new entries per starved audience in social_agent/content.py."),
        )
    return Check(name="Content pool", severity="info", status="info", detail=detail)


# ─────────────────────────── Cadence ───────────────────────────

def check_cadence() -> Check:
    h = derive_health()
    last_success = ""
    for v in h.values():
        ls = v.get("last_success_at", "")
        if ls and ls > last_success:
            last_success = ls
    if not last_success:
        return Check(
            name="Recent success",
            severity="info", status="info",
            detail="no successful real posts in the log",
        )
    try:
        ts = datetime.fromisoformat(last_success.split("+")[0])
        age_h = (datetime.now() - ts).total_seconds() / 3600
    except ValueError:
        return Check(name="Recent success", severity="info", status="info",
                     detail=f"last success at {last_success}")
    if age_h > SUCCESS_STALENESS_HOURS:
        return Check(
            name="Recent success",
            severity="P1", status="warn",
            detail=f"last successful post was {age_h:.1f}h ago (budget {SUCCESS_STALENESS_HOURS}h)",
            fix_hint="Either cron stopped firing or every live platform is failing — re-check --status + Platform health",
        )
    return Check(name="Recent success", severity="info", status="info",
                 detail=f"last successful post {age_h:.1f}h ago")


# ─────────────────────────── History inventory ───────────────────────────

def check_log_inventory() -> Check:
    log = _load(LOG_FILE, [])
    if not isinstance(log, list):
        return Check(name="Dispatch log shape", severity="P1", status="warn",
                     detail=f"social_posts.json expected list, got {type(log).__name__}")
    n = len(log)
    if n == 0:
        return Check(name="Dispatch log", severity="info", status="info",
                     detail="empty — no dispatches yet")
    dry = sum(1 for e in log if e.get("dry_run"))
    real = n - dry
    return Check(name="Dispatch log", severity="info", status="info",
                 detail=f"total={n}  real={real}  dry_run={dry}")


# ─────────────────────────── Aggregate ───────────────────────────

def run_diagnostics() -> dict:
    checks = [
        check_credentials(),
        check_platform_health(),
        check_content_pool(),
        check_cadence(),
        check_log_inventory(),
    ]
    summary = {
        "P0_fail": sum(1 for c in checks if c.severity == "P0" and c.status == "fail"),
        "P1_warn": sum(1 for c in checks if c.severity == "P1" and c.status == "warn"),
        "passed":  sum(1 for c in checks if c.status == "pass"),
        "total":   len(checks),
    }
    summary["ready_to_dispatch"] = summary["P0_fail"] == 0
    return {"checks": [c.__dict__ for c in checks], "summary": summary}


def print_report(report: dict) -> None:
    icon = {"pass": "✓", "fail": "✗", "warn": "!", "info": "·"}
    for c in report["checks"]:
        print(f"  [{icon[c['status']]}] [{c['severity']:>4s}] {c['name']:24s} {c['detail']}")
        if c["fix_hint"] and c["status"] in ("fail", "warn"):
            print(f"        ↳ {c['fix_hint']}")
    s = report["summary"]
    print()
    print(f"  Result: {s['passed']}/{s['total']} passed · P0 fails={s['P0_fail']} · "
          f"P1 warns={s['P1_warn']}")
    if s["ready_to_dispatch"]:
        print("  ✓ Ready to dispatch. Use `--status` for credential detail, "
              "`--history` for per-dispatch results.")
    else:
        print("  ✗ Fix P0 items above first.")


def main() -> int:
    print("Social Agent preflight\n")
    report = run_diagnostics()
    print_report(report)
    return 0 if report["summary"]["ready_to_dispatch"] else 1


if __name__ == "__main__":
    sys.exit(main())
