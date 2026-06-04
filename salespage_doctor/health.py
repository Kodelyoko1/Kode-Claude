"""
SalesPageDoctor health: per-Bing-query yield + per-audit outcome log.

SalesPageDoctor auto-scrapes via Bing dorks (DEFAULT_PROSPECT_QUERIES
rotates one per day in tools.py) → fetches each candidate page →
extracts a contact email → audits the URL with a heuristic checker →
emails a free 3-issue preview to the contact.

Three silent failure modes the existing run_full_cycle doesn't surface:

  1. Bing layout/anti-bot changes break _bing_search() for one or all
     queries. Same gap link_mender already flagged: aggregate count
     looks low but no error fires. We track per-query streaks so the
     diagnose check can attribute the rot to a specific dork.
  2. Target sites start blocking our UA (or egress is broken) →
     _fetch() returns "" → audit_salespage returns
     {"error": "fetch_failed"}. acquire_cycle stamps the prospect as
     "audit_error_fetch_failed" and moves on. No alert fires when the
     ratio of fetch_failed climbs.
  3. Audits return predominantly high scores (≥85 → "high_score_skip")
     — looks like success but actually means we're targeting creators
     whose pages don't need fixing. Worth surfacing as info.

State files (separated because the lifecycles are different):
  data/spd_query_health.json   — dict keyed by query string
  data/spd_audit_log.json      — rolling per-URL outcome log

Env:
  SPD_ALERT_AFTER_ZEROS    default 3   — per-query zero-streak threshold
  SPD_AUDIT_LOG_MAX        default 200 — cap on rolling outcome history
  SPD_EGRESS_PROBE         default https://httpbin.org/get
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

DATA_DIR     = Path(__file__).parent.parent / "data"
QUERY_HEALTH = DATA_DIR / "spd_query_health.json"
AUDIT_LOG    = DATA_DIR / "spd_audit_log.json"

ALERT_AFTER_ZEROS = int(os.environ.get("SPD_ALERT_AFTER_ZEROS", "3"))
AUDIT_LOG_MAX     = int(os.environ.get("SPD_AUDIT_LOG_MAX", "200"))
EGRESS_PROBE_URL  = os.environ.get("SPD_EGRESS_PROBE", "https://httpbin.org/get")


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


# ─────────────────────────── Per-query Bing yield ───────────────────────────

def record_query(query: str, results: int, discovered: int, error: str = "") -> None:
    """Persist one Bing query attempt. `results` = raw Bing hits parsed,
    `discovered` = new prospects added (post-filter, post-fetch, post-email-extract)."""
    if not query:
        return
    health = _load(QUERY_HEALTH, {})
    if not isinstance(health, dict):
        health = {}
    rec = health.get(query, {
        "last_run":          "",
        "last_results":      0,
        "last_discovered":   0,
        "last_nonzero_at":   "",
        "consecutive_zeros": 0,
        "total_runs":        0,
        "total_results":     0,
        "total_discovered":  0,
        "last_error":        "",
    })
    rec["last_run"]         = _now()
    rec["last_results"]     = results
    rec["last_discovered"]  = discovered
    rec["last_error"]       = error or ""
    rec["total_runs"]      += 1
    rec["total_results"]   += max(results, 0)
    rec["total_discovered"] += max(discovered, 0)
    if results > 0:
        rec["last_nonzero_at"]   = _now()
        rec["consecutive_zeros"] = 0
    else:
        rec["consecutive_zeros"] = rec.get("consecutive_zeros", 0) + 1
    health[query] = rec
    _save(QUERY_HEALTH, health)


def unhealthy_queries(threshold: int = None) -> list[dict]:
    threshold = ALERT_AFTER_ZEROS if threshold is None else threshold
    health = _load(QUERY_HEALTH, {})
    if not isinstance(health, dict):
        return []
    out = []
    for q, rec in health.items():
        if rec.get("consecutive_zeros", 0) >= threshold:
            out.append({"query": q, **rec})
    return sorted(out, key=lambda r: -r.get("consecutive_zeros", 0))


def query_summary() -> dict:
    health = _load(QUERY_HEALTH, {})
    if not isinstance(health, dict) or not health:
        return {"queries": 0, "healthy": 0, "warning": 0,
                "total_discovered_all_time": 0, "alert_threshold": ALERT_AFTER_ZEROS}
    healthy = sum(1 for r in health.values()
                  if r.get("consecutive_zeros", 0) < ALERT_AFTER_ZEROS)
    return {
        "queries":                  len(health),
        "healthy":                  healthy,
        "warning":                  len(health) - healthy,
        "total_discovered_all_time": sum(r.get("total_discovered", 0) for r in health.values()),
        "alert_threshold":          ALERT_AFTER_ZEROS,
    }


def query_report_lines() -> list[str]:
    health = _load(QUERY_HEALTH, {})
    if not isinstance(health, dict) or not health:
        return ["(no queries tracked yet — run a cycle first)"]
    lines = [f"{'QUERY':<48s}  {'LAST RUN':<19s}  {'RES':>4s}  "
             f"{'NEW':>4s}  {'STREAK':>6s}  {'TOTAL':>6s}"]
    for q, r in sorted(health.items()):
        cz = r.get("consecutive_zeros", 0)
        streak = f"-{cz}" if cz else "ok"
        lines.append(
            f"{q[:48]:<48s}  {(r.get('last_run') or '')[:19]:<19s}  "
            f"{r.get('last_results',0):>4d}  {r.get('last_discovered',0):>4d}  "
            f"{streak:>6s}  {r.get('total_discovered',0):>6d}"
            + (f"  err: {r.get('last_error','')[:30]}" if r.get("last_error") else "")
        )
    return lines


# ─────────────────────────── Audit outcome log ───────────────────────────

VALID_OUTCOMES = {
    "success", "fetch_failed", "bs4_missing", "high_score_skip",
}


def record_audit(url: str, outcome: str, score: int = -1, issue_count: int = 0,
                 detail: str = "") -> None:
    """outcome ∈ {success, fetch_failed, bs4_missing, high_score_skip}.
    score = -1 for outcomes where no score was computed."""
    if not url:
        return
    log = _load(AUDIT_LOG, [])
    if not isinstance(log, list):
        log = []
    log.append({
        "ts":          _now(),
        "url":         url,
        "outcome":     outcome,
        "score":       score,
        "issue_count": issue_count,
        "detail":      detail or "",
    })
    if len(log) > AUDIT_LOG_MAX:
        log = log[-AUDIT_LOG_MAX:]
    _save(AUDIT_LOG, log)


def recent_audits(limit: int = 50) -> list[dict]:
    log = _load(AUDIT_LOG, [])
    if not isinstance(log, list):
        return []
    return log[-limit:][::-1]


def audit_outcome_summary() -> dict:
    log = _load(AUDIT_LOG, [])
    if not isinstance(log, list) or not log:
        return {"total": 0, "success": 0, "fetch_failed": 0,
                "bs4_missing": 0, "high_score_skip": 0,
                "score_dist": {"90-100": 0, "75-89": 0, "50-74": 0, "<50": 0},
                "avg_score": None}
    counts = {oc: 0 for oc in VALID_OUTCOMES}
    score_dist = {"90-100": 0, "75-89": 0, "50-74": 0, "<50": 0}
    scored_sum = 0
    scored_n   = 0
    for r in log:
        oc = r.get("outcome", "")
        if oc in counts:
            counts[oc] += 1
        s = r.get("score", -1)
        if isinstance(s, (int, float)) and s >= 0:
            scored_sum += s
            scored_n += 1
            if s >= 90: score_dist["90-100"] += 1
            elif s >= 75: score_dist["75-89"] += 1
            elif s >= 50: score_dist["50-74"] += 1
            else: score_dist["<50"] += 1
    return {
        "total":           len(log),
        **counts,
        "score_dist":      score_dist,
        "avg_score":       round(scored_sum / scored_n, 1) if scored_n else None,
    }


# ─────────────────────────── Probes ───────────────────────────

def probe_egress() -> dict:
    """Single HTTP egress check. Catches the case where the container's
    outbound network is broken or DNS dead — every _fetch() would return ""
    after that and audits would universally fetch_fail."""
    try:
        import requests
    except ImportError:
        return {"ok": False, "error": "requests not importable"}
    try:
        r = requests.get(EGRESS_PROBE_URL, timeout=8)
        if r.status_code == 200:
            return {"ok": True, "probe": EGRESS_PROBE_URL,
                    "status": r.status_code, "bytes": len(r.content)}
        return {"ok": False, "probe": EGRESS_PROBE_URL,
                "status": r.status_code, "error": f"HTTP {r.status_code}"}
    except Exception as e:
        return {"ok": False, "probe": EGRESS_PROBE_URL,
                "error": f"{type(e).__name__}: {str(e)[:160]}"}


def probe_bing(query: str = "") -> dict:
    """Consume one Bing query end-to-end without persisting it or
    discovering prospects. Returns parsed-result count + any error."""
    # Lazy import to keep `health.py` importable even when requests/bs4 are missing.
    try:
        from salespage_doctor.tools import _bing_search, DEFAULT_PROSPECT_QUERIES
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    q = query or DEFAULT_PROSPECT_QUERIES[datetime.now().day % len(DEFAULT_PROSPECT_QUERIES)]
    try:
        results = _bing_search(q, n=10)
        return {"ok": len(results) > 0, "query": q, "results": len(results)}
    except Exception as e:
        return {"ok": False, "query": q, "error": f"{type(e).__name__}: {str(e)[:160]}"}


def _cli():
    import argparse
    p = argparse.ArgumentParser(description="SalesPageDoctor health + probes")
    p.add_argument("--probe-bing",   action="store_true")
    p.add_argument("--probe-egress", action="store_true")
    p.add_argument("--audits", type=int, default=0,
                   help="Show the most recent N audit outcomes")
    p.add_argument("--summary-json", action="store_true")
    args = p.parse_args()
    if args.probe_bing:
        print(json.dumps(probe_bing(), indent=2))
        return
    if args.probe_egress:
        print(json.dumps(probe_egress(), indent=2))
        return
    if args.audits:
        for r in recent_audits(args.audits):
            print(f"  {r['ts'][:19]}  {r['outcome']:<15s}  "
                  f"score={r['score']:>3d}  issues={r['issue_count']:>2d}  {r['url'][:60]}")
        s = audit_outcome_summary()
        print(f"\n  log_total={s['total']}  success={s['success']}  "
              f"fetch_failed={s['fetch_failed']}  bs4_missing={s['bs4_missing']}  "
              f"high_score_skip={s['high_score_skip']}  avg_score={s['avg_score']}")
        return
    if args.summary_json:
        print(json.dumps({
            "queries": query_summary(),
            "unhealthy_queries": unhealthy_queries(),
            "audits":  audit_outcome_summary(),
        }, indent=2))
        return
    for line in query_report_lines():
        print(line)
    s = query_summary()
    if s["queries"]:
        print()
        print(f"  {s['healthy']} healthy / {s['warning']} warning  "
              f"(threshold ≥{s['alert_threshold']} consecutive zeros)  "
              f"all-time discovered: {s['total_discovered_all_time']}")
    a = audit_outcome_summary()
    if a["total"]:
        print(f"  audits log: total={a['total']}  success={a['success']}  "
              f"fetch_failed={a['fetch_failed']}  high_score_skip={a['high_score_skip']}  "
              f"avg_score={a['avg_score']}")
        print(f"  score dist: " + "  ".join(f"{k}={v}" for k, v in a["score_dist"].items()))


if __name__ == "__main__":
    _cli()
