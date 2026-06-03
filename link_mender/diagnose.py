"""
LinkMender preflight.

Three external dependencies that the current cycle fails silently against:

  1. Bing search — the entire discovery pipeline lives here. When Bing changes
     selector or rate-limits us, _bing_search returns [] and discover_prospects
     reports "0 discovered" — same shape as a healthy idle day after the lead
     pool already saturated. There's no way to tell from the daily counter
     whether discovery is broken or just done.
  2. Outbound HTTP fetches — link_check uses urllib HEAD; sites that block bots
     score as 0/unreachable and pad broken_count without being broken.
  3. Bing redirect unwrap — every Bing URL is wrapped in /ck/a?u=a1<base64>.
     If the encoding changes, _unwrap_bing_redirect returns the wrapped URL
     verbatim and every "snapshot" is just Bing's redirect page.

Plus the prospect-pool funnel: discovered → contacted → client (paid) or
insufficient_signal (skipped). The existing CLI doesn't surface stage counts.
"""
from __future__ import annotations

import json
import os
import smtplib
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

DATA_DIR     = Path(__file__).parent.parent / "data"
PROSPECTS    = DATA_DIR / "lm_prospects.json"
CLIENTS      = DATA_DIR / "lm_clients.json"
SNAPSHOT_DIR = DATA_DIR / "lm_snapshots"
REPORTS_DIR  = DATA_DIR / "lm_reports"

PLAN_PRICES_MO = {"audit_97": 0, "monthly_47": 47, "agency_197": 0}


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


def _writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".lm_writable_probe"
        probe.write_text("ok")
        probe.unlink()
        return True
    except OSError:
        return False


# ─────────────────────────── Channels ───────────────────────────

def check_smtp() -> Check:
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER", "")
    pwd  = os.environ.get("SMTP_PASS", "")
    if not (user and pwd):
        return Check(name="SMTP creds", severity="P0", status="fail",
                     detail="SMTP_USER / SMTP_PASS not set",
                     fix_hint="Outreach + monthly client reports both go through SMTP")
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


# ─────────────────────────── External fetches ───────────────────────────

def check_http_egress() -> Check:
    """Probe outbound HTTP — without it, no snapshots, no link checks."""
    try:
        import requests
    except ImportError:
        return Check(name="requests library", severity="P0", status="fail",
                     detail="not installed",
                     fix_hint="pip install requests beautifulsoup4")
    try:
        r = requests.get("https://httpbin.org/status/200", timeout=5,
                          headers={"User-Agent": "LinkMender/1.0"})
        if r.status_code == 200:
            return Check(name="Outbound HTTP", severity="P0", status="pass",
                         detail="httpbin reached ok")
        return Check(name="Outbound HTTP", severity="P1", status="warn",
                     detail=f"httpbin returned {r.status_code}")
    except Exception as e:
        return Check(name="Outbound HTTP", severity="P0", status="fail",
                     detail=f"{type(e).__name__}: {str(e)[:120]}",
                     fix_hint="Container has no internet egress — discovery + audits will both fail")


def check_bing_search() -> Check:
    """Probe the actual Bing search pipeline — the #1 silent failure mode."""
    try:
        from link_mender.tools import _bing_search, _unwrap_bing_redirect
    except Exception as e:
        return Check(name="Bing search probe", severity="P0", status="fail",
                     detail=f"can't import probe helpers: {e}")
    # Use a stable query that should always return something
    results = _bing_search("real estate investing blog", n=3)
    if not results:
        return Check(
            name="Bing search probe",
            severity="P1", status="warn",
            detail="0 results from a stable query — Bing might be rate-limiting or blocking us",
            fix_hint=("Try a fresh User-Agent or wait 1h; selector b_algo h2 a may have changed. "
                      "Discovery is dead-on-arrival without this."),
        )
    # Validate the unwrap is producing real URLs (not raw /ck/a redirects)
    raw_redirect_count = sum(1 for r in results if "/ck/a" in r["url"])
    if raw_redirect_count == len(results):
        return Check(
            name="Bing search probe",
            severity="P1", status="warn",
            detail=f"{len(results)} results but all still wrapped in /ck/a redirects",
            fix_hint="_unwrap_bing_redirect didn't decode — base64 wrapper format probably changed",
        )
    return Check(name="Bing search probe", severity="info", status="info",
                 detail=f"{len(results)} results, {len(results) - raw_redirect_count} unwrapped successfully")


