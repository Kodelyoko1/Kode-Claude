"""
Subscriber lifecycle for CarouselForge.

Three plans matching CLAUDE.md:
  per_carousel_29        $29 one-time per carousel
  monthly_99             $99/mo for 4 carousels (cap surfaced in diagnose)
  monthly_unlimited_297  $297/mo unlimited

State: data/cr_subscribers.json + data/cr_subscription_log.json
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

DATA_DIR  = Path(__file__).parent.parent / "data"
SUBS_FILE = DATA_DIR / "cr_subscribers.json"
LOG_FILE  = DATA_DIR / "cr_subscription_log.json"

PLANS = {
    "per_carousel_29":       {"price_mo": 0,   "one_time": 29,  "monthly_cap": 0,
                              "label": "Per-carousel ($29 one-time)"},
    "monthly_99":            {"price_mo": 99,  "one_time": 0,   "monthly_cap": 4,
                              "label": "Monthly 4-carousel ($99/mo)"},
    "monthly_unlimited_297": {"price_mo": 297, "one_time": 0,   "monthly_cap": -1,
                              "label": "Unlimited monthly ($297/mo)"},
}


def _now() -> str: return datetime.now().isoformat()
def _norm(e: str) -> str: return (e or "").strip().lower()


def _load(path, default):
    if not path.exists(): return default
    try: return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError): return default


def _save(path, data):
    path.parent.mkdir(exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as f: json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except Exception:
        try: os.unlink(tmp)
        except OSError: pass
        raise


def _log(event, email, **extra):
    rec = _load(LOG_FILE, [])
    if not isinstance(rec, list): rec = []
    rec.append({"ts": _now(), "event": event, "email": email, **extra})
    _save(LOG_FILE, rec)


def add(email: str, plan: str, name: str = "", notes: str = "") -> dict:
    email = _norm(email)
    if not email or "@" not in email: return {"error": "invalid email"}
    if plan not in PLANS: return {"error": f"unknown plan; choose from {list(PLANS)}"}
    subs = _load(SUBS_FILE, [])
    if not isinstance(subs, list): subs = []
    if any(_norm(s.get("email", "")) == email and s.get("plan") == plan for s in subs):
        return {"error": f"{email} already has {plan}"}
    subs.append({"email": email, "name": name or email.split("@")[0],
                 "plan": plan, "status": "pending", "added_at": _now(),
                 "activated_at": "", "fulfilled_at": "", "churned_at": "", "notes": notes})
    _save(SUBS_FILE, subs)
    _log("added", email, plan=plan)
    return {"status": "pending", "email": email, "plan": plan}


def activate(email: str) -> dict:
    email = _norm(email)
    subs = _load(SUBS_FILE, [])
    if not isinstance(subs, list): return {"error": "cr_subscribers.json wrong shape"}
    for s in subs:
        if _norm(s.get("email", "")) == email and s.get("status") == "pending":
            s["status"] = "active"; s["activated_at"] = _now()
            _save(SUBS_FILE, subs)
            _log("activated", email, plan=s.get("plan"))
            return {"status": "active", "email": email, "plan": s.get("plan")}
    return {"error": f"no pending subscription for {email}"}


def fulfill(email: str) -> dict:
    email = _norm(email)
    subs = _load(SUBS_FILE, [])
    if not isinstance(subs, list): return {"error": "cr_subscribers.json wrong shape"}
    for s in subs:
        if _norm(s.get("email", "")) == email and s.get("status") == "active":
            plan = s.get("plan", "")
            if PLANS.get(plan, {}).get("price_mo", 0) > 0:
                return {"error": f"{plan} is recurring — use cancel"}
            s["status"] = "fulfilled"; s["fulfilled_at"] = _now()
            _save(SUBS_FILE, subs)
            _log("fulfilled", email, plan=plan)
            return {"status": "fulfilled", "email": email, "plan": plan}
    return {"error": f"no active subscription for {email}"}


def cancel(email: str, reason: str = "") -> dict:
    email = _norm(email)
    subs = _load(SUBS_FILE, [])
    if not isinstance(subs, list): return {"error": "cr_subscribers.json wrong shape"}
    touched = []
    for s in subs:
        if _norm(s.get("email", "")) != email: continue
        if s.get("status") in ("active", "pending"):
            s["status"] = "churned"; s["churned_at"] = _now()
            if reason:
                s["notes"] = (s.get("notes", "") + f"\n[{_now()[:10]}] churn: {reason}").strip()
            touched.append(s.get("plan", ""))
    if not touched: return {"error": f"no active/pending subscription for {email}"}
    _save(SUBS_FILE, subs)
    _log("cancelled", email, plans=touched, reason=reason)
    return {"status": "churned", "email": email, "plans": touched}


def listing() -> dict:
    subs = _load(SUBS_FILE, [])
    if not isinstance(subs, list):
        return {"total": 0, "active": 0, "pending": 0, "fulfilled": 0, "churned": 0,
                "mrr": 0.0, "one_time_collected": 0, "by_plan": {}, "subscribers": []}
    by_status = {"active": 0, "pending": 0, "fulfilled": 0, "churned": 0}
    by_plan: dict[str, int] = {}
    mrr = 0.0; one_time = 0
    for s in subs:
        st = s.get("status", "pending")
        by_status[st] = by_status.get(st, 0) + 1
        plan = s.get("plan", "")
        by_plan[plan] = by_plan.get(plan, 0) + 1
        p = PLANS.get(plan, {})
        if st == "active": mrr += p.get("price_mo", 0)
        if st in ("active", "fulfilled"): one_time += p.get("one_time", 0)
    return {"total": len(subs), **by_status, "mrr": round(mrr, 2),
            "one_time_collected": one_time, "by_plan": by_plan, "subscribers": subs}


def _cli():
    import argparse
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add"); a.add_argument("email"); a.add_argument("plan", choices=list(PLANS)); a.add_argument("--name", default=""); a.add_argument("--notes", default="")
    ac = sub.add_parser("activate"); ac.add_argument("email")
    fu = sub.add_parser("fulfill"); fu.add_argument("email")
    ca = sub.add_parser("cancel"); ca.add_argument("email"); ca.add_argument("--reason", default="")
    sub.add_parser("list")
    args = p.parse_args()
    if args.cmd == "add": print(json.dumps(add(args.email, args.plan, args.name, args.notes), indent=2))
    elif args.cmd == "activate": print(json.dumps(activate(args.email), indent=2))
    elif args.cmd == "fulfill": print(json.dumps(fulfill(args.email), indent=2))
    elif args.cmd == "cancel": print(json.dumps(cancel(args.email, args.reason), indent=2))
    elif args.cmd == "list":
        out = listing()
        print(json.dumps({k: out[k] for k in ("total", "active", "pending", "fulfilled", "churned", "mrr", "one_time_collected", "by_plan")}, indent=2))
        for s in out["subscribers"]:
            print(f"  {s.get('status','?'):>9s}  {s.get('plan',''):<22s}  {s.get('email','')}")


if __name__ == "__main__": _cli()
