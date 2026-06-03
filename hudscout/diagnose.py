"""
HUDScout preflight + revenue-pipeline audit.

The product: $97/mo subscription (or $297 quarterly / $497 white-label) to a
daily digest of new HUD-owned REO listings. The cron sweeps HD_STATES, hits
HUD's JSON endpoint with a freshly-bootstrapped antiforgery token, normalizes
each listing into a lead, and emails the digest to owner + subscribers.

Two silent failure modes the CLAUDE.md note explicitly calls out:
  · Token bootstrap fails — entire cycle returns 0
  · Single state goes silent — aggregate still looks OK

This module answers, in one read-only command:
  1. Channels: SMTP, owner email target
  2. HUD session: can we still get a valid antiforgery token?  (P0)
  3. Configured states: HD_STATES env vs. DEFAULT_STATES
  4. Per-state health (from hudscout.health)
  5. Inventory: hd_leads.json size, last_seen age
  6. Pipeline attribution in shared leads.json
  7. Subscribers + MRR
"""
from __future__ import annotations

import json
import os
import smtplib
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from hudscout.health import (
    summary as health_summary,
    unhealthy_states,
    probe_session,
    ALERT_AFTER_ZEROS,
)

DATA_DIR     = Path(__file__).parent.parent / "data"
HD_LEADS     = DATA_DIR / "hd_leads.json"
LEADS_FILE   = DATA_DIR / "leads.json"
HD_SUBS      = DATA_DIR / "hd_subscribers.json"
DIGESTS_DIR  = DATA_DIR / "hd_outputs"


@dataclass
class Check:
    name: str
    severity: str   # "P0" | "P1" | "info"
    status: str     # "pass" | "fail" | "warn" | "info"
    detail: str = ""
    fix_hint: str = ""