# ─────────────────────────── Directories ───────────────────────────

def check_dirs() -> Check:
    issues = []
    if not _writable(SNAPSHOT_DIR):
        issues.append(f"snapshots not writable ({SNAPSHOT_DIR})")
    if not _writable(REPORTS_DIR):
        issues.append(f"reports not writable ({REPORTS_DIR})")
    if issues:
        return Check(name="Snapshot/report dirs", severity="P0", status="fail",
                     detail="; ".join(issues))
    return Check(name="Snapshot/report dirs", severity="P0", status="pass",
                 detail=f"snapshots + reports under {DATA_DIR.name}/")


# ─────────────────────────── Prospect funnel ───────────────────────────

def check_funnel() -> Check:
    prospects = _load(PROSPECTS, [])
    if not isinstance(prospects, list):
        return Check(name="lm_prospects.json shape", severity="P1", status="warn",
                     detail=f"expected list, got {type(prospects).__name__}")
    n = len(prospects)
    if n == 0:
        return Check(name="Prospect funnel", severity="P1", status="warn",
                     detail="0 prospects — discovery hasn't surfaced any yet",
                     fix_hint="Run --bing-probe to check Bing reachability, then run a cycle")
    from collections import Counter
    by_status = Counter(p.get("status", "?") for p in prospects)
    parts = "  ".join(f"{k}={v}" for k, v in by_status.most_common())
    contacted = by_status.get("contacted", 0)
    client = by_status.get("client", 0)
    insufficient = by_status.get("insufficient_signal", 0)
    if n > 5 and contacted == 0 and client == 0:
        return Check(
            name="Prospect funnel",
            severity="P1", status="warn",
            detail=f"total={n}  ·  {parts}",
            fix_hint=("Lots of prospects but no outreach attempts — every site is being scored "
                      "<3 broken links. Either the link-check is being blocked or your snapshot "
                      "queries are landing on wrong-shape pages."),
        )
    return Check(name="Prospect funnel", severity="info", status="info",
                 detail=f"total={n}  ·  {parts}")


# ─────────────────────────── Clients ───────────────────────────

def check_clients() -> Check:
    clients = _load(CLIENTS, [])
    if not isinstance(clients, list):
        return Check(name="lm_clients.json shape", severity="P1", status="warn",
                     detail=f"expected list, got {type(clients).__name__}")
    if not clients:
        return Check(name="Clients", severity="info", status="info",
                     detail="0 — no paying clients yet")
    active = [c for c in clients if c.get("status") == "active"]
    mrr = sum(PLAN_PRICES_MO.get(c.get("plan", ""), 0) for c in active)
    return Check(name="Clients", severity="info", status="info",
                 detail=f"total={len(clients)}  active={len(active)}  MRR=${mrr}/mo")


# ─────────────────────────── Snapshot freshness ───────────────────────────

def check_snapshot_freshness() -> Check:
    if not SNAPSHOT_DIR.exists():
        return Check(name="Snapshot freshness", severity="info", status="info",
                     detail="lm_snapshots/ does not exist yet")
    sub_dirs = [d for d in SNAPSHOT_DIR.iterdir() if d.is_dir()]
    if not sub_dirs:
        return Check(name="Snapshot freshness", severity="info", status="info",
                     detail="lm_snapshots/ is empty (no discoveries yet)")
    newest = max(sub_dirs, key=lambda d: d.stat().st_mtime)
    age_h = (datetime.now() - datetime.fromtimestamp(newest.stat().st_mtime)).total_seconds() / 3600
    return Check(name="Snapshot freshness", severity="info", status="info",
                 detail=f"{len(sub_dirs)} site(s) snapshotted, newest {age_h:.1f}h ago ({newest.name})")


# ─────────────────────────── Aggregate ───────────────────────────

def run_diagnostics() -> dict:
    checks = [
        check_smtp(),
        check_dirs(),
        check_http_egress(),
        check_bing_search(),
        check_funnel(),
        check_snapshot_freshness(),
        check_clients(),
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
        print("  ✓ Ready to discover + audit. See --health-report for per-query yield, "
              "--bing-probe to test discovery on demand.")
    else:
        print("  ✗ Fix P0 items above first.")


def main() -> int:
    print("LinkMender preflight\n")
    report = run_diagnostics()
    print_report(report)
    return 0 if report["summary"]["ready_to_run"] else 1


if __name__ == "__main__":
    sys.exit(main())
