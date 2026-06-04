"""
NicheLens preflight + revenue-pipeline audit.

The product: paid hyper-niche curation newsletters ($7/mo per niche,
$59/yr, affiliate injection). Each cycle fans out across every niche
with an active subscriber, reading snapshots from
data/nl_snapshots/<niche>/ and emailing free or paid versions.

Silent failure modes — none of these are loud today:
  · A subscribed niche has no snapshot subdirectory → build_newsletter
    returns "" and that niche's subscribers get nothing this week
  · bs4 missing → every snapshot silently parses to []; entire fleet
    goes dark at once
  · A niche is configured (in nl_niche_configs.json) but no subscriber
    exists → we read snapshots for nothing
  · A subscriber niche has no affiliate map → free tier still sends but
    monetization is dead
  · SMTP creds rotated → all niches affected
  · nl_subscribers.json was consumed but never written; fixed by
    subscribers.py

This module answers, in one read-only command:
  1. Channels: SMTP creds + login
  2. Parser: beautifulsoup4 importable
  3. Snapshot inventory: per-niche count + newest age across all niches
  4. Cross-check: active-subscriber niches vs. snapshot directories
     (dark niches = subscribed but no inputs → P1)
  5. Cross-check: snapshot directories vs. subscribers
     (orphaned snapshots = parsed but nobody reads → info)
  6. Per-niche yield from nl_niche_health.json (P1 if any niche is
     ≥NL_ALERT_AFTER_SKIPS consecutive skips)
  7. Newsletter cadence (P1 if newest > 10d old)
  8. Subscribers + MRR + paid/free split
  9. Affiliate map coverage info (how many subscribed niches have an
     affiliate map → low monetization signal)
"""
from __future__ import annotations

import json
import os
import smtplib
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from nichelens.health import (
    summary as health_summary,
    unhealthy_niches,
    probe_snapshots,
    ALERT_AFTER_SKIPS,
)
from nichelens.subscribers import listing as sub_listing

DATA_DIR       = Path(__file__).parent.parent / "data"
SNAP_DIR       = DATA_DIR / "nl_snapshots"
NEWSLETTER_DIR = DATA_DIR / "nl_newsletters"
NICHE_CFG      = DATA_DIR / "nl_niche_configs.json"
AFFILIATE_MAP  = DATA_DIR / "nl_affiliates.json"


@dataclass
class Check:
    name: str
    severity: str
    status: str
    detail: str = ""
    fix_hint: str = ""


def _load(path: Path, default):
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
                     fix_hint="Required for newsletter fulfillment")
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


def check_parser() -> Check:
    try:
        import bs4
        return Check(name="HTML parser", severity="P0", status="pass",
                     detail=f"beautifulsoup4 {bs4.__version__}")
    except ImportError:
        return Check(name="HTML parser", severity="P0", status="fail",
                     detail="beautifulsoup4 not importable",
                     fix_hint="pip install beautifulsoup4 — every niche silently empties without it")


# ─────────────────────────── Inputs + audience cross-check ───────────────────────────

def check_snapshot_inventory() -> Check:
    """The combined input gate. If zero snapshots anywhere, every niche dark."""
    p = probe_snapshots()
    if not p.get("ok"):
        return Check(name="Snapshot inventory", severity="P0", status="fail",
                     detail=p.get("error") or "0 snapshots in nl_snapshots/",
                     fix_hint="Drop niche HTML into data/nl_snapshots/<niche-slug>/*.html")
    n_niches = len(p["by_niche"])
    age = p.get("newest_age_days")
    age_str = f" · newest {age}d old" if age is not None else ""
    top = sorted(p["by_niche"].items(), key=lambda kv: -kv[1])[:4]
    top_str = ", ".join(f"{c}={n}" for c, n in top)
    extra = f" +{n_niches - 4} more" if n_niches > 4 else ""
    severity, status = "info", "info"
    fix = ""
    if age is not None and age > 14:
        severity, status = "P1", "warn"
        fix = ("Snapshots are stale across the fleet — same items keep ranking. "
               "Refresh nl_snapshots/<niche>/ for active niches.")
    return Check(name="Snapshot inventory", severity=severity, status=status,
                 detail=f"{p['total']} file(s) across {n_niches} niche dir(s) — {top_str}{extra}{age_str}",
                 fix_hint=fix)


