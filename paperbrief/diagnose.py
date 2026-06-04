"""
PaperBrief preflight + revenue-pipeline audit.

The product: paid weekly vertical research summarization ($39/mo,
$399/yr, $999/yr enterprise). Owner queues papers in pb_queue.json,
drops PDFs into pb_pdfs/<paper_id>.pdf; build_brief() extracts +
sectionizes into pb_briefs/<paper_id>.md; weekly_digest(vertical) bundles
≥3 undelivered briefs; fulfill_cycle() emails the bundle to subscribers.

Silent failure modes — none are loud today:
  · PDF missing for a queued paper → build_brief errors `missing_pdf` →
    paper stays in queue forever
  · PDF present but extraction yields <500 chars (scan, corrupt file) →
    `extract_failed` → same outcome
  · weekly_digest needs ≥3 undelivered briefs; with 2 ready it silently
    skips the vertical
  · pypdf/PyPDF2 not installed → every extract_pdf returns "" → every
    paper extract_failed at once
  · pb_subscribers.json was consumed but never written; fixed by
    subscribers.py

This module answers, in one read-only command:
  1. Channels: SMTP creds + login
  2. PDF library: pypdf or PyPDF2 importable
  3. Queue + PDFs + briefs triangulation (P0 if nothing to do; P1 on
     missing PDFs for queued items)
  4. Stuck queue items (queued > 14d, no brief built — usually
     extract_failed)
  5. Per-vertical readiness (undelivered briefs ≥ MIN_DIGEST_BRIEFS?)
  6. Recent per-paper build-outcome streaks (PDFs failing repeatedly)
  7. Per-vertical extraction streak (consecutive_skips on fulfill)
  8. Newsletter cadence (briefs dir, since digests are bundled inline)
  9. Subscribers + MRR
 10. Leads pipeline (trial_sent rate)
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
from paperbrief.health import (
    probe_inputs,
    vertical_summary,
    unhealthy_verticals,
    build_outcome_summary,
    ALERT_AFTER_SKIPS,
    MIN_DIGEST_BRIEFS,
)
from paperbrief.subscribers import listing as sub_listing

DATA_DIR    = Path(__file__).parent.parent / "data"
QUEUE_FILE  = DATA_DIR / "pb_queue.json"
PDF_DIR     = DATA_DIR / "pb_pdfs"
BRIEFS_DIR  = DATA_DIR / "pb_briefs"
LEADS_FILE  = DATA_DIR / "pb_leads.json"


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
                     fix_hint="Required for subscriber fulfillment + lead-magnet outreach")
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


def check_pdf_library() -> Check:
    """extract_pdf in tools.py tries pypdf then falls back to PyPDF2.
    If neither is importable, every extract returns "" and every paper
    fails extract_failed at once."""
    tried = []
    for mod in ("pypdf", "PyPDF2"):
        try:
            m = __import__(mod)
            ver = getattr(m, "__version__", "?")
            return Check(name="PDF library", severity="P0", status="pass",
                         detail=f"{mod} {ver}")
        except ImportError as e:
            tried.append(f"{mod}: {e}")
    return Check(name="PDF library", severity="P0", status="fail",
                 detail="neither pypdf nor PyPDF2 importable",
                 fix_hint="pip install pypdf — every PDF silently extracts to '' without it")


# ─────────────────────────── Inputs ───────────────────────────

def check_inputs() -> Check:
    p = probe_inputs()
    if not p.get("ok"):
        return Check(name="Inputs", severity="P0", status="fail",
                     detail="pb_queue.json empty AND pb_pdfs/ has no files",
                     fix_hint="Queue papers in data/pb_queue.json and drop PDFs into data/pb_pdfs/")
    # Stale PDFs
    age = p.get("pdfs_newest_age_days")
    age_str = f" · pdfs newest {age}d old" if age is not None else ""
    detail = (f"queue={p['queue_total']} ({p['queue_undelivered']} undelivered) · "
              f"pdfs={p['pdfs_total']} · briefs={p['briefs_total']}{age_str}")
    # Missing PDFs for queued items
    missing = p["missing_pdfs"]
    if missing:
        sample = ", ".join(missing[:4])
        extra  = f" +{len(missing) - 4}" if len(missing) > 4 else ""
        return Check(name="Inputs", severity="P1", status="warn",
                     detail=f"{detail} · missing_pdf for {len(missing)} queued item(s): {sample}{extra}",
                     fix_hint=("Drop the missing PDFs into data/pb_pdfs/<paper_id>.pdf "
                               "or remove those paper_ids from pb_queue.json."))
    if age is not None and age > 14:
        return Check(name="Inputs", severity="P1", status="warn",
                     detail=detail,
                     fix_hint="Newest PDF >14d old — owner may have stopped feeding the queue.")
    return Check(name="Inputs", severity="info", status="info", detail=detail)


def check_queue_staleness() -> Check:
    """Queued > 14d, no brief built → likely permanent extract_failed."""
    queue = _load(QUEUE_FILE, [])
    if not isinstance(queue, list) or not queue:
        return Check(name="Queue staleness", severity="info", status="info",
                     detail="(queue empty)")
    now = datetime.now()
    stuck = []
    for q in queue:
        if q.get("delivered"):
            continue
        pid = q.get("paper_id", "")
        if not pid:
            continue
        brief_exists = (BRIEFS_DIR / f"{pid}.md").exists()
        if brief_exists:
            continue
        ts = q.get("queued_at") or q.get("added_at") or ""
        try:
            qt = datetime.fromisoformat(ts.split("+")[0])
            if (now - qt).days > 14:
                stuck.append(pid)
        except (ValueError, AttributeError):
            # No timestamp = treat as stuck for safety
            stuck.append(pid)
    if stuck:
        return Check(name="Queue staleness", severity="P1", status="warn",
                     detail=f"{len(stuck)} queued item(s) > 14d with no brief built: "
                            + ", ".join(stuck[:4])
                            + (f" +{len(stuck) - 4}" if len(stuck) > 4 else ""),
                     fix_hint=("Likely extract_failed (scanned PDF or no text layer). "
                               "Check `--builds` log for outcomes, then either "
                               "re-OCR the PDF or drop the item."))
    return Check(name="Queue staleness", severity="info", status="info",
                 detail="(no stuck items)")


# ─────────────────────────── Per-vertical readiness ───────────────────────────

def check_vertical_readiness() -> Check:
    """For each active-subscriber vertical: does it have ≥ MIN_DIGEST_BRIEFS
    undelivered briefs ready? If not, fulfill_cycle silently skips."""
    subs = sub_listing()
    if subs["active"] == 0:
        return Check(name="Vertical readiness", severity="info", status="info",
                     detail="no active subscribers — nothing to verify")
    p = probe_inputs()
    by_v = p["queue_by_vertical"]
    subscribed = set(subs["by_vertical"].keys())
    below = sorted(v for v in subscribed if by_v.get(v, 0) < MIN_DIGEST_BRIEFS)
    if below:
        gap = ", ".join(f"{v}({by_v.get(v, 0)}/{MIN_DIGEST_BRIEFS})" for v in below[:4])
        extra = f" +{len(below) - 4}" if len(below) > 4 else ""
        return Check(name="Vertical readiness", severity="P1", status="warn",
                     detail=f"{len(subscribed) - len(below)}/{len(subscribed)} subscribed verticals "
                            f"have ≥{MIN_DIGEST_BRIEFS} undelivered briefs · gap: {gap}{extra}",
                     fix_hint=(f"Queue ≥{MIN_DIGEST_BRIEFS} new papers per vertical or the "
                               "weekly digest silently skips these subscribers."))
    return Check(name="Vertical readiness", severity="info", status="info",
                 detail=f"all {len(subscribed)} subscribed vertical(s) have ≥{MIN_DIGEST_BRIEFS} undelivered briefs ready")


# ─────────────────────────── Per-vertical extraction streak ───────────────────────────

def check_vertical_health() -> Check:
    s = vertical_summary()
    if s["verticals"] == 0:
        return Check(name="Per-vertical streak", severity="P1", status="warn",
                     detail="no verticals tracked yet — run a cycle first",
                     fix_hint="Run `python3 run_paperbrief_auto.py` once to populate pb_vertical_health.json")
    bad = unhealthy_verticals()
    if bad:
        names = ", ".join(f"{r['vertical']}(-{r['consecutive_skips']})" for r in bad[:5])
        extra = f" +{len(bad) - 5}" if len(bad) > 5 else ""
        return Check(name="Per-vertical streak", severity="P1", status="warn",
                     detail=(f"{s['healthy']}/{s['verticals']} healthy · "
                             f"{s['warning']} vertical(s) with ≥{ALERT_AFTER_SKIPS} skips: "
                             f"{names}{extra}"),
                     fix_hint="Multiple weeks in a row below threshold — see Vertical readiness above.")
    return Check(name="Per-vertical streak", severity="info", status="info",
                 detail=f"{s['healthy']}/{s['verticals']} healthy · "
                        f"all-time sent: {s['total_sent_all_time']}")


# ─────────────────────────── Per-paper failure streaks ───────────────────────────

def check_build_outcomes() -> Check:
    bs = build_outcome_summary()
    if bs["total"] == 0:
        return Check(name="Build outcomes", severity="info", status="info",
                     detail="(no builds logged yet)")
    detail = (f"log={bs['total']}  success={bs['success']}  "
              f"missing_pdf={bs['missing_pdf']}  extract_failed={bs['extract_failed']}")
    rf = bs["repeated_failures"]
    if rf:
        sample = ", ".join(f"{r['paper_id']}(-{r['streak']})" for r in rf[:4])
        extra = f" +{len(rf) - 4}" if len(rf) > 4 else ""
        return Check(name="Build outcomes", severity="P1", status="warn",
                     detail=f"{detail} · repeated_failures: {sample}{extra}",
                     fix_hint=("Same paper_ids keep failing. Inspect PDF (likely "
                               "image-only or corrupt) or drop the queue entry."))
    return Check(name="Build outcomes", severity="info", status="info", detail=detail)


# ─────────────────────────── Output cadence ───────────────────────────

def check_cadence() -> Check:
    if not BRIEFS_DIR.exists():
        return Check(name="Brief output", severity="info", status="info",
                     detail="pb_briefs/ does not exist (no briefs built yet)")
    files = sorted(BRIEFS_DIR.glob("*.md"))
    if not files:
        return Check(name="Brief output", severity="info", status="info", detail="(empty)")
    last = max(files, key=lambda f: f.stat().st_mtime)
    age = (datetime.now() - datetime.fromtimestamp(last.stat().st_mtime)).days
    if age > 21:
        return Check(name="Brief output", severity="P1", status="warn",
                     detail=f"{len(files)} brief(s), newest {age}d old ({last.name})",
                     fix_hint="No new briefs in 3+ weeks — see Build outcomes + Inputs.")
    if age > 10:
        return Check(name="Brief output", severity="P1", status="warn",
                     detail=f"{len(files)} brief(s), newest {age}d old ({last.name})",
                     fix_hint="Weekly cadence slipping — check Build outcomes.")
    return Check(name="Brief output", severity="info", status="info",
                 detail=f"{len(files)} brief(s), newest {age}d old")


# ─────────────────────────── Subscribers + leads ───────────────────────────

def check_subscribers() -> Check:
    out = sub_listing()
    if out["total"] == 0:
        return Check(name="Subscribers", severity="info", status="info",
                     detail="0 — owner-only mode")
    by_v = ", ".join(f"{v}={n}" for v, n in
                     sorted(out["by_vertical"].items(), key=lambda kv: -kv[1])[:4])
    extra = f" +{len(out['by_vertical']) - 4}" if len(out["by_vertical"]) > 4 else ""
    return Check(name="Subscribers", severity="info", status="info",
                 detail=(f"active={out['active']}  pending={out['pending']}  "
                         f"churned={out['churned']}  MRR≈${out['mrr']:.0f}/mo  "
                         f"verticals: {by_v}{extra}"))


def check_leads() -> Check:
    leads = _load(LEADS_FILE, [])
    if not isinstance(leads, list) or not leads:
        return Check(name="Lead pipeline", severity="info", status="info",
                     detail="0 leads — populate pb_leads.json to power trial outreach")
    teased = sum(1 for l in leads if l.get("trial_sent"))
    return Check(name="Lead pipeline", severity="info", status="info",
                 detail=f"{len(leads)} lead(s) · trial_sent={teased} · pending={len(leads) - teased}")


# ─────────────────────────── Aggregate ───────────────────────────

def run_diagnostics() -> dict:
    checks = [
        check_smtp(),
        check_pdf_library(),
        check_inputs(),
        check_queue_staleness(),
        check_vertical_readiness(),
        check_vertical_health(),
        check_build_outcomes(),
        check_cadence(),
        check_subscribers(),
        check_leads(),
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
        print("  ✓ Ready to run. See `--health-report` for per-vertical detail "
              "and `--builds N` for per-paper outcomes.")
    else:
        print("  ✗ Fix P0 items above first — cycle would produce no briefs.")


def main() -> int:
    print("PaperBrief preflight\n")
    report = run_diagnostics()
    print_report(report)
    return 0 if report["summary"]["ready_to_run"] else 1


if __name__ == "__main__":
    sys.exit(main())
