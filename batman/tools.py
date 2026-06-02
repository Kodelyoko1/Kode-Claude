"""
Batman — autonomous self-healing agent fleet manager.
Revenue: $147/mo subscription, $497 quarterly retainer, $997/yr enterprise.

What Batman does (every cycle)
------------------------------
    1. SCAN RUN LOGS — opens the N most recent data/runlog/*.log files,
       extracts per-agent failures (`<agent> failed` lines) and tracebacks
       grouped under the agent panel that emitted them.
    2. VERIFY JSON INTEGRITY — parse-attempts every data/*.json, flags
       files that won't json.load. Excludes files modified in the last 60s
       (might be a live writer).
    3. DETECT STALE AGENTS — reads data/agent_metrics.json; flags any
       agent whose last_run is older than STALE_THRESHOLD_HOURS, with a
       carve-out for known weekly agents.
    4. QUARANTINE — when BATMAN_LIVE=1, copies each corrupted JSON to
       data/.batman_quarantine/<ts>-<name> and replaces it with the agent's
       known-default schema (only for files we have a default for). Without
       BATMAN_LIVE, quarantine is dry-run (files are listed but not moved).
    5. REPORT — writes a markdown digest to data/bm_reports/YYYY-MM-DD.md
       and emails it to the owner.
    6. METRICS — emits one cycle's stats to the ecosystem dashboard.

Why not auto-patch source code? Because v1 stays conservative. Batman
ships actionable diagnostics + safe data-file repair. Code-level
self-patching can be layered on top once we trust the detection layer.

Known default schemas
---------------------
    agent_metrics.json     → {}
    agent_send_log.json    → []
    leads.json             → {} (dict keyed by LEAD-NNNN)
    hd_leads.json          → {"seen_cases": [], "leads": []}
    ps_leads.json          → []
    agent_subscriptions.json → {}
    agent_invoices.json    → []
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))
from autonomous import storage, mailer, billing, metrics

# ============================================================================
# CONFIG
# ============================================================================

AGENT_KEY     = "batman"
ROOT          = Path(__file__).parent.parent
DATA_DIR      = ROOT / "data"
RUNLOG_DIR    = DATA_DIR / "runlog"
QUARANTINE    = DATA_DIR / ".batman_quarantine"
REPORTS_DIR   = DATA_DIR / "bm_reports"

LIVE = os.environ.get("BATMAN_LIVE", "0") == "1"
LOGS_TO_SCAN          = int(os.environ.get("BM_LOG_SCAN_N", "5"))
JSON_LIVE_GUARD_SEC   = int(os.environ.get("BM_LIVE_GUARD_SEC", "60"))
STALE_THRESHOLD_HOURS = int(os.environ.get("BM_STALE_HOURS", "48"))

# Agents whose cadence is weekly (Monday-only in run_all_autonomous_agents.sh).
# These shouldn't trip the staleness alarm during the week.
WEEKLY_AGENTS = {
    "towncrier", "trendscout", "paperbrief", "nichelens", "link_mender",
    "gutenberg_voice", "speedaudit", "courseforge", "localize", "domainscout",
}

# Defaults for known data files. Files NOT listed here are quarantined but
# not auto-replaced — owner must restore manually so we don't destroy data
# we don't understand the shape of.
DEFAULT_SCHEMAS = {
    "agent_metrics.json":       {},
    "agent_send_log.json":      [],
    "leads.json":               {},
    "hd_leads.json":            {"seen_cases": [], "leads": []},
    "ps_leads.json":            [],
    "agent_subscriptions.json": {},
    "agent_invoices.json":      [],
    "cash_buyers.json":         [],
    "email_log.json":           [],
    "outreach_clients.json":    [],
    "outreach_campaigns.json":  [],
    "prospects.json":           [],
    "pitch_log.json":           [],
    "social_posts.json":        [],
}


# ============================================================================
# RUN-LOG SCANNER
# ============================================================================

PANEL_RE = re.compile(r"Wholesale Omniverse — (.+?) ─")
TRACE_RE = re.compile(r"^Traceback \(most recent call last\)", re.M)
FAILED_RE = re.compile(r"\] (\S+) failed\s*$", re.M)


def scan_run_logs(n: int = LOGS_TO_SCAN) -> dict:
    """Walk the N most recent run logs and extract failures + tracebacks.
    Returns {logs_scanned, failures: [{log, agent, reason, ts}],
             tracebacks: [{log, agent, trace}]}."""
    if not RUNLOG_DIR.exists():
        return {"logs_scanned": 0, "failures": [], "tracebacks": []}

    logs = sorted(RUNLOG_DIR.glob("*.log"),
                  key=lambda p: p.stat().st_mtime, reverse=True)[:n]
    failures, tracebacks = [], []

    for log in logs:
        try:
            text = log.read_text(errors="replace")
        except Exception:
            continue

        # 1. Explicit `<agent> failed` lines from the cron script's `|| log "X failed"`.
        for m in FAILED_RE.finditer(text):
            agent = m.group(1)
            ts_match = re.search(r"\[([\d\-: ]+)\] " + re.escape(agent) + " failed",
                                  text[:m.end()])
            failures.append({
                "log":   log.name,
                "agent": agent,
                "ts":    ts_match.group(1) if ts_match else "",
                "reason": "exit-nonzero (failed line in cron)",
            })

        # 2. Python tracebacks — attribute each to the most recent agent panel
        #    above it in the file.
        panel_positions = [(m.group(1).strip(), m.start())
                            for m in PANEL_RE.finditer(text)]
        for m in TRACE_RE.finditer(text):
            pos = m.start()
            # Find the agent panel immediately preceding this traceback
            agent = "?"
            for name, start in panel_positions:
                if start < pos:
                    agent = name
                else:
                    break
            # Capture up to 30 lines of trace
            tail = text[pos:pos + 4000]
            trace_lines = []
            for line in tail.splitlines():
                trace_lines.append(line)
                if len(trace_lines) >= 30:
                    break
                if line and not line[0].isspace() and "Error" in line and len(trace_lines) > 2:
                    break
            tracebacks.append({
                "log":   log.name,
                "agent": agent,
                "trace": "\n".join(trace_lines),
            })

    return {
        "logs_scanned": len(logs),
        "failures":    failures,
        "tracebacks":  tracebacks,
    }


# ============================================================================
# JSON INTEGRITY CHECKER
# ============================================================================

def verify_json_integrity() -> dict:
    """Try-load every data/*.json. Flags files that won't parse, excluding
    those touched within the live-write guard window."""
    checked, corrupted, skipped_live = 0, [], []
    now = time.time()

    for p in sorted(DATA_DIR.glob("*.json")):
        checked += 1
        try:
            age = now - p.stat().st_mtime
        except OSError:
            continue
        try:
            with open(p) as f:
                json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            if age < JSON_LIVE_GUARD_SEC:
                skipped_live.append({"file": p.name, "age_sec": int(age),
                                      "error": type(e).__name__})
            else:
                corrupted.append({"file": p.name, "size": p.stat().st_size,
                                   "error": str(e)[:160]})

    return {
        "json_checked":  checked,
        "corrupted":     corrupted,
        "skipped_live":  skipped_live,
    }


# ============================================================================
# STALE AGENT DETECTOR
# ============================================================================

def find_stale_agents() -> dict:
    """Read agent_metrics.json and flag agents whose last_run is beyond the
    threshold. Weekly agents get a longer leash."""
    try:
        m = json.load(open(DATA_DIR / "agent_metrics.json"))
    except (OSError, json.JSONDecodeError):
        return {"stale_agents": []}

    now = datetime.now()
    stale = []
    for agent, info in m.items():
        last_str = info.get("last_run", "")
        if not last_str:
            continue
        try:
            last = datetime.fromisoformat(last_str)
        except ValueError:
            continue
        threshold = (STALE_THRESHOLD_HOURS * 5 if agent in WEEKLY_AGENTS
                     else STALE_THRESHOLD_HOURS)
        hours_since = (now - last).total_seconds() / 3600
        if hours_since > threshold:
            stale.append({
                "agent":       agent,
                "last_run":    last_str,
                "hours_since": round(hours_since, 1),
                "threshold":   threshold,
                "cadence":     "weekly" if agent in WEEKLY_AGENTS else "daily",
            })
    return {"stale_agents": sorted(stale, key=lambda x: -x["hours_since"])}


# ============================================================================
# QUARANTINE + AUTO-REPAIR
# ============================================================================

def quarantine_and_repair(corrupted: list) -> dict:
    """Move each corrupted file aside, then reset to its default schema if we
    know one. Without BATMAN_LIVE=1 this is dry-run (no filesystem changes)."""
    moved, restored, untouched = [], [], []
    if not corrupted:
        return {"quarantined": 0, "restored": 0, "untouched": []}

    QUARANTINE.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    for entry in corrupted:
        name = entry["file"]
        src = DATA_DIR / name
        if not src.exists():
            continue

        dst = QUARANTINE / f"{ts}-{name}"
        default = DEFAULT_SCHEMAS.get(name)

        if not LIVE:
            # Dry-run: just announce intent
            moved.append({"file": name, "would_move_to": str(dst),
                          "would_restore_default": default is not None})
            continue

        try:
            shutil.copy2(src, dst)
            moved.append({"file": name, "moved_to": str(dst)})
        except Exception as e:
            untouched.append({"file": name, "reason": f"copy failed: {e}"})
            continue

        if default is None:
            untouched.append({"file": name,
                              "reason": "no known default schema; owner must restore"})
            continue

        try:
            with open(src, "w") as f:
                json.dump(default, f, indent=2)
            restored.append(name)
        except Exception as e:
            untouched.append({"file": name, "reason": f"reset failed: {e}"})

    return {
        "quarantined": len(moved),
        "restored":    len(restored),
        "restored_files": restored,
        "untouched":   untouched,
        "moved":       moved,
    }


# ============================================================================
# REPORT BUILDER + DELIVERY
# ============================================================================

def build_report(scan: dict, integrity: dict, stale: dict, repair: dict) -> str:
    today = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        f"# Batman fleet report — {today}",
        "",
        f"**Mode:** {'LIVE (auto-repair enabled)' if LIVE else 'DRY-RUN (no files modified)'}",
        "",
        "## Run-log scan",
        f"- Logs scanned: {scan['logs_scanned']}",
        f"- Failure lines: {len(scan['failures'])}",
        f"- Python tracebacks: {len(scan['tracebacks'])}",
        "",
    ]
    if scan["failures"]:
        lines.append("### Failures")
        for f in scan["failures"][:20]:
            lines.append(f"- `{f['log']}` · **{f['agent']}** · {f['ts']} · {f['reason']}")
        lines.append("")
    if scan["tracebacks"]:
        lines.append("### Tracebacks")
        for t in scan["tracebacks"][:8]:
            last_line = t["trace"].rstrip().split("\n")[-1][:160]
            lines.append(f"- `{t['log']}` · **{t['agent']}** → `{last_line}`")
        lines.append("")

    lines.extend([
        "## JSON integrity",
        f"- Files checked: {integrity['json_checked']}",
        f"- Corrupted: {len(integrity['corrupted'])}",
        f"- Skipped (live-write guard): {len(integrity['skipped_live'])}",
        "",
    ])
    if integrity["corrupted"]:
        lines.append("### Corrupted files")
        for c in integrity["corrupted"]:
            lines.append(f"- `{c['file']}` ({c['size']:,} bytes) — {c['error']}")
        lines.append("")
        lines.append("### Repair actions")
        if repair.get("quarantined"):
            lines.append(f"- Quarantined: **{repair['quarantined']}**")
        if repair.get("restored"):
            lines.append(f"- Restored to default schema: **{repair['restored']}** "
                          f"({', '.join(repair.get('restored_files', []))})")
        for u in repair.get("untouched", []):
            lines.append(f"- ⚠ `{u['file']}` — {u['reason']}")
        lines.append("")

    lines.extend([
        "## Stale agents",
        f"- Stale (over threshold): {len(stale['stale_agents'])}",
        "",
    ])
    if stale["stale_agents"]:
        for a in stale["stale_agents"][:15]:
            lines.append(f"- **{a['agent']}** ({a['cadence']}) — "
                          f"last_run {a['last_run']} "
                          f"({a['hours_since']}h ago, threshold {a['threshold']}h)")
        lines.append("")

    if not (scan["failures"] or scan["tracebacks"] or integrity["corrupted"]
            or stale["stale_agents"]):
        lines.append("## All clear")
        lines.append("Fleet is healthy: no failures, no corrupted data, no stale agents.")

    return "\n".join(lines)


def write_report(body: str) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORTS_DIR / f"{datetime.now():%Y-%m-%d}.md"
    path.write_text(body, encoding="utf-8")
    return path


def deliver_report(report_path: Path, has_findings: bool) -> int:
    """Email the owner. If BM_OWNER_DIGEST_ALWAYS=0 and there are no findings,
    skip the email so we don't spam the owner with all-clear notes."""
    if not has_findings and os.environ.get("BM_OWNER_DIGEST_ALWAYS", "1") == "0":
        return 0
    owner = os.environ.get("BM_OWNER_EMAIL", os.environ.get("SMTP_USER", ""))
    if not owner:
        return 0
    subject = ("Batman fleet report — " +
               ("issues detected" if has_findings else "all clear") +
               f" ({datetime.now():%b %d})")
    body = ("Batman finished its fleet sweep. Full markdown report attached.\n\n"
            "— Batman, Wholesale Omniverse")
    r = mailer.send(AGENT_KEY, owner, subject, body,
                    purpose="fulfillment",
                    attachments=[str(report_path)])
    return 1 if r.get("status") == "sent" else 0


# ============================================================================
# ENTRY POINT
# ============================================================================

def run_full_cycle() -> dict:
    mode = "LIVE" if LIVE else "DRY-RUN"
    print(f"Batman cycle ({mode}): scanning run logs + JSON files…")

    scan      = scan_run_logs()
    integrity = verify_json_integrity()
    stale     = find_stale_agents()
    repair    = quarantine_and_repair(integrity["corrupted"])

    has_findings = bool(scan["failures"] or scan["tracebacks"]
                        or integrity["corrupted"] or stale["stale_agents"])

    report_body = build_report(scan, integrity, stale, repair)
    report_path = write_report(report_body)
    sent = deliver_report(report_path, has_findings)

    rev  = billing.revenue_summary(AGENT_KEY)
    subs = storage.load("bm_subscribers.json", [])
    metrics.record(
        AGENT_KEY,
        prospects_added  = 0,
        outreach_sent    = 0,
        fulfillment_sent = sent,
        active_subs      = sum(1 for s in subs if s.get("status") == "active"),
        mrr              = rev["mrr"],
        total_revenue    = rev["total_paid"],
        # Batman-specific counters
        failures_found   = len(scan["failures"]),
        tracebacks_found = len(scan["tracebacks"]),
        corrupted_files  = len(integrity["corrupted"]),
        stale_agents     = len(stale["stale_agents"]),
        files_quarantined = repair.get("quarantined", 0),
        files_restored   = repair.get("restored", 0),
    )

    return {
        "mode":          mode,
        "logs_scanned":  scan["logs_scanned"],
        "failures":      len(scan["failures"]),
        "tracebacks":    len(scan["tracebacks"]),
        "json_checked":  integrity["json_checked"],
        "corrupted":     len(integrity["corrupted"]),
        "quarantined":   repair.get("quarantined", 0),
        "restored":      repair.get("restored", 0),
        "stale_agents":  len(stale["stale_agents"]),
        "report_path":   str(report_path),
        "report_sent":   sent,
        **rev,
    }
