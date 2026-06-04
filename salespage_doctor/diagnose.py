"""
SalesPageDoctor preflight + revenue-pipeline audit.

The product: $77 one-time audit + $37/mo monitoring + $147 launch
package. The cycle auto-scrapes Bing for creator product pages, fetches
each candidate, extracts a contact email, runs a heuristic audit, and
emails a free 3-issue preview as the lead magnet. Paying clients get a
monthly full re-audit.

Silent failure modes — none are loud today:
  · Bing breaks for one or all dorks → discovered=0 silently
  · Target sites block our UA / egress dies → every audit fetch_fails
  · requests or bs4 missing → entire pipeline silently no-ops
  · All recent audits scored high (≥85, high_score_skip) — looks like
    success but means we're targeting wrong creators
  · spd_clients.json was consumed but never written; same gap as the
    other agents — fixed by clients.py

This module answers, in one read-only command:
  1. Channels: SMTP creds + login
  2. Scraper deps: requests + bs4 importable
  3. HTTP egress: real outbound probe (httpbin)
  4. Bing handshake: consume one dork, count parsed results
  5. Per-query Bing yield from spd_query_health.json (P1 if any query
     has ≥SPD_ALERT_AFTER_ZEROS consecutive zero-result runs)
  6. Prospect inventory: discovered / contacted / errors / high_score_skip
  7. Audit outcome distribution (P1 if fetch_failed dominates,
     info-level score distribution otherwise)
  8. Clients + MRR + one-time collected
  9. Monitoring cadence (P1 if any active monitoring_37 client hasn't
     received a re-audit in > 35 days — fulfill_cycle's 30d cycle plus
     a 5d safety margin)
 10. Public landing page age (info — page is regenerated each cycle)
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
from salespage_doctor.health import (
    probe_bing,
    probe_egress,
    query_summary,
    unhealthy_queries,
    audit_outcome_summary,
    ALERT_AFTER_ZEROS,
)
from salespage_doctor.clients import listing as client_listing

DATA_DIR     = Path(__file__).parent.parent / "data"
PROSPECTS    = DATA_DIR / "spd_prospects.json"
CLIENT_FILE  = DATA_DIR / "spd_clients.json"
REPORTS_DIR  = DATA_DIR / "spd_reports"
PUBLIC_PAGE  = Path(__file__).parent.parent / "website" / "salespage_doctor.html"


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
                     fix_hint="Required for preview outreach + monthly fulfillment")
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


def check_scraper_deps() -> Check:
    missing = []
    versions = []
    for mod in ("requests", "bs4"):
        try:
            m = __import__(mod)
            versions.append(f"{mod} {getattr(m, '__version__', '?')}")
        except ImportError:
            missing.append(mod)
    if missing:
        return Check(name="Scraper deps", severity="P0", status="fail",
                     detail=f"missing: {', '.join(missing)}",
                     fix_hint=f"pip install {' '.join(missing)} — without these, every audit silently no-ops")
    return Check(name="Scraper deps", severity="P0", status="pass",
                 detail=" / ".join(versions))


def check_egress() -> Check:
    r = probe_egress()
    if r.get("ok"):
        return Check(name="HTTP egress", severity="P0", status="pass",
                     detail=f"{r.get('probe')} → {r.get('status')} ({r.get('bytes')} bytes)")
    return Check(name="HTTP egress", severity="P0", status="fail",
                 detail=r.get("error", "unknown"),
                 fix_hint="No outbound HTTP — every audit fetch_fails. Check DNS / firewall.")


def check_bing() -> Check:
    r = probe_bing()
    q = r.get("query", "")[:48]
    if r.get("ok"):
        return Check(name="Bing handshake", severity="P0", status="pass",
                     detail=f"query='{q}' → {r['results']} result(s)")
    if "error" in r:
        return Check(name="Bing handshake", severity="P0", status="fail",
                     detail=f"query='{q}' → {r.get('error', '')}",
                     fix_hint="Bing parse broke. Check _bing_search() in tools.py.")
    # ok=False but no error → zero parsed results from a working request
    return Check(name="Bing handshake", severity="P0", status="fail",
                 detail=f"query='{q}' → 0 results parsed",
                 fix_hint=("Bing layout/anti-bot changed OR the dork rotted. "
                           "Same pattern as link_mender. Adjust the CSS selectors "
                           "in _bing_search() or refresh the dork."))


# ─────────────────────────── Per-query yield ───────────────────────────

def check_query_yield() -> Check:
    s = query_summary()
    if s["queries"] == 0:
        return Check(name="Per-query yield", severity="P1", status="warn",
                     detail="no queries tracked yet — run a cycle first",
                     fix_hint="Run `python3 run_salespage_doctor_auto.py` once to populate spd_query_health.json")
    bad = unhealthy_queries()
    if bad:
        names = ", ".join(f"{q['query'][:32]}(-{q['consecutive_zeros']})" for q in bad[:3])
        extra = f" +{len(bad) - 3}" if len(bad) > 3 else ""
        return Check(name="Per-query yield", severity="P1", status="warn",
                     detail=(f"{s['healthy']}/{s['queries']} healthy · "
                             f"{s['warning']} dork(s) with ≥{ALERT_AFTER_ZEROS} zeros: {names}{extra}"),
                     fix_hint=("Per-dork rot — refresh those queries in "
                               "DEFAULT_PROSPECT_QUERIES or accept the platform is dead."))
    return Check(name="Per-query yield", severity="info", status="info",
                 detail=f"{s['healthy']}/{s['queries']} healthy · "
                        f"all-time discovered: {s['total_discovered_all_time']}")


# ─────────────────────────── Prospect inventory ───────────────────────────

def check_prospects() -> Check:
    prospects = _load(PROSPECTS, [])
    if not isinstance(prospects, list) or not prospects:
        return Check(name="Prospects", severity="info", status="info",
                     detail="0 — discovery hasn't found any yet")
    counts = {"discovered": 0, "contacted": 0, "client": 0, "high_score_skip": 0,
              "audit_error_fetch_failed": 0, "audit_error_bs4_missing": 0, "other_error": 0}
    for p in prospects:
        st = p.get("status", "discovered")
        if st in counts:
            counts[st] += 1
        elif st.startswith("audit_error_"):
            counts["other_error"] += 1
        else:
            counts.setdefault(st, 0)
            counts[st] += 1
    return Check(name="Prospects", severity="info", status="info",
                 detail=(f"total={len(prospects)}  discovered={counts['discovered']}  "
                         f"contacted={counts['contacted']}  "
                         f"high_score_skip={counts['high_score_skip']}  "
                         f"fetch_failed={counts['audit_error_fetch_failed']}"))


# ─────────────────────────── Audit outcome distribution ───────────────────────────

def check_audit_outcomes() -> Check:
    s = audit_outcome_summary()
    if s["total"] == 0:
        return Check(name="Audit outcomes", severity="info", status="info",
                     detail="(no audits logged yet)")
    fetch_fail_rate = s["fetch_failed"] / s["total"] if s["total"] else 0
    detail = (f"log={s['total']}  success={s['success']}  "
              f"fetch_failed={s['fetch_failed']}  high_score_skip={s['high_score_skip']}  "
              f"avg_score={s['avg_score']}")
    if fetch_fail_rate >= 0.5:
        return Check(name="Audit outcomes", severity="P1", status="warn",
                     detail=detail + f" · fetch_failed_rate={fetch_fail_rate:.0%}",
                     fix_hint=("Over half of audits can't even fetch the page. "
                               "Either egress is broken, target sites are blocking "
                               "the SalesPageDoctor UA, or DEFAULT_PROSPECT_QUERIES "
                               "is returning broken URLs."))
    return Check(name="Audit outcomes", severity="info", status="info",
                 detail=detail + " · dist " +
                 " ".join(f"{k}={v}" for k, v in s["score_dist"].items()))


# ─────────────────────────── Clients + revenue ───────────────────────────

def check_clients() -> Check:
    out = client_listing()
    if out["total"] == 0:
        return Check(name="Clients", severity="info", status="info",
                     detail="0 — owner-only mode")
    return Check(name="Clients", severity="info", status="info",
                 detail=(f"active={out['active']}  pending={out['pending']}  "
                         f"fulfilled={out['fulfilled']}  churned={out['churned']}  "
                         f"MRR≈${out['mrr']:.0f}/mo  "
                         f"one-time-collected=${out['one_time_collected']}"))


def check_monitoring_cadence() -> Check:
    """Active monitoring_37 clients should be re-audited every 30d.
    Flag any whose last fulfillment-report is > 35d old (or never)."""
    out = client_listing()
    if out["active"] == 0:
        return Check(name="Monitoring cadence", severity="info", status="info",
                     detail="(no active clients)")
    now = datetime.now()
    stale = []
    for c in out["clients"]:
        if c.get("status") != "active" or c.get("plan") != "monitoring_37":
            continue
        # Look for the most recent _full.md report for this slug
        slug = c.get("slug", "")
        if not slug or not REPORTS_DIR.exists():
            stale.append((c.get("contact_email", "?"), "no_report"))
            continue
        reports = sorted(REPORTS_DIR.glob(f"{slug}_*_full.md"),
                         key=lambda f: f.stat().st_mtime)
        if not reports:
            stale.append((c.get("contact_email", "?"), "no_report"))
            continue
        age = (now - datetime.fromtimestamp(reports[-1].stat().st_mtime)).days
        if age > 35:
            stale.append((c.get("contact_email", "?"), f"{age}d"))
    if stale:
        sample = ", ".join(f"{e}({age})" for e, age in stale[:4])
        extra = f" +{len(stale) - 4}" if len(stale) > 4 else ""
        return Check(name="Monitoring cadence", severity="P1", status="warn",
                     detail=f"{len(stale)} monitoring client(s) overdue: {sample}{extra}",
                     fix_hint=("Monthly audit hasn't run for these clients in >35d. "
                               "Either the cron is broken or audit fetch_failed for them."))
    return Check(name="Monitoring cadence", severity="info", status="info",
                 detail="all monitoring clients within 35d window")


# ─────────────────────────── Public page ───────────────────────────

def check_public_page() -> Check:
    if not PUBLIC_PAGE.exists():
        return Check(name="Public landing", severity="info", status="info",
                     detail="website/salespage_doctor.html not generated yet")
    age = (datetime.now() - datetime.fromtimestamp(PUBLIC_PAGE.stat().st_mtime)).days
    return Check(name="Public landing", severity="info", status="info",
                 detail=f"website/salespage_doctor.html, {age}d old")


# ─────────────────────────── Aggregate ───────────────────────────

def run_diagnostics() -> dict:
    checks = [
        check_smtp(),
        check_scraper_deps(),
        check_egress(),
        check_bing(),
        check_query_yield(),
        check_prospects(),
        check_audit_outcomes(),
        check_clients(),
        check_monitoring_cadence(),
        check_public_page(),
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
        print(f"  [{icon[c['status']]}] [{c['severity']:>4s}] {c['name']:24s} {c['detail']}")
        if c["fix_hint"] and c["status"] in ("fail", "warn"):
            print(f"        ↳ {c['fix_hint']}")
    s = report["summary"]
    print()
    print(f"  Result: {s['passed']}/{s['total']} passed · P0 fails={s['P0_fail']} · "
          f"P1 warns={s['P1_warn']}")
    if s["ready_to_run"]:
        print("  ✓ Ready to run. See `--health-report` for per-query detail "
              "and `--audits N` for per-URL outcomes.")
    else:
        print("  ✗ Fix P0 items above first — cycle would discover/audit nothing.")


def main() -> int:
    print("SalesPageDoctor preflight\n")
    report = run_diagnostics()
    print_report(report)
    return 0 if report["summary"]["ready_to_run"] else 1


if __name__ == "__main__":
    sys.exit(main())
