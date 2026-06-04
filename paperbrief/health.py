"""
PaperBrief health: per-vertical digest yield + per-paper build outcomes.

PaperBrief is owner-fed through TWO inputs: a queue (pb_queue.json,
listing paper_id + vertical + industry) and a directory of PDFs
(data/pb_pdfs/<paper_id>.pdf). For each queued paper, build_brief()
extracts text via pypdf/PyPDF2, sectionizes, and writes
data/pb_briefs/<paper_id>.md. Then weekly_digest(vertical) bundles
≥3 undelivered briefs and fulfill_cycle() emails the bundle to
subscribers of that vertical.

Silent failure modes the existing run_full_cycle doesn't surface:

  1. PDF missing from pb_pdfs/ for a queued paper. build_brief returns
     `{"error": "missing_pdf"}` and stays in the queue forever.
  2. PDF present but extraction yields <500 chars (image-only scan,
     corrupt file). build_brief returns `{"error": "extract_failed"}`.
     Same stuck-queue outcome.
  3. weekly_digest needs ≥3 undelivered briefs for a vertical. With 2
     briefs ready, fulfill_cycle silently skips that vertical's
     subscribers.
  4. pb_subscribers.json was consumed but never written.

This module tracks two ledgers:
  · pb_vertical_health.json — per-vertical digest cycles
    (consecutive_skips for the <3-undelivered case)
  · pb_build_log.json — append-only per-paper build outcomes
    (success / missing_pdf / extract_failed) — so diagnose can spot
    PDFs that have been failing extraction repeatedly

Env:
  PB_ALERT_AFTER_SKIPS    default 2   — consecutive-skip threshold for P1 warn
  PB_MIN_DIGEST_BRIEFS    default 3   — weekly_digest's gate (must match tools.py)
  PB_BUILD_LOG_MAX        default 500 — cap on rolling per-paper outcome history
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

DATA_DIR        = Path(__file__).parent.parent / "data"
PDF_DIR         = DATA_DIR / "pb_pdfs"
BRIEFS_DIR      = DATA_DIR / "pb_briefs"
VERT_HEALTH     = DATA_DIR / "pb_vertical_health.json"
BUILD_LOG       = DATA_DIR / "pb_build_log.json"
QUEUE_FILE      = DATA_DIR / "pb_queue.json"

ALERT_AFTER_SKIPS = int(os.environ.get("PB_ALERT_AFTER_SKIPS", "2"))
MIN_DIGEST_BRIEFS = int(os.environ.get("PB_MIN_DIGEST_BRIEFS", "3"))
BUILD_LOG_MAX     = int(os.environ.get("PB_BUILD_LOG_MAX", "500"))


def _now() -> str:
    return datetime.now().isoformat()


def _load(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return default


def _save(path: Path, data) -> None:
    path.parent.mkdir(exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except Exception:
        try: os.unlink(tmp)
        except OSError: pass
        raise


def _slug(s: str) -> str:
    return (s or "").strip().lower().replace(" ", "-")


# ─────────────────────────── Per-paper build outcomes ───────────────────────────

def record_build(paper_id: str, outcome: str, detail: str = "") -> None:
    """outcome ∈ {success, missing_pdf, extract_failed}.
    Rolling log capped at BUILD_LOG_MAX entries."""
    if not paper_id:
        return
    log = _load(BUILD_LOG, [])
    if not isinstance(log, list):
        log = []
    log.append({"ts": _now(), "paper_id": paper_id,
                "outcome": outcome, "detail": detail or ""})
    if len(log) > BUILD_LOG_MAX:
        log = log[-BUILD_LOG_MAX:]
    _save(BUILD_LOG, log)


def recent_builds(limit: int = 50) -> list[dict]:
    log = _load(BUILD_LOG, [])
    if not isinstance(log, list):
        return []
    return log[-limit:][::-1]


def build_outcome_summary() -> dict:
    log = _load(BUILD_LOG, [])
    if not isinstance(log, list) or not log:
        return {"total": 0, "success": 0, "missing_pdf": 0, "extract_failed": 0,
                "repeated_failures": []}
    counts = {"success": 0, "missing_pdf": 0, "extract_failed": 0}
    last_outcome: dict[str, str] = {}
    fail_count: dict[str, int]   = {}
    for r in log:
        oc = r.get("outcome", "")
        if oc in counts:
            counts[oc] += 1
        pid = r.get("paper_id", "")
        if not pid:
            continue
        # Track consecutive most-recent failures per paper.
        if oc == "success":
            fail_count[pid] = 0
        else:
            # Reset chain if this is a new run after a success — log is
            # append-only so just re-derive from end.
            pass
        last_outcome[pid] = oc
    # Recompute fail streak per paper from the tail.
    for pid in last_outcome:
        streak = 0
        for r in reversed(log):
            if r.get("paper_id") != pid:
                continue
            if r.get("outcome") == "success":
                break
            streak += 1
        fail_count[pid] = streak
    repeated = sorted(
        [{"paper_id": p, "streak": s} for p, s in fail_count.items() if s >= 2],
        key=lambda x: -x["streak"],
    )
    return {"total": len(log), **counts, "repeated_failures": repeated}


# ─────────────────────────── Per-vertical digest cycles ───────────────────────────

def _load_vert() -> dict:
    d = _load(VERT_HEALTH, {})
    return d if isinstance(d, dict) else {}


def record_vertical(
    vertical: str,
    available_briefs: int,
    sent: int = 0,
    skipped: bool = False,
    skip_reason: str = "",
) -> None:
    v = _slug(vertical)
    if not v:
        return
    health = _load_vert()
    rec = health.get(v, {
        "last_run":          "",
        "last_briefs":       0,
        "last_sent":         0,
        "last_skipped":      False,
        "last_skip_reason":  "",
        "last_nonzero_at":   "",
        "consecutive_skips": 0,
        "total_runs":        0,
        "total_briefs":      0,
        "total_sent":        0,
    })
    rec["last_run"]         = _now()
    rec["last_briefs"]      = available_briefs
    rec["last_sent"]        = sent
    rec["last_skipped"]     = bool(skipped)
    rec["last_skip_reason"] = skip_reason or ""
    rec["total_runs"]      += 1
    rec["total_briefs"]    += max(available_briefs, 0)
    rec["total_sent"]      += max(sent, 0)
    if skipped:
        rec["consecutive_skips"] = rec.get("consecutive_skips", 0) + 1
    else:
        rec["consecutive_skips"] = 0
        rec["last_nonzero_at"]   = _now()
    health[v] = rec
    _save(VERT_HEALTH, health)


def unhealthy_verticals(threshold: int = None) -> list[dict]:
    threshold = ALERT_AFTER_SKIPS if threshold is None else threshold
    health = _load_vert()
    out = []
    for v, rec in health.items():
        if rec.get("consecutive_skips", 0) >= threshold:
            out.append({"vertical": v, **rec})
    return sorted(out, key=lambda r: -r.get("consecutive_skips", 0))


def vertical_summary() -> dict:
    health = _load_vert()
    n = len(health)
    if not n:
        return {"verticals": 0, "healthy": 0, "warning": 0,
                "total_sent_all_time": 0, "alert_threshold": ALERT_AFTER_SKIPS}
    healthy = sum(1 for r in health.values()
                  if r.get("consecutive_skips", 0) < ALERT_AFTER_SKIPS)
    return {
        "verticals":           n,
        "healthy":             healthy,
        "warning":             n - healthy,
        "total_sent_all_time": sum(r.get("total_sent", 0) for r in health.values()),
        "alert_threshold":     ALERT_AFTER_SKIPS,
    }


def report_lines() -> list[str]:
    health = _load_vert()
    if not health:
        return ["(no verticals tracked yet — run a cycle first)"]
    lines = [f"{'VERTICAL':<20s}  {'LAST RUN':<19s}  {'BRIEFS':>6s}  "
             f"{'SENT':>5s}  {'STREAK':>6s}  {'TOTAL_SENT':>10s}"]
    for v, r in sorted(health.items()):
        cs = r.get("consecutive_skips", 0)
        streak = f"-{cs}" if cs else "ok"
        lines.append(
            f"{v[:20]:<20s}  {(r.get('last_run') or '')[:19]:<19s}  "
            f"{r.get('last_briefs',0):>6d}  {r.get('last_sent',0):>5d}  "
            f"{streak:>6s}  {r.get('total_sent',0):>10d}"
            + (f"  skip: {r.get('last_skip_reason','')[:24]}"
               if r.get("last_skipped") and r.get("last_skip_reason") else "")
        )
    return lines


# ─────────────────────────── Input probe ───────────────────────────

def probe_inputs() -> dict:
    """Triangulate queue + PDFs + already-built briefs.

    Returns {
      "ok":  bool,                   — any work is possible
      "queue_total":  N,
      "queue_undelivered": N,
      "queue_by_vertical": {...},
      "pdfs_total":   N,
      "pdfs_newest_age_days": N|None,
      "briefs_total": N,
      "missing_pdfs": [paper_id, ...],   — queued but no PDF on disk
      "orphan_pdfs":  [paper_id, ...],   — PDF on disk but not queued
    }
    """
    queue = _load(QUEUE_FILE, [])
    if not isinstance(queue, list):
        queue = []
    queue_undelivered = [q for q in queue if not q.get("delivered")]
    queue_by_v: dict[str, int] = {}
    for q in queue_undelivered:
        v = _slug(q.get("vertical", ""))
        queue_by_v[v] = queue_by_v.get(v, 0) + 1

    pdf_files: list[Path] = []
    pdfs_age = None
    if PDF_DIR.exists():
        pdf_files = [f for f in PDF_DIR.glob("*.pdf") if f.is_file()]
        if pdf_files:
            newest = max(f.stat().st_mtime for f in pdf_files)
            pdfs_age = (datetime.now() - datetime.fromtimestamp(newest)).days
    pdf_ids = {f.stem for f in pdf_files}

    brief_files: list[Path] = []
    if BRIEFS_DIR.exists():
        brief_files = [f for f in BRIEFS_DIR.glob("*.md") if f.is_file()]

    queued_ids = {q.get("paper_id", "") for q in queue if q.get("paper_id")}
    missing = sorted(qid for qid in queued_ids if qid and qid not in pdf_ids)
    orphan  = sorted(pid for pid in pdf_ids if pid not in queued_ids)

    return {
        "ok": bool(queue) or bool(pdf_files),
        "queue_total":      len(queue),
        "queue_undelivered": len(queue_undelivered),
        "queue_by_vertical": queue_by_v,
        "pdfs_total":       len(pdf_files),
        "pdfs_newest_age_days": pdfs_age,
        "briefs_total":     len(brief_files),
        "missing_pdfs":     missing,
        "orphan_pdfs":      orphan,
        "min_digest_briefs": MIN_DIGEST_BRIEFS,
    }


def _cli():
    import argparse
    p = argparse.ArgumentParser(description="PaperBrief vertical + per-paper health")
    p.add_argument("--probe", action="store_true",
                   help="Triangulate queue + PDFs + built briefs")
    p.add_argument("--builds", type=int, default=0,
                   help="Show the most recent N per-paper build outcomes")
    p.add_argument("--summary-json", action="store_true",
                   help="Emit machine-readable summary")
    args = p.parse_args()
    if args.probe:
        print(json.dumps(probe_inputs(), indent=2))
        return
    if args.builds:
        for r in recent_builds(args.builds):
            print(f"  {r['ts'][:19]}  {r['outcome']:<14s}  {r['paper_id']}"
                  + (f"  {r['detail'][:60]}" if r.get('detail') else ""))
        s = build_outcome_summary()
        print(f"\n  log_total={s['total']}  success={s['success']}  "
              f"missing_pdf={s['missing_pdf']}  extract_failed={s['extract_failed']}")
        if s["repeated_failures"]:
            print(f"  repeated_failures: " +
                  ", ".join(f"{r['paper_id']}(-{r['streak']})" for r in s["repeated_failures"][:5]))
        return
    if args.summary_json:
        print(json.dumps({
            "verticals": vertical_summary(),
            "unhealthy_verticals": unhealthy_verticals(),
            "builds": build_outcome_summary(),
        }, indent=2))
        return
    for line in report_lines():
        print(line)
    s = vertical_summary()
    if s["verticals"]:
        print()
        print(f"  {s['healthy']} healthy / {s['warning']} warning  "
              f"(threshold ≥{s['alert_threshold']} consecutive skips)  "
              f"all-time sent: {s['total_sent_all_time']}")
    bs = build_outcome_summary()
    if bs["total"]:
        print(f"  builds log: total={bs['total']}  success={bs['success']}  "
              f"missing_pdf={bs['missing_pdf']}  extract_failed={bs['extract_failed']}")


if __name__ == "__main__":
    _cli()
