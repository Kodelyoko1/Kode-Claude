"""CourseForge subscribers. Self-publish kit ($29) · Done-for-you ($99) · White-label monthly ($297/mo)."""
from __future__ import annotations
import json, os, tempfile
from datetime import datetime
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
SUBS = DATA_DIR / "co_subscribers.json"
LOG  = DATA_DIR / "co_subscription_log.json"

PLANS = {
    "kit_29": {
        "price_mo": 0,
        "one_time": 29,
        "label": "Self-publish kit ($29)"
    },
    "done_for_you_99": {
        "price_mo": 0,
        "one_time": 99,
        "label": "Done-for-you ($99)"
    },
    "white_label_297": {
        "price_mo": 297,
        "one_time": 0,
        "label": "White-label monthly ($297/mo)"
    }
}


def _now(): return datetime.now().isoformat()
def _norm(e): return (e or "").strip().lower()
def _load(p, d):
    if not p.exists(): return d
    try: return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError): return d
def _save(p, d):
    p.parent.mkdir(exist_ok=True)
    fd, t = tempfile.mkstemp(prefix=f".{p.name}.", suffix=".tmp", dir=p.parent)
    try:
        with os.fdopen(fd, "w") as f: json.dump(d, f, indent=2)
        os.replace(t, p)
    except Exception:
        try: os.unlink(t)
        except OSError: pass
        raise
def _log(event, email, **x):
    rec = _load(LOG, [])
    if not isinstance(rec, list): rec = []
    rec.append({"ts": _now(), "event": event, "email": email, **x})
    _save(LOG, rec)


def add(email, plan, name="", notes=""):
    email = _norm(email)
    if not email or "@" not in email: return {"error": "invalid email"}
    if plan not in PLANS: return {"error": f"unknown plan; choose from {list(PLANS)}"}
    subs = _load(SUBS, [])
    if not isinstance(subs, list): subs = []
    if any(_norm(s.get("email", "")) == email and s.get("plan") == plan for s in subs):
        return {"error": f"{email} already has {plan}"}
    subs.append({"email": email, "name": name or email.split("@")[0], "plan": plan,
                 "status": "pending", "added_at": _now(), "activated_at": "",
                 "fulfilled_at": "", "churned_at": "", "notes": notes})
    _save(SUBS, subs); _log("added", email, plan=plan)
    return {"status": "pending", "email": email, "plan": plan}


def activate(email):
    email = _norm(email); subs = _load(SUBS, [])
    if not isinstance(subs, list): return {"error": "co_subscribers.json wrong shape"}
    for s in subs:
        if _norm(s.get("email", "")) == email and s.get("status") == "pending":
            s["status"] = "active"; s["activated_at"] = _now()
            _save(SUBS, subs); _log("activated", email, plan=s.get("plan"))
            return {"status": "active", "email": email, "plan": s.get("plan")}
    return {"error": f"no pending for {email}"}


def fulfill(email):
    email = _norm(email); subs = _load(SUBS, [])
    if not isinstance(subs, list): return {"error": "co_subscribers.json wrong shape"}
    for s in subs:
        if _norm(s.get("email", "")) == email and s.get("status") == "active":
            plan = s.get("plan", "")
            if PLANS.get(plan, {}).get("price_mo", 0) > 0:
                return {"error": f"{plan} is recurring — use cancel"}
            s["status"] = "fulfilled"; s["fulfilled_at"] = _now()
            _save(SUBS, subs); _log("fulfilled", email, plan=plan)
            return {"status": "fulfilled", "email": email, "plan": plan}
    return {"error": f"no active for {email}"}


def cancel(email, reason=""):
    email = _norm(email); subs = _load(SUBS, [])
    if not isinstance(subs, list): return {"error": "co_subscribers.json wrong shape"}
    touched = []
    for s in subs:
        if _norm(s.get("email", "")) != email: continue
        if s.get("status") in ("active", "pending"):
            s["status"] = "churned"; s["churned_at"] = _now()
            if reason: s["notes"] = (s.get("notes", "") + f"\n[{_now()[:10]}] churn: {reason}").strip()
            touched.append(s.get("plan", ""))
    if not touched: return {"error": f"no active/pending for {email}"}
    _save(SUBS, subs); _log("cancelled", email, plans=touched, reason=reason)
    return {"status": "churned", "email": email, "plans": touched}


def listing():
    subs = _load(SUBS, [])
    if not isinstance(subs, list):
        return {"total": 0, "active": 0, "pending": 0, "fulfilled": 0, "churned": 0,
                "mrr": 0.0, "one_time_collected": 0, "by_plan": {}, "subscribers": []}
    bs = {"active": 0, "pending": 0, "fulfilled": 0, "churned": 0}
    bp = {}; mrr = 0.0; ot = 0
    for s in subs:
        st = s.get("status", "pending"); bs[st] = bs.get(st, 0) + 1
        plan = s.get("plan", ""); bp[plan] = bp.get(plan, 0) + 1
        p = PLANS.get(plan, {})
        if st == "active": mrr += p.get("price_mo", 0)
        if st in ("active", "fulfilled"): ot += p.get("one_time", 0)
    return {"total": len(subs), **bs, "mrr": round(mrr, 2),
            "one_time_collected": ot, "by_plan": bp, "subscribers": subs}


def _cli():
    import argparse
    p = argparse.ArgumentParser(); sub = p.add_subparsers(dest="cmd", required=True)
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