def check_dark_niches() -> Check:
    """A niche has active subscribers but no snapshot directory or empty
    directory → that niche silently delivers nothing."""
    out = sub_listing()
    if out["active"] == 0:
        return Check(name="Audience coverage", severity="info", status="info",
                     detail="no active subscribers — nothing to verify")
    p = probe_snapshots()
    snap_by = p.get("by_niche", {})
    subscribed = set(out["by_niche"].keys())
    dark = sorted(n for n in subscribed if snap_by.get(n, 0) == 0)
    orphans = sorted(n for n in snap_by if n not in subscribed)
    if dark:
        notes = [f"dark niches (subbed, 0 snapshots): {', '.join(dark[:4])}"
                 + (f" +{len(dark) - 4}" if len(dark) > 4 else "")]
        if orphans:
            notes.append(f"orphan snapshots (no subscribers): {', '.join(orphans[:4])}"
                         + (f" +{len(orphans) - 4}" if len(orphans) > 4 else ""))
        return Check(name="Audience coverage", severity="P1", status="warn",
                     detail=" · ".join(notes),
                     fix_hint=("Drop snapshots into the dark-niche directories or "
                               "churn those subscribers to stop the silent dark delivery."))
    detail = f"{len(subscribed)} subscribed niche(s) all have snapshot input"
    if orphans:
        detail += f" · orphan snapshots (no subscribers): {', '.join(orphans[:4])}"
        if len(orphans) > 4:
            detail += f" +{len(orphans) - 4}"
    return Check(name="Audience coverage", severity="info", status="info", detail=detail)


# ─────────────────────────── Per-niche extraction yield ───────────────────────────

def check_niche_health() -> Check:
    s = health_summary()
    if s["niches"] == 0:
        return Check(name="Per-niche yield", severity="P1", status="warn",
                     detail="no niches tracked yet — run a cycle first",
                     fix_hint="Run `python3 run_nichelens_auto.py` once to populate nl_niche_health.json")
    bad = unhealthy_niches()
    if bad:
        names = ", ".join(f"{n['niche']}(-{n['consecutive_skips']})" for n in bad[:5])
        extra = f" +{len(bad) - 5}" if len(bad) > 5 else ""
        return Check(name="Per-niche yield", severity="P1", status="warn",
                     detail=(f"{s['healthy']}/{s['niches']} healthy · "
                             f"{s['warning']} niche(s) with ≥{ALERT_AFTER_SKIPS} skips: {names}{extra}"),
                     fix_hint=("Either snapshots stopped or the page layout shifted. "
                               "Spot-check parse_items() in tools.py."))
    return Check(name="Per-niche yield", severity="info", status="info",
                 detail=f"{s['healthy']}/{s['niches']} healthy · "
                        f"all-time items: {s['total_items_all_time']} · "
                        f"sent: {s['total_sent_all_time']}")


# ─────────────────────────── Output cadence ───────────────────────────

def check_cadence() -> Check:
    if not NEWSLETTER_DIR.exists():
        return Check(name="Newsletter cadence", severity="info", status="info",
                     detail="nl_newsletters/ does not exist (no cycles run yet)")
    files = sorted(NEWSLETTER_DIR.glob("*.md"))
    if not files:
        return Check(name="Newsletter cadence", severity="info", status="info", detail="(empty)")
    last = files[-1]
    age = (datetime.now() - datetime.fromtimestamp(last.stat().st_mtime)).days
    if age > 21:
        return Check(name="Newsletter cadence", severity="P1", status="warn",
                     detail=f"{len(files)} newsletter(s), newest {age}d old ({last.name})",
                     fix_hint="No output in 3+ weeks — see Audience coverage + Per-niche yield.")
    if age > 10:
        return Check(name="Newsletter cadence", severity="P1", status="warn",
                     detail=f"{len(files)} newsletter(s), newest {age}d old ({last.name})",
                     fix_hint="Weekly cadence slipping — check cron + yield streaks.")
    return Check(name="Newsletter cadence", severity="info", status="info",
                 detail=f"{len(files)} newsletter(s), newest {age}d old")


