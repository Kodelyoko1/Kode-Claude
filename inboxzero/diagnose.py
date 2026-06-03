"""
InboxZero preflight.

The agent reads + mutates the owner's Gmail over IMAP, so silent failures are
especially bad: a wrong app password means every cycle records "login failed"
and quietly does nothing, while the owner assumes triage is running.

Two failure modes the existing cycle hides:
  · IMAP login rejects (app password rotated, 2FA reset, Gmail security alert).
    triage_inbox() returns {error: login failed} and the digest email reports
    "error" with no further escalation.
  · The "archive" action uses Gmail's X-GM-LABELS extension. If IMAP_HOST is
    ever changed away from imap.gmail.com (e.g., a customer with FastMail),
    archive becomes a no-op. The agent looks like it's working but nothing is
    being archived.

This module answers in one read-only command:
  1. Channels: SMTP creds (for sending the digest), IMAP login (for reading)
  2. Config: IMAP_HOST, IZ_FETCH_LIMIT, IZ_OWNER_EMAIL, ANTHROPIC_API_KEY
  3. Recent triage stats from iz_log.json (last 5 cycles)
  4. Lead queue (iz_leads.json) — depth + how many already trial-sent
  5. Subscribers + MRR
"""
from __future__ import annotations

import imaplib
import json
import os
import smtplib
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
LOG_FILE = DATA_DIR / "iz_log.json"
LEADS_FILE = DATA_DIR / "iz_leads.json"
SUBS_FILE = DATA_DIR / "iz_subscribers.json"

IMAP_HOST = "imap.gmail.com"   # mirror of inboxzero.tools.IMAP_HOST
PLAN_PRICES_MO = {"monthly_97": 97, "team_297": 297, "deep_clean_97": 0}


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
                     fix_hint="Owner won't receive the daily digest without this")
    try:
        with smtplib.SMTP(host, port, timeout=10) as srv:
            srv.starttls()
            srv.login(user, pwd)
        return Check(name="SMTP auth", severity="P0", status="pass",
                     detail=f"{host}:{port} as {user}")
    except smtplib.SMTPAuthenticationError as e:
        return Check(name="SMTP auth", severity="P0", status="fail",
                     detail=f"Gmail rejected: {str(e)[:120]}",
                     fix_hint="Re-generate the Gmail app password — same app password is used for IMAP")
    except Exception as e:
        return Check(name="SMTP connection", severity="P0", status="fail",
                     detail=f"{type(e).__name__}: {str(e)[:120]}")


def check_imap() -> Check:
    """Try to log in to IMAP — the real failure mode is silent rejection."""
    user = os.environ.get("SMTP_USER", "")
    pwd  = os.environ.get("SMTP_PASS", "")
    if not (user and pwd):
        return Check(name="IMAP login probe", severity="P0", status="fail",
                     detail="SMTP_USER / SMTP_PASS not set",
                     fix_hint="Same Gmail app password is used for IMAP")
    try:
        mail = imaplib.IMAP4_SSL(IMAP_HOST, timeout=10)
    except OSError as e:
        return Check(name="IMAP connection", severity="P0", status="fail",
                     detail=f"can't reach {IMAP_HOST}: {str(e)[:120]}")
    try:
        mail.login(user, pwd)
    except imaplib.IMAP4.error as e:
        return Check(name="IMAP login", severity="P0", status="fail",
                     detail=f"Gmail rejected: {str(e)[:160]}",
                     fix_hint="Re-generate the Gmail app password at myaccount.google.com/apppasswords")
    try:
        mail.select("INBOX", readonly=True)
        typ, data = mail.search(None, "UNSEEN")
        unread = len(data[0].split()) if typ == "OK" else 0
    except Exception as e:
        unread = -1
    finally:
        try: mail.logout()
        except Exception: pass
    if unread < 0:
        return Check(name="IMAP INBOX select", severity="P1", status="warn",
                     detail="login ok but INBOX select/search failed")
    return Check(name="IMAP login", severity="P0", status="pass",
                 detail=f"{IMAP_HOST} as {user}  ·  {unread} unread in INBOX")


def check_gmail_archive_trick() -> Check:
    """The agent archives via Gmail's X-GM-LABELS extension. Surface this so
    the owner knows the trick only works against actual Gmail."""
    if IMAP_HOST != "imap.gmail.com":
        return Check(
            name="Archive mechanism",
            severity="P1", status="warn",
            detail=f"IMAP_HOST={IMAP_HOST} but archive uses Gmail's X-GM-LABELS extension",
            fix_hint="On non-Gmail hosts, archive is a no-op — port to MOVE INBOX → Archive instead",
        )
    return Check(name="Archive mechanism", severity="info", status="info",
                 detail=f"Gmail X-GM-LABELS archive (IMAP_HOST={IMAP_HOST})")


