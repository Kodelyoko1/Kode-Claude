"""
Per-vertical subscription lifecycle for PaperBrief.

fulfill_cycle() already reads pb_subscribers.json and emails every
active subscriber the digest for their vertical. The file was consumed
but never written — this module fills that gap.

Three plans matching CLAUDE.md product description:
  monthly_39     — $39/mo per vertical
  annual_399     — $399/yr per vertical (≈ $33.25/mo equivalent)
  enterprise_999 — $999/yr enterprise (≈ $83.25/mo equivalent; includes
                   all verticals + early access)

A single email can subscribe to multiple verticals at different plans,
so the join key is (email, vertical). The enterprise plan typically
covers all verticals; the convention here is to add one row per
vertical the subscriber wants tracked.

State file: data/pb_subscribers.json — list of:
  {
    "email":        "research@example.com",
    "vertical":     "ai-safety",
    "plan":         "monthly_39" | "annual_399" | "enterprise_999",
    "status":       "active" | "pending" | "churned",
    "added_at":     ISO,
    "activated_at": ISO,
    "churned_at":   ISO,
    "notes":        "...",
  }

Log: data/pb_subscription_log.json — append-only audit trail.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from collections import Counter
from datetime import datetime
from pathlib import Path

DATA_DIR  = Path(__file__).parent.parent / "data"
SUBS_FILE = DATA_DIR / "pb_subscribers.json"
LOG_FILE  = DATA_DIR / "pb_subscription_log.json"

PLANS = {
    "monthly_39":     {"price_mo": 39,    "label": "Monthly per-vertical"},
    "annual_399":     {"price_mo": 33.25, "label": "Annual prepaid ($399/yr)"},
    "enterprise_999": {"price_mo": 83.25, "label": "Enterprise ($999/yr per vertical)"},
}


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


def _log(event: str, email: str, **extra) -> None:
    rec = _load(LOG_FILE, [])
    if not isinstance(rec, list):
        rec = []
    rec.append({"ts": _now(), "event": event, "email": email, **extra})
    _save(LOG_FILE, rec)


def _norm_email(email: str) -> str:
    return (email or "").strip().lower()


def _norm_vertical(v: str) -> str:
    return (v or "").strip().lower().replace(" ", "-")


def add(email: str, vertical: str, plan: str, notes: str = "") -> dict:
    email    = _norm_email(email)
    vertical = _norm_vertical(vertical)
    if not email or "@" not in email:
        return {"error": "invalid email"}
    if not vertical:
        return {"error": "vertical is required"}
    if plan not in PLANS:
        return {"error": f"unknown plan; choose from {list(PLANS)}"}
    subs = _load(SUBS_FILE, [])
    if not isinstance(subs, list):
        subs = []
    for s in subs:
        if (_norm_email(s.get("email", "")) == email
            and _norm_vertical(s.get("vertical", "")) == vertical):
            if s.get("status") == "churned":
                s["status"]     = "pending"
                s["plan"]       = plan
                s["added_at"]   = _now()
                s["churned_at"] = ""
                _save(SUBS_FILE, subs)
                _log("re-subscribed", email, vertical=vertical, plan=plan)
                return {"status": "pending", "email": email, "vertical": vertical, "plan": plan}
            return {"error": f"{email} already subscribed to {vertical}"}
    subs.append({
        "email":        email,
        "vertical":     vertical,
        "plan":         plan,
        "status":       "pending",
        "added_at":     _now(),
        "activated_at": "",
        "churned_at":   "",
        "notes":        notes,
    })
    _save(SUBS_FILE, subs)
    _log("added", email, vertical=vertical, plan=plan)
    return {"status": "pending", "email": email, "vertical": vertical, "plan": plan}


def activate(email: str, vertical: str = "") -> dict:
    email    = _norm_email(email)
    vertical = _norm_vertical(vertical)
    subs = _load(SUBS_FILE, [])
    if not isinstance(subs, list):
        return {"error": "pb_subscribers.json wrong shape"}
    matches = [s for s in subs
               if _norm_email(s.get("email", "")) == email
               and s.get("status") == "pending"
               and (not vertical or _norm_vertical(s.get("vertical", "")) == vertical)]
    if not matches:
        return {"error": f"no pending subscription for {email}"
                          + (f" / {vertical}" if vertical else "")}
    if len(matches) > 1 and not vertical:
        return {"error": f"{email} has multiple pending — pass --vertical to disambiguate "
                         f"(verticals: {[s.get('vertical') for s in matches]})"}
    s = matches[0]
    s["status"]       = "active"
    s["activated_at"] = _now()
    _save(SUBS_FILE, subs)
    _log("activated", email, vertical=s.get("vertical", ""))
    return {"status": "active", "email": email,
            "vertical": s.get("vertical"), "plan": s.get("plan")}


def cancel(email: str, vertical: str = "", reason: str = "") -> dict:
    email    = _norm_email(email)
    vertical = _norm_vertical(vertical)
    subs = _load(SUBS_FILE, [])
    if not isinstance(subs, list):
        return {"error": "pb_subscribers.json wrong shape"}
    touched = []
    for s in subs:
        if _norm_email(s.get("email", "")) != email:
            continue
        if vertical and _norm_vertical(s.get("vertical", "")) != vertical:
            continue
        if s.get("status") in ("active", "pending"):
            s["status"]     = "churned"
            s["churned_at"] = _now()
            if reason:
                s["notes"] = (s.get("notes", "") + f"\n[{_now()[:10]}] churn: {reason}").strip()
            touched.append(s.get("vertical", ""))
    if not touched:
        return {"error": f"no active/pending subscription for {email}"
                          + (f" / {vertical}" if vertical else "")}
    _save(SUBS_FILE, subs)
    _log("cancelled", email, verticals=touched, reason=reason)
    return {"status": "churned", "email": email, "verticals": touched}


def listing(vertical: str = "") -> dict:
    vertical = _norm_vertical(vertical)
    subs = _load(SUBS_FILE, [])
    if not isinstance(subs, list):
        return {"total": 0, "active": 0, "pending": 0, "churned": 0,
                "by_vertical": {}, "mrr": 0.0, "subscribers": []}
    rows = [s for s in subs if (not vertical or _norm_vertical(s.get("vertical", "")) == vertical)]
    active = [s for s in rows if s.get("status") == "active"]
    mrr = sum(PLANS.get(s.get("plan", ""), {}).get("price_mo", 0) for s in active)
    by_v = Counter(_norm_vertical(s.get("vertical", "")) for s in active)
    return {
        "total":       len(rows),
        "active":      len(active),
        "pending":     sum(1 for s in rows if s.get("status") == "pending"),
        "churned":     sum(1 for s in rows if s.get("status") == "churned"),
        "by_vertical": dict(by_v),
        "mrr":         round(mrr, 2),
        "subscribers": rows,
    }


def _cli():
    import argparse
    p = argparse.ArgumentParser(description="PaperBrief subscriber lifecycle")
    sub = p.add_subparsers(dest="cmd", required=True)
    p_add = sub.add_parser("add", help="Create a pending subscriber")
    p_add.add_argument("email")
    p_add.add_argument("vertical")
    p_add.add_argument("plan", choices=list(PLANS))
    p_add.add_argument("--notes", default="")
    p_act = sub.add_parser("activate", help="Flip pending subscriber to active")
    p_act.add_argument("email")
    p_act.add_argument("--vertical", default="")
    p_can = sub.add_parser("cancel", help="Churn active/pending subscription(s)")
    p_can.add_argument("email")
    p_can.add_argument("--vertical", default="",
                       help="Restrict to one vertical; default: churn all of this email's rows")
    p_can.add_argument("--reason", default="")
    p_lis = sub.add_parser("list", help="List subscribers + MRR + per-vertical counts")
    p_lis.add_argument("--vertical", default="")
    args = p.parse_args()
    if args.cmd == "add":
        print(json.dumps(add(args.email, args.vertical, args.plan, args.notes), indent=2))
    elif args.cmd == "activate":
        print(json.dumps(activate(args.email, args.vertical), indent=2))
    elif args.cmd == "cancel":
        print(json.dumps(cancel(args.email, args.vertical, args.reason), indent=2))
    elif args.cmd == "list":
        out = listing(args.vertical)
        out_summary = {k: out[k] for k in (
            "total", "active", "pending", "churned", "by_vertical", "mrr")}
        print(json.dumps(out_summary, indent=2))
        for s in out["subscribers"]:
            print(f"  {s.get('status','?'):>8s}  {s.get('plan',''):<16s}  "
                  f"{s.get('vertical',''):<20s}  {s.get('email','')}")


if __name__ == "__main__":
    _cli()