# ─────────────────────────── Audience + revenue ───────────────────────────

def check_subscribers() -> Check:
    out = sub_listing()
    if out["total"] == 0:
        return Check(name="Subscribers", severity="info", status="info",
                     detail="0 — owner-only mode")
    return Check(name="Subscribers", severity="info", status="info",
                 detail=(f"active={out['active']}  paid={out['active_paid']}  "
                         f"free={out['active_free']}  pending={out['pending']}  "
                         f"churned={out['churned']}  MRR≈${out['mrr']:.0f}/mo"))


# ─────────────────────────── Monetization config ───────────────────────────

def check_affiliate_coverage() -> Check:
    """Niches with active subscribers but no affiliate-link map produce
    unmonetized free-tier sends."""
    out = sub_listing()
    if out["active"] == 0:
        return Check(name="Affiliate coverage", severity="info", status="info",
                     detail="(skipped — no active subscribers)")
    aff = _load(AFFILIATE_MAP, {})
    if not isinstance(aff, dict):
        return Check(name="Affiliate coverage", severity="P1", status="warn",
                     detail="nl_affiliates.json wrong shape (expected dict)")
    subscribed = set(out["by_niche"].keys())
    no_map = sorted(n for n in subscribed if not aff.get(n))
    if no_map:
        return Check(name="Affiliate coverage", severity="info", status="info",
                     detail=(f"{len(subscribed) - len(no_map)}/{len(subscribed)} "
                             f"subscribed niches have affiliate maps · "
                             f"missing: {', '.join(no_map[:4])}"
                             + (f" +{len(no_map) - 4}" if len(no_map) > 4 else "")),
                     fix_hint=("Add entries to nl_affiliates.json keyed by niche → "
                               "{keyword: url} to monetize free-tier sends."))
    return Check(name="Affiliate coverage", severity="info", status="info",
                 detail=f"all {len(subscribed)} subscribed niches have affiliate maps")


def check_niche_configs() -> Check:
    cfg = _load(NICHE_CFG, {})
    if not isinstance(cfg, dict):
        return Check(name="Niche configs", severity="P1", status="warn",
                     detail="nl_niche_configs.json wrong shape (expected dict)")
    if not cfg:
        return Check(name="Niche configs", severity="info", status="info",
                     detail="(no nl_niche_configs.json — keyword fallback in effect)",
                     fix_hint="Optional but recommended: define niche keywords for better scoring.")
    return Check(name="Niche configs", severity="info", status="info",
                 detail=f"{len(cfg)} niche(s) with custom keyword config")


# ─────────────────────────── Aggregate ───────────────────────────

def run_diagnostics() -> dict:
    checks = [
        check_smtp(),
        check_parser(),
        check_snapshot_inventory(),
        check_dark_niches(),
        check_niche_health(),
        check_cadence(),
        check_subscribers(),
        check_affiliate_coverage(),
        check_niche_configs(),
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
        print(f"  [{icon[c['status']]}] [{c['severity']:>4s}] {c['name']:25s} {c['detail']}")
        if c["fix_hint"] and c["status"] in ("fail", "warn"):
            print(f"        ↳ {c['fix_hint']}")
    s = report["summary"]
    print()
    print(f"  Result: {s['passed']}/{s['total']} passed · P0 fails={s['P0_fail']} · "
          f"P1 warns={s['P1_warn']}")
    if s["ready_to_run"]:
        print("  ✓ Ready to run. See `--health-report` for per-niche detail.")
    else:
        print("  ✗ Fix P0 items above first — cycle would deliver nothing.")


def main() -> int:
    print("NicheLens preflight\n")
    report = run_diagnostics()
    print_report(report)
    return 0 if report["summary"]["ready_to_run"] else 1


if __name__ == "__main__":
    sys.exit(main())
