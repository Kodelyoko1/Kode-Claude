"""
Subscriber lifecycle for Transcribe.

The fulfill_cycle() loop in transcribe.tools reads tr_subscribers.json
and emails new transcripts to every active row. But the file was
consumed and never written — subscribers had to be hand-edited in.

Three plans matching CLAUDE.md product description:
  per_episode_19         $19 one-time per episode delivered
                         ($0 MRR, $19 one-time)
  monthly_10hr_79        $79/mo, 10 hours of audio (recurring;
                         diagnose checks the 36000s/mo cap)
  bulk_pack_297          $297 one-time, 30-episode bulk pack
                         ($0 MRR, $297 one-time, terminal)

State file: data/tr_subscribers.json — list of:
  {
    "email":         "creator@example.com",
    "name":          "Sam Creator",
    "plan":          "per_episode_19" | "monthly_10hr_79" | "bulk_pack_297",
    "status":        "active" | "pending" | "fulfilled" | "churned",
    "added_at":      ISO,
    "activated_at":  ISO,
    "fulfilled_at":  ISO,
    "churned_at":    ISO,
    "notes":         "...",
  }

Log: data/tr_subscription_log.json — append-only audit trail.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

DATA_DIR  = Path(__file__).parent.parent / "data"
SUBS_FILE = DATA_DIR / "tr_subscribers.json"
LOG_FILE  = DATA_DIR / "tr_subscription_log.json"

PLANS = {
    "per_episode_19":  {"price_mo": 0,  "one_time": 19,
                        "monthly_cap_seconds": 0,
                        "label": "Per-episode ($19 one-time)"},
    "monthly_10hr_79": {"price_mo": 79, "one_time": 0,
                        "monthly_cap_seconds": 36000,
                        "label": "Monthly 10-hour ($79/mo)"},
    "bulk_pack_297":   {"price_mo": 0,  "one_time": 297,
                        "monthly_cap_seconds": 0,
                        "label": "30-episode bulk pack ($297 one-time)"},
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


def add(email: str, plan: str, name: str = "", notes: str = "") -> dict:
    email = _norm_email(email)
    if not email or "@" not in email:
        return {"error": "invalid email"}
    if plan not in PLANS:
        return {"error": f"unknown plan; choose from {list(PLANS)}"}
    name = (name or "").strip() or email.split("@")[0]
    subs = _load(SUBS_FILE, [])
    if not isinstance(subs, list):
        subs = []
    if any(_norm_email(s.get("email", "")) == email and s.get("plan") == plan
           for s in subs):
        return {"error": f"{email} already has {plan}"}
    subs.append({
        "email":        email,
        "name":         name,
        "plan":         plan,
        "status":       "pending",
        "added_at":     _now(),
        "activated_at": "",
        "fulfilled_at": "",
        "churned_at":   "",
        "notes":        notes,
    })
    _save(SUBS_FILE, subs)
    _log("added", email, plan=plan)
    return {"status": "pending", "email": email, "plan": plan}


def activate(email: str) -> dict:
    email = _norm_email(email)
    subs = _load(SUBS_FILE, [])
    if not isinstance(subs, list):
        return {"error": "tr_subscribers.json wrong shape"}
    for s in subs:
        if _norm_email(s.get("email", "")) == email and s.get("status") == "pending":
            s["status"] = "active"
            s["activated_at"] = _now()
            _save(SUBS_FILE, subs)
            _log("activated", email, plan=s.get("plan"))
            return {"status": "active", "email": email, "plan": s.get("plan")}
    return {"error": f"no pending subscription for {email}"}


def fulfill(email: str) -> dict:
    email = _norm_email(email)
    subs = _load(SUBS_FILE, [])
    if not isinstance(subs, list):
        return {"error": "tr_subscribers.json wrong shape"}
    for s in subs:
        if _norm_email(s.get("email", "")) == email and s.get("status") == "active":
            plan = s.get("plan", "")
            if PLANS.get(plan, {}).get("price_mo", 0) > 0:
                return {"error": f"{plan} is recurring — use cancel instead of fulfill"}
            s["status"]       = "fulfilled"
            s["fulfilled_at"] = _now()
            _save(SUBS_FILE, subs)
            _log("fulfilled", email, plan=plan)
            return {"status": "fulfilled", "email": email, "plan": plan}
    return {"error": f"no active subscription for {email}"}


def cancel(email: str, reason: str = "") -> dict:
    email = _norm_email(email)
    subs = _load(SUBS_FILE, [])
    if not isinstance(subs, list):
        return {"error": "tr_subscribers.json wrong shape"}
    touched = []
    for s in subs:
        if _norm_email(s.get("email", "")) != email:
            continue
        if s.get("status") in ("active", "pending"):
            s["status"]     = "churned"
            s["churned_at"] = _now()
            if reason:
                s["notes"] = (s.get("notes", "") + f"\n[{_now()[:10]}] churn: {reason}").strip()
            touched.append(s.get("plan", ""))
    if not touched:
        return {"error": f"no active/pending subscription for {email}"}
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
    p = argparse.ArgumentParser(description="Transcribe subscriber lifecycle")
    sub = p.add_subparsers(dest="cmd", required=True)
    p_add = sub.add_parser("add", help="Create a pending subscriber")
    p_add.add_argument("email")
    p_add.add_argument("plan", choices=list(PLANS))
    p_add.add_argument("--name", default="")
    p_add.add_argument("--notes", default="")
    p_act = sub.add_parser("activate", help="Flip pending to active (after payment)")
    p_act.add_argument("email")
    p_ful = sub.add_parser("fulfill", help="Mark one-time plan as fulfilled")
    p_ful.add_argument("email")
    p_can = sub.add_parser("cancel", help="Churn subscriber")
    p_can.add_argument("email")
    p_can.add_argument("--reason", default="")
    sub.add_parser("list", help="List subscribers + MRR + by_plan")
    args = p.parse_args()
    if args.cmd == "add":
        print(json.dumps(add(args.email, args.plan, args.name, args.notes), indent=2))
    elif args.cmd == "activate":
        print(json.dumps(activate(args.email), indent=2))
    elif args.cmd == "fulfill":
        print(json.dumps(fulfill(args.email), indent=2))
    elif args.cmd == "cancel":
        print(json.dumps(cancel(args.email, args.reason), indent=2))
    elif args.cmd == "list":
        out = listing()
        summ = {k: out[k] for k in (
            "total", "active", "pending", "fulfilled", "churned",
            "mrr", "one_time_collected", "by_plan")}
        print(json.dumps(summ, indent=2))
        for s in out["subscribers"]:
            print(f"  {s.get('status','?'):>9s}  {s.get('plan',''):<18s}  "
                  f"{s.get('email','')}")


if __name__ == "__main__":
    _cli()
