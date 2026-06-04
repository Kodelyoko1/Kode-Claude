"""
CareerForge health: per-order outcomes + ATS score distribution +
subscription-cap usage tracker.

CareerForge is usage-based: each paid order in cf_orders.json (or each
month of an active monthly_49 subscription) translates into one
tailored resume + cover + ATS match report. fulfill_orders() processes
the queue, but several conditions cause silent per-order skips:

  1. cf_profiles/<user_id>.json missing — fulfill_orders silently
     continues; the customer paid and never gets their resume
  2. Order has neither jd_text nor jd_file → same outcome
  3. jd_file path doesn't resolve under cf_jobs/ → JSONDecode or
     FileNotFound caught upstream by the self-healing wrapper, but
     each subsequent run re-tries the same broken order forever
  4. monthly_49 says "unlimited" but per CLAUDE.md the actual cap is
     ~20/mo. fulfill_orders has no enforcement. A single abusive
     subscriber can ship 200 resumes/mo for $49.

This module tracks per-order outcomes in a rolling log, derives an ATS
score distribution from delivered orders, and counts each
active monthly_49 subscriber's deliveries in the current month so the
diagnose check can flag anyone approaching or over the cap.

State files:
  data/cf_order_log.json    — rolling per-order outcome log
  data/cf_score_log.json    — rolling per-order ATS score history

Env:
  CF_ORDER_LOG_MAX        default 300 — cap on rolling order outcome history
  CF_MONTHLY_CAP          default 20  — monthly_49 expected ceiling
  CF_OVER_CAP_WARN        default 18  — warn-on-approach threshold
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

DATA_DIR     = Path(__file__).parent.parent / "data"
PROFILES_DIR = DATA_DIR / "cf_profiles"
JOB_DIR      = DATA_DIR / "cf_jobs"
OUTPUT_DIR   = DATA_DIR / "cf_resumes"
LEADS_FILE   = DATA_DIR / "cf_leads.json"
ORDERS_FILE  = DATA_DIR / "cf_orders.json"
ORDER_LOG    = DATA_DIR / "cf_order_log.json"
SCORE_LOG    = DATA_DIR / "cf_score_log.json"

ORDER_LOG_MAX  = int(os.environ.get("CF_ORDER_LOG_MAX", "300"))
MONTHLY_CAP    = int(os.environ.get("CF_MONTHLY_CAP", "20"))
OVER_CAP_WARN  = int(os.environ.get("CF_OVER_CAP_WARN", "18"))

VALID_ORDER_OUTCOMES = {"success", "no_profile", "no_jd", "no_email", "mail_failed"}


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


def _month_key(ts: str = "") -> str:
    if ts:
        return ts[:7]
    return datetime.now().strftime("%Y-%m")


# ─────────────────────────── Per-order outcomes ───────────────────────────

def record_order(user_id: str, outcome: str, order_id: str = "",
                 detail: str = "") -> None:
    """outcome ∈ {success, no_profile, no_jd, no_email}."""
    if not user_id:
        return
    log = _load(ORDER_LOG, [])
    if not isinstance(log, list):
        log = []
    log.append({
        "ts":       _now(),
        "user_id":  user_id,
        "order_id": order_id or "",
        "outcome":  outcome,
        "detail":   detail or "",
    })
    if len(log) > ORDER_LOG_MAX:
        log = log[-ORDER_LOG_MAX:]
    _save(ORDER_LOG, log)


def recent_orders(limit: int = 50) -> list[dict]:
    log = _load(ORDER_LOG, [])
    if not isinstance(log, list):
        return []
    return log[-limit:][::-1]


def order_outcome_summary() -> dict:
    log = _load(ORDER_LOG, [])
    if not isinstance(log, list) or not log:
        return {"total": 0, **{oc: 0 for oc in VALID_ORDER_OUTCOMES}}
    counts = {oc: 0 for oc in VALID_ORDER_OUTCOMES}
    for r in log:
        oc = r.get("outcome", "")
        if oc in counts:
            counts[oc] += 1
    return {"total": len(log), **counts}


def stuck_mail_failed(min_attempts: int = 3) -> list[dict]:
    """Per-order mail_failed retries — surfaces orders that keep failing
    the mailer step. order_id ↦ {"attempts": N, "user_id": ..., "last_ts": ...}."""
    log = _load(ORDER_LOG, [])
    if not isinstance(log, list):
        return []
    by_order: dict[str, dict] = {}
    for r in log:
        if r.get("outcome") != "mail_failed":
            continue
        oid = r.get("order_id") or f"<no_id>:{r.get('user_id','')}"
        rec = by_order.setdefault(oid, {"attempts": 0, "user_id": r.get("user_id", ""),
                                        "last_ts": "", "last_detail": ""})
        rec["attempts"] += 1
        rec["last_ts"] = r.get("ts", "")
        rec["last_detail"] = r.get("detail", "")
    return sorted(
        [{"order_id": oid, **rec} for oid, rec in by_order.items() if rec["attempts"] >= min_attempts],
        key=lambda r: -r["attempts"],
    )


# ─────────────────────────── ATS score distribution ───────────────────────────

def record_score(user_id: str, score: int) -> None:
    if not user_id or not isinstance(score, (int, float)):
        return
    log = _load(SCORE_LOG, [])
    if not isinstance(log, list):
        log = []
    log.append({"ts": _now(), "user_id": user_id, "score": int(score)})
    if len(log) > ORDER_LOG_MAX:
        log = log[-ORDER_LOG_MAX:]
    _save(SCORE_LOG, log)


def score_summary() -> dict:
    log = _load(SCORE_LOG, [])
    if not isinstance(log, list) or not log:
        return {"total": 0, "avg": None, "dist": {"90-100": 0, "75-89": 0,
                                                  "50-74": 0, "<50": 0}}
    dist = {"90-100": 0, "75-89": 0, "50-74": 0, "<50": 0}
    s = 0
    for r in log:
        v = r.get("score", -1)
        if not isinstance(v, (int, float)) or v < 0:
            continue
        s += v
        if v >= 90: dist["90-100"] += 1
        elif v >= 75: dist["75-89"] += 1
        elif v >= 50: dist["50-74"] += 1
        else: dist["<50"] += 1
    return {"total": len(log), "avg": round(s / len(log), 1), "dist": dist}


# ─────────────────────────── Subscription usage ───────────────────────────

def monthly_usage_per_user(month: str = "") -> dict[str, int]:
    """Count successful orders per user_id in the given month.
    Defaults to the current YYYY-MM."""
    month = month or _month_key()
    log = _load(ORDER_LOG, [])
    if not isinstance(log, list):
        return {}
    counts: dict[str, int] = {}
    for r in log:
        if r.get("outcome") != "success":
            continue
        if _month_key(r.get("ts", "")) != month:
            continue
        u = r.get("user_id", "")
        if not u:
            continue
        counts[u] = counts.get(u, 0) + 1
    return counts


def users_over_threshold(threshold: int = None) -> list[dict]:
    threshold = OVER_CAP_WARN if threshold is None else threshold
    usage = monthly_usage_per_user()
    return sorted(
        [{"user_id": u, "count": n, "over_cap": n > MONTHLY_CAP}
         for u, n in usage.items() if n >= threshold],
        key=lambda r: -r["count"],
    )


# ─────────────────────────── Input probe ───────────────────────────

def probe_inputs() -> dict:
    """Triangulate profiles + queued orders + jobs + leads.

    Returns {
      "ok":                  bool,
      "profiles":            N,
      "orders_total":        N,
      "orders_paid_pending": N,
      "orders_delivered":    N,
      "jobs_files":          N,
      "leads_total":         N,
      "leads_ready":         N,   — has both profile_data + jd_text
      "orders_missing_profile": [user_id, ...],
    }
    """
    profiles = []
    if PROFILES_DIR.exists():
        profiles = [f for f in PROFILES_DIR.glob("*.json") if f.is_file()]
    profile_ids = {f.stem for f in profiles}

    orders = _load(ORDERS_FILE, [])
    if not isinstance(orders, list):
        orders = []
    paid_pending = [o for o in orders
                    if o.get("status") == "paid" and not o.get("delivered_at")]
    delivered = [o for o in orders if o.get("delivered_at")]
    missing_profile = sorted({
        o.get("user_id", "?") for o in paid_pending
        if o.get("user_id") and o.get("user_id") not in profile_ids
    })

    jobs = []
    if JOB_DIR.exists():
        jobs = [f for f in JOB_DIR.iterdir() if f.is_file()]

    leads = _load(LEADS_FILE, [])
    if not isinstance(leads, list):
        leads = []
    leads_ready = sum(1 for l in leads
                      if l.get("email") and l.get("profile_data") and l.get("jd_text"))

    return {
        "ok":                     len(profiles) > 0 or len(orders) > 0,
        "profiles":               len(profiles),
        "orders_total":           len(orders),
        "orders_paid_pending":    len(paid_pending),
        "orders_delivered":       len(delivered),
        "jobs_files":             len(jobs),
        "leads_total":            len(leads),
        "leads_ready":            leads_ready,
        "orders_missing_profile": missing_profile,
        "monthly_cap":            MONTHLY_CAP,
        "over_cap_warn":          OVER_CAP_WARN,
    }


def _cli():
    import argparse
    p = argparse.ArgumentParser(description="CareerForge health + probes")
    p.add_argument("--probe",        action="store_true",
                   help="Triangulate profiles + orders + jobs + leads")
    p.add_argument("--orders", type=int, default=0,
                   help="Show last N per-order outcomes")
    p.add_argument("--scores", action="store_true",
                   help="ATS score distribution from delivered orders")
    p.add_argument("--usage",  action="store_true",
                   help="Per-user usage in the current month")
    p.add_argument("--summary-json", action="store_true")
    args = p.parse_args()
    if args.probe:
        print(json.dumps(probe_inputs(), indent=2))
        return
    if args.orders:
        for r in recent_orders(args.orders):
            print(f"  {r['ts'][:19]}  {r['outcome']:<11s}  "
                  f"{r['user_id']:<20s}  {r.get('detail','')[:60]}")
        s = order_outcome_summary()
        print(f"\n  log_total={s['total']}  success={s['success']}  "
              f"no_profile={s['no_profile']}  no_jd={s['no_jd']}  no_email={s['no_email']}")
        return
    if args.scores:
        s = score_summary()
        print(f"  total={s['total']}  avg={s['avg']}")
        for bucket, n in s["dist"].items():
            print(f"  {bucket:<8s}  {n}")
        return
    if args.usage:
        usage = monthly_usage_per_user()
        if not usage:
            print("(no usage recorded this month)")
        else:
            for u, n in sorted(usage.items(), key=lambda kv: -kv[1]):
                gate = "OVER" if n > MONTHLY_CAP else "warn" if n >= OVER_CAP_WARN else "ok"
                print(f"  {u:<24s}  {n:>3d}/{MONTHLY_CAP}  [{gate}]")
        return
    if args.summary_json:
        print(json.dumps({
            "orders":  order_outcome_summary(),
            "scores":  score_summary(),
            "usage":   monthly_usage_per_user(),
            "alerts":  users_over_threshold(),
        }, indent=2))
        return
    s = order_outcome_summary()
    sc = score_summary()
    print(f"  orders log:  total={s['total']}  success={s['success']}  "
          f"no_profile={s['no_profile']}  no_jd={s['no_jd']}")
    if sc["total"]:
        print(f"  scores log:  total={sc['total']}  avg={sc['avg']}  dist " +
              "  ".join(f"{k}={v}" for k, v in sc["dist"].items()))
    over = users_over_threshold()
    if over:
        print(f"  high usage:  " +
              ", ".join(f"{r['user_id']}({r['count']}/{MONTHLY_CAP})" for r in over[:5]))


if __name__ == "__main__":
    _cli()