# ─────────────────────────── Config ───────────────────────────

def check_config() -> Check:
    fetch_limit = int(os.environ.get("IZ_FETCH_LIMIT", "50"))
    owner = os.environ.get("IZ_OWNER_EMAIL", os.environ.get("SMTP_USER", "(unset)"))
    has_claude = bool(os.environ.get("ANTHROPIC_API_KEY"))
    return Check(name="Config", severity="info", status="info",
                 detail=(f"IZ_FETCH_LIMIT={fetch_limit}  ·  "
                         f"digest_to={owner}  ·  "
                         f"claude_escalation={'on' if has_claude else 'off'}"))


# ─────────────────────────── Recent triage ───────────────────────────

def check_recent_triage() -> Check:
    log = _load(LOG_FILE, [])
    if not isinstance(log, list) or not log:
        return Check(name="Recent triage", severity="info", status="info",
                     detail="iz_log.json empty — no cycles run yet")
    last = log[-1]
    res = last.get("result", {})
    if "error" in res:
        return Check(name="Recent triage", severity="P1", status="warn",
                     detail=f"last cycle errored: {res['error'][:80]}",
                     fix_hint="Check IMAP probe above for root cause")
    summ = res.get("summary", {})
    actions = res.get("actions", {})
    scanned = res.get("scanned", 0)
    cats = "  ".join(f"{c}={len(summ.get(c, []))}"
                     for c in ("urgent", "important", "promo", "newsletter", "social", "other"))
    return Check(name="Recent triage (last cycle)", severity="info", status="info",
                 detail=(f"scanned={scanned}  archived={actions.get('archived',0)}  "
                         f"flagged={actions.get('flagged',0)}  left={actions.get('left',0)}  ·  {cats}"))


def check_log_freshness() -> Check:
    log = _load(LOG_FILE, [])
    if not isinstance(log, list) or not log:
        return Check(name="Log freshness", severity="info", status="info",
                     detail="(no log entries)")
    latest = log[-1].get("at", "")
    if not latest:
        return Check(name="Log freshness", severity="info", status="info", detail="(no timestamp)")
    try:
        ts = datetime.fromisoformat(latest.split("+")[0])
        age_h = (datetime.now() - ts).total_seconds() / 3600
    except ValueError:
        return Check(name="Log freshness", severity="info", status="info", detail=f"last_at={latest}")
    if age_h > 36:
        return Check(name="Log freshness", severity="P1", status="warn",
                     detail=f"last cycle {age_h:.1f}h ago (daily cron probably stopped)",
                     fix_hint="Check cron tail entry for inboxzero in run_all_autonomous_agents.sh")
    return Check(name="Log freshness", severity="info", status="info",
                 detail=f"last cycle {age_h:.1f}h ago")


# ─────────────────────────── Lead queue + subscribers ───────────────────────────

def check_leads() -> Check:
    leads = _load(LEADS_FILE, [])
    if not isinstance(leads, list):
        return Check(name="Lead queue", severity="P1", status="warn",
                     detail=f"iz_leads.json wrong shape: {type(leads).__name__}")
    if not leads:
        return Check(name="Lead queue", severity="info", status="info",
                     detail="0 — no inbound prospects yet")
    pitched = sum(1 for l in leads if l.get("trial_sent"))
    return Check(name="Lead queue", severity="info", status="info",
                 detail=f"total={len(leads)}  trial_sent={pitched}  un-pitched={len(leads) - pitched}")


def check_subscribers() -> Check:
    subs = _load(SUBS_FILE, [])
    if not isinstance(subs, list):
        return Check(name="Subscribers", severity="P1", status="warn",
                     detail=f"iz_subscribers.json wrong shape: {type(subs).__name__}")
    if not subs:
        return Check(name="Subscribers", severity="info", status="info",
                     detail="0 — owner-only mode")
    active = [s for s in subs if s.get("status") == "active"]
    mrr = sum(PLAN_PRICES_MO.get(s.get("plan", ""), 0) for s in active)
    return Check(name="Subscribers", severity="info", status="info",
                 detail=f"total={len(subs)}  active={len(active)}  MRR=${mrr}/mo")


# ─────────────────────────── Aggregate ───────────────────────────

def run_diagnostics() -> dict:
    checks = [
        check_smtp(),
        check_imap(),
        check_gmail_archive_trick(),
        check_config(),
        check_recent_triage(),
        check_log_freshness(),
        check_leads(),
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
        print("  ✓ Ready to triage. Use --dry-run for a no-modification preview.")
    else:
        print("  ✗ Fix P0 items above first — cycle will silently no-op.")


def main() -> int:
    print("InboxZero preflight\n")
    report = run_diagnostics()
    print_report(report)
    return 0 if report["summary"]["ready_to_run"] else 1


if __name__ == "__main__":
    sys.exit(main())
