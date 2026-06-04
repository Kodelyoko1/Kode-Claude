"""
Paying-subscriber lifecycle for PantryChef.

The fulfill_cycle() loop in pantrychef.tools reads pc_subscribers.json
and runs build_plan() for every active row. But the file was consumed
and never written — subscribers had to be hand-edited in.

Three plans matching CLAUDE.md product description:
  basic_14         — $14/mo basic weekly plan
  full_family_29   — $29/mo full + family (more recipes / family-size)
  deep_30day_79    — $79 one-time 30-day deep package (terminal one-time)

The deep_30day_79 plan is a one-time deliverable; once shipped, the
owner should `fulfill` it to stop fulfill_cycle from re-running on it.
The two monthly plans keep running through the cron until cancelled.

State file: data/pc_subscribers.json — list of:
  {
    "email":         "user@example.com",
    "name":          "Sam Smith",
    "user_id":       "sam-smith",
    "plan":          "basic_14" | "full_family_29" | "deep_30day_79",
    "status":        "active" | "pending" | "fulfilled" | "churned",
    "added_at":      ISO,
    "activated_at":  ISO,
    "fulfilled_at":  ISO,
    "churned_at":    ISO,
    "notes":         "...",
  }

Log: data/pc_subscription_log.json — append-only audit trail.
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path

DATA_DIR  = Path(__file__).parent.parent / "data"
SUBS_FILE = DATA_DIR / "pc_subscribers.json"
LOG_FILE  = DATA_DIR / "pc_subscription_log.json"

PLANS = {
    "basic_14":       {"price_mo": 14, "one_time": 0,
                       "label": "Basic monthly ($14/mo)"},
    "full_family_29": {"price_mo": 29, "one_time": 0,
                       "label": "Full + family ($29/mo)"},
    "deep_30day_79":  {"price_mo": 0,  "one_time": 79,
                       "label": "30-day deep package ($79 one-time)"},
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


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")


def _find(subs: list, ident: str) -> dict | None:
    ident_lc = (ident or "").strip().lower()
    if not ident_lc:
        return None
    for s in subs:
        if _norm_email(s.get("email", "")) == ident_lc:
            return s
    for s in subs:
        if (s.get("user_id") or "").lower() == ident_lc:
            return s
    return None


def add(email: str, plan: str, name: str = "", user_id: str = "",
        notes: str = "") -> dict:
    email = _norm_email(email)
    if not email or "@" not in email:
        return {"error": "invalid email"}
    if plan not in PLANS:
        return {"error": f"unknown plan; choose from {list(PLANS)}"}
    name = (name or "").strip() or email.split("@")[0]
    user_id = (user_id or "").strip() or _slug(name)
    if not user_id:
        return {"error": "could not derive user_id"}
    subs = _load(SUBS_FILE, [])
    if not isinstance(subs, list):
        subs = []
    if any(_norm_email(s.get("email", "")) == email and s.get("plan") == plan
           for s in subs):
        return {"error": f"{email} already has {plan}"}
    subs.append({
        "email":        email,
        "name":         name,
        "user_id":      user_id,
        "plan":         plan,
        "status":       "pending",
        "added_at":     _now(),
        "activated_at": "",
        "fulfilled_at": "",
        "churned_at":   "",
        "notes":        notes,
    })
    _save(SUBS_FILE, subs)
    _log("added", email, plan=plan, user_id=user_id)
    return {"status": "pending", "email": email, "user_id": user_id, "plan": plan}


def activate(ident: str) -> dict:
    subs = _load(SUBS_FILE, [])
    if not isinstance(subs, list):
        return {"error": "pc_subscribers.json wrong shape"}
    s = _find(subs, ident)
    if not s:
        return {"error": f"no subscriber matches {ident!r}"}
    if s.get("status") in ("active", "fulfilled"):
        return {"error": f"{s.get('email')} already {s.get('status')}"}
    s["status"]       = "active"
    s["activated_at"] = _now()
    _save(SUBS_FILE, subs)
    _log("activated", s.get("email", ""), plan=s.get("plan"))
    return {"status": "active", "email": s.get("email"),
            "user_id": s.get("user_id"), "plan": s.get("plan")}


def fulfill(ident: str) -> dict:
    subs = _load(SUBS_FILE, [])
    if not isinstance(subs, list):
        return {"error": "pc_subscribers.json wrong shape"}
    s = _find(subs, ident)
    if not s:
        return {"error": f"no subscriber matches {ident!r}"}
    plan = s.get("plan", "")
    if PLANS.get(plan, {}).get("price_mo", 0) > 0:
        return {"error": f"{plan} is recurring — use cancel instead of fulfill"}
    s["status"]       = "fulfilled"
    s["fulfilled_at"] = _now()
    _save(SUBS_FILE, subs)
    _log("fulfilled", s.get("email", ""), plan=plan)
    return {"status": "fulfilled", "email": s.get("email"), "plan": plan}


def cancel(ident: str, reason: str = "") -> dict:
    subs = _load(SUBS_FILE, [])
    if not isinstance(subs, list):
        return {"error": "pc_subscribers.json wrong shape"}
    s = _find(subs, ident)
    if not s:
        return {"error": f"no subscriber matches {ident!r}"}
    s["status"]     = "churned"
    s["churned_at"] = _now()
    if reason:
        s["notes"] = (s.get("notes", "") + f"\n[{_now()[:10]}] churn: {reason}").strip()
    _save(SUBS_FILE, subs)
    _log("cancelled", s.get("email", ""), reason=reason)
    return {"status": "churned", "email": s.get("email")}


def listing() -> dict:
    subs = _load(SUBS_FILE, [])
    if not isinstance(subs, list):
        return {"total": 0, "active": 0, "pending": 0, "fulfilled": 0, "churned": 0,
                "mrr": 0.0, "one_time_collected": 0, "by_plan": {}, "subscribers": []}
    by_status = {"active": 0, "pending": 0, "fulfilled": 0, "churned": 0}
    by_plan: dict[str, int] = {}
    mrr = 0.0
    one_time = 0
    for s in subs:
        st = s.get("status", "pending")
        by_status[st] = by_status.get(st, 0) + 1
        plan = s.get("plan", "")
        by_plan[plan] = by_plan.get(plan, 0) + 1
        p = PLANS.get(plan, {})
        if st == "active":
            mrr += p.get("price_mo", 0)
        if st in ("active", "fulfilled"):
            one_time += p.get("one_time", 0)
    return {
        "total":              len(subs),
        **by_status,
        "mrr":                round(mrr, 2),
        "one_time_collected": one_time,
        "by_plan":            by_plan,
        "subscribers":        subs,
    }


def _cli():
    import argparse
    p = argparse.ArgumentParser(description="PantryChef subscriber lifecycle")
    sub = p.add_subparsers(dest="cmd", required=True)
    p_add = sub.add_parser("add", help="Create a pending subscriber")
    p_add.add_argument("email")
    p_add.add_argument("plan", choices=list(PLANS))
    p_add.add_argument("--name", default="")
    p_add.add_argument("--user-id", default="")
    p_add.add_argument("--notes", default="")
    p_act = sub.add_parser("activate", help="Flip pending to active (after payment)")
    p_act.add_argument("ident", help="email OR user_id")
    p_ful = sub.add_parser("fulfill", help="Mark deep_30day_79 as fulfilled")
    p_ful.add_argument("ident")
    p_can = sub.add_parser("cancel", help="Churn subscriber")
    p_can.add_argument("ident")
    p_can.add_argument("--reason", default="")
    sub.add_parser("list", help="List subscribers + MRR + one-time + by-plan")
    args = p.parse_args()
    if args.cmd == "add":
        print(json.dumps(add(args.email, args.plan, args.name, args.user_id, args.notes), indent=2))
    elif args.cmd == "activate":
        print(json.dumps(activate(args.ident), indent=2))
    elif args.cmd == "fulfill":
        print(json.dumps(fulfill(args.ident), indent=2))
    elif args.cmd == "cancel":
        print(json.dumps(cancel(args.ident, args.reason), indent=2))
    elif args.cmd == "list":
        out = listing()
        summ = {k: out[k] for k in (
            "total", "active", "pending", "fulfilled", "churned",
            "mrr", "one_time_collected", "by_plan")}
        print(json.dumps(summ, indent=2))
        for s in out["subscribers"]:
            print(f"  {s.get('status','?'):>9s}  {s.get('plan',''):<16s}  "
                  f"{s.get('user_id',''):<20s}  {s.get('email','')}")


if __name__ == "__main__":
    _cli()