def _load(path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return default


# ─────────────────────────── Channels ───────────────────────────

def check_smtp() -> Check:
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER", "")
    pwd  = os.environ.get("SMTP_PASS", "")
    if not (user and pwd):
        return Check(name="SMTP creds", severity="P0", status="fail",
                     detail="SMTP_USER / SMTP_PASS not set",
                     fix_hint="Required for owner digest + subscriber fulfilment")
    try:
        with smtplib.SMTP(host, port, timeout=10) as srv:
            srv.starttls()
            srv.login(user, pwd)
        return Check(name="SMTP auth", severity="P0", status="pass",
                     detail=f"{host}:{port} as {user}")
    except smtplib.SMTPAuthenticationError as e:
        return Check(name="SMTP auth", severity="P0", status="fail",
                     detail=f"Gmail rejected: {str(e)[:120]}",
                     fix_hint="Re-generate the Gmail app password")
    except Exception as e:
        return Check(name="SMTP connection", severity="P0", status="fail",
                     detail=f"{type(e).__name__}: {str(e)[:120]}")


def check_hud_session() -> Check:
    """Probe the antiforgery handshake — #1 break point per the module docstring."""
    r = probe_session()
    if r.get("ok"):
        return Check(
            name="HUD session bootstrap",
            severity="P0", status="pass",
            detail=f"token len={r['token_len']}  cookies={r['cookies']}",
        )
    return Check(
        name="HUD session bootstrap",
        severity="P0", status="fail",
        detail=r.get("error", "unknown"),
        fix_hint=("HUD likely changed the search page layout. Adjust _open_session() "
                  "(token capture regex) in hudscout/tools.py — see module docstring."),
    )


# ─────────────────────────── Configuration ───────────────────────────

def check_configured_states() -> Check:
    env = os.environ.get("HD_STATES", "")
    if env:
        states = [s.strip().upper() for s in env.split(",") if s.strip()]
        return Check(name="Configured states", severity="info", status="info",
                     detail=f"HD_STATES={env}  ({len(states)} state(s))")
    # Fall back to module default — try to read it without a full import cycle
    from hudscout.tools import DEFAULT_STATES
    return Check(name="Configured states", severity="info", status="info",
                 detail=f"DEFAULT_STATES (HD_STATES unset): {', '.join(DEFAULT_STATES)}")


# ─────────────────────────── Per-state health ───────────────────────────

def check_state_health() -> Check:
    s = health_summary()
    if s["states"] == 0:
        return Check(
            name="Per-state health",
            severity="P1", status="warn",
            detail="no states tracked yet — run a cycle first",
            fix_hint="Run `python3 run_hudscout_auto.py` once to populate hd_state_health.json",
        )
    bad = unhealthy_states()
    if bad:
        names = ", ".join(f"{c['state']}(-{c['consecutive_zeros']})" for c in bad[:5])
        extra = f" +{len(bad) - 5}" if len(bad) > 5 else ""
        return Check(
            name="Per-state health",
            severity="P1", status="warn",
            detail=(f"{s['healthy']}/{s['states']} healthy  ·  "
                    f"{s['warning']} state(s) with ≥{ALERT_AFTER_ZEROS} zeros: {names}{extra}"),
            fix_hint=("HUD's per-state normalization or dataset may have shifted for these "
                      "states. Check `_state_name()` and the raw JSON response shape."),
        )
    return Check(name="Per-state health", severity="info", status="info",
                 detail=f"{s['healthy']}/{s['states']} healthy  ·  "
                        f"total listings all-time: {s['total_found_all_time']}")


# ─────────────────────────── Inventory ───────────────────────────

def check_inventory() -> Check:
    store = _load(HD_LEADS, {})
    if isinstance(store, dict):
        leads = store.get("leads", [])
        seen  = store.get("seen_cases", [])
    else:
        return Check(name="hd_leads.json shape", severity="P0", status="fail",
                     detail=f"expected dict, got {type(store).__name__}")
    n_leads = len(leads) if isinstance(leads, list) else 0
    n_seen  = len(seen)  if isinstance(seen, list)  else 0
    if n_leads == 0:
        return Check(name="HUD inventory", severity="info", status="info",
                     detail="empty — first cycle hasn't run yet or no HUD listings in range")
    # Freshness: most recent listing first_seen age
    most_recent = ""
    for l in leads[-20:]:  # only need to scan the tail
        ts = l.get("first_seen", "")
        if ts > most_recent:
            most_recent = ts
    age = ""
    if most_recent:
        try:
            mr = datetime.fromisoformat(most_recent.split("+")[0])
            age = f" · last_seen {(datetime.now() - mr).days}d ago"
        except ValueError:
            pass
    return Check(name="HUD inventory", severity="info", status="info",
                 detail=f"hd_leads={n_leads}  seen_cases={n_seen}{age}")


def check_digests() -> Check:
    if not DIGESTS_DIR.exists():
        return Check(name="Digest output", severity="info", status="info",
                     detail="hd_outputs/ does not exist (no cycles run yet)")
    files = sorted(DIGESTS_DIR.glob("*.md"))
    if not files:
        return Check(name="Digest output", severity="info", status="info", detail="(empty)")
    last = files[-1]
    age = (datetime.now() - datetime.fromtimestamp(last.stat().st_mtime)).days
    if age > 3:
        return Check(
            name="Digest output",
            severity="P1", status="warn",
            detail=f"{len(files)} digest(s), newest {age}d old ({last.name})",
            fix_hint="Cron probably broken or harvest returned nothing — see Per-state health",
        )
    return Check(name="Digest output", severity="info", status="info",
                 detail=f"{len(files)} digest(s), newest {age}d old")


# ─────────────────────────── Pipeline attribution ───────────────────────────

def check_pipeline_attribution() -> Check:
    leads = _load(LEADS_FILE, {})
    if not isinstance(leads, dict):
        return Check(name="Pipeline attribution", severity="P1", status="warn",
                     detail="leads.json shape")
    if not leads:
        return Check(name="Pipeline attribution", severity="info", status="info",
                     detail="(no leads)")
    tagged = sum(1 for l in leads.values() if l.get("lead_source") == "HUDScout")
    return Check(name="Pipeline attribution", severity="info", status="info",
                 detail=f"{tagged} lead(s) in shared queue carry lead_source=HUDScout")


# ─────────────────────────── Subscribers + MRR ───────────────────────────

PLAN_PRICES = {"monthly_97": 97, "quarterly_297": 99, "white_label_497": 166}  # $/mo equivalent


def check_subscribers() -> Check:
    subs = _load(HD_SUBS, [])
    if not isinstance(subs, list):
        return Check(name="Subscribers", severity="P1", status="warn",
                     detail="hd_subscribers.json wrong shape (expected list)")
    n = len(subs)
    active = [s for s in subs if s.get("status") == "active"]
    if n == 0:
        return Check(name="Subscribers", severity="info", status="info",
                     detail="0 — owner-only mode")
    mrr = sum(PLAN_PRICES.get(s.get("plan", ""), 0) for s in active)
    return Check(name="Subscribers", severity="info", status="info",
                 detail=f"total={n}  active={len(active)}  MRR≈${mrr}/mo")


# ─────────────────────────── Aggregate ───────────────────────────

def run_diagnostics() -> dict:
    checks = [
        check_smtp(),
        check_hud_session(),
        check_configured_states(),
        check_state_health(),
        check_inventory(),
        check_digests(),
        check_pipeline_attribution(),
        check_subscribers(),
    ]
    summary = {
        "P0_fail": sum(1 for c in checks if c.severity == "P0" and c.status == "fail"),
        "P1_warn": sum(1 for c in checks if c.severity == "P1" and c.status == "warn"),
        "passed":  sum(1 for c in checks if c.status == "pass"),
        "total":   len(checks),
    }
    summary["ready_to_run"] = summary["P0_fail"] == 0
    return {"checks": [c.__dict__ for c in checks], "summary": summary}


def print_report(report: dict) -> None:
    icon = {"pass": "✓", "fail": "✗", "warn": "!", "info": "·"}
    for c in report["checks"]:
        print(f"  [{icon[c['status']]}] [{c['severity']:>4s}] {c['name']:30s} {c['detail']}")
        if c["fix_hint"] and c["status"] in ("fail", "warn"):
            print(f"        ↳ {c['fix_hint']}")
    s = report["summary"]
    print()
    print(f"  Result: {s['passed']}/{s['total']} passed · P0 fails={s['P0_fail']} · "
          f"P1 warns={s['P1_warn']}")
    if s["ready_to_run"]:
        print("  ✓ Ready to sweep. See `--health-report` for per-state detail.")
    else:
        print("  ✗ Fix P0 items above first — cycle would return zero listings.")


def main() -> int:
    print("HUDScout preflight\n")
    report = run_diagnostics()
    print_report(report)
    return 0 if report["summary"]["ready_to_run"] else 1


if __name__ == "__main__":
    sys.exit(main())
