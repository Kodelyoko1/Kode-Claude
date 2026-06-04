"""
Paying-client lifecycle for CareerForge.

The fulfill_orders() loop in careerforge.tools reads cf_orders.json
(per-tailoring queue), and acquire_cycle reads cf_leads.json. But there
was no surface for managing the people who paid for the subscription
tier or the one-time packages. This module adds that:

Three plans matching CLAUDE.md product description:
  tailoring_29     — $29 one-time per tailoring (1 deliverable per order)
  monthly_49       — $49/mo unlimited (~20/mo per CLAUDE.md), recurring
  career_pkg_147   — $147 one-time career package (5 tailorings included)

Of these, only monthly_49 contributes to MRR. tailoring_29 and
career_pkg_147 contribute to one_time_collected (lifetime gross from
active + fulfilled). The actual per-tailoring orders go through
cf_orders.json — this module just tracks WHO is paying and via WHICH
plan so MRR/one-time and the monthly-cap diagnose check have a source
of truth.

State file: data/cf_clients.json — list of:
  {
    "email":         "jane@example.com",
    "name":          "Jane Job-Seeker",
    "user_id":       "jane-job-seeker",
    "plan":          "tailoring_29" | "monthly_49" | "career_pkg_147",
    "status":        "active" | "pending" | "fulfilled" | "churned",
    "added_at":      ISO,
    "activated_at":  ISO,
    "fulfilled_at":  ISO,
    "churned_at":    ISO,
    "tailorings_promised": int,    # 1 / -1 unlimited / 5
    "notes":         "...",
  }

Log: data/cf_client_log.json — append-only audit trail.
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path

DATA_DIR    = Path(__file__).parent.parent / "data"
CLIENT_FILE = DATA_DIR / "cf_clients.json"
LOG_FILE    = DATA_DIR / "cf_client_log.json"

PLANS = {
    "tailoring_29":   {"price_mo": 0,  "one_time": 29,  "tailorings": 1,
                       "label": "Single tailoring ($29 one-time)"},
    "monthly_49":     {"price_mo": 49, "one_time": 0,   "tailorings": -1,
                       "label": "Unlimited monthly ($49/mo, ~20/mo cap)"},
    "career_pkg_147": {"price_mo": 0,  "one_time": 147, "tailorings": 5,
                       "label": "Career package ($147 one-time, 5 tailorings)"},
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


def _find(clients: list, ident: str) -> dict | None:
    """Match by email OR user_id, case-insensitive."""
    ident_lc = (ident or "").strip().lower()
    if not ident_lc:
        return None
    for c in clients:
        if _norm_email(c.get("email", "")) == ident_lc:
            return c
    for c in clients:
        if (c.get("user_id") or "").lower() == ident_lc:
            return c
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
    clients = _load(CLIENT_FILE, [])
    if not isinstance(clients, list):
        clients = []
    if any(_norm_email(c.get("email", "")) == email and c.get("plan") == plan
           for c in clients):
        return {"error": f"{email} already has {plan}"}
    clients.append({
        "email":               email,
        "name":                name,
        "user_id":             user_id,
        "plan":                plan,
        "status":              "pending",
        "added_at":            _now(),
        "activated_at":        "",
        "fulfilled_at":        "",
        "churned_at":          "",
        "tailorings_promised": PLANS[plan]["tailorings"],
        "notes":               notes,
    })
    _save(CLIENT_FILE, clients)
    _log("added", email, plan=plan, user_id=user_id)
    return {"status": "pending", "email": email, "user_id": user_id, "plan": plan}


def activate(ident: str) -> dict:
    clients = _load(CLIENT_FILE, [])
    if not isinstance(clients, list):
        return {"error": "cf_clients.json wrong shape"}
    c = _find(clients, ident)
    if not c:
        return {"error": f"no client matches {ident!r}"}
    if c.get("status") in ("active", "fulfilled"):
        return {"error": f"{c.get('email')} already {c.get('status')}"}
    c["status"]       = "active"
    c["activated_at"] = _now()
    _save(CLIENT_FILE, clients)
    _log("activated", c.get("email", ""), plan=c.get("plan"))
    return {"status": "active", "email": c.get("email"),
            "user_id": c.get("user_id"), "plan": c.get("plan")}


def fulfill(ident: str) -> dict:
    """Terminal status for tailoring_29 and career_pkg_147 (one-time plans)."""
    clients = _load(CLIENT_FILE, [])
    if not isinstance(clients, list):
        return {"error": "cf_clients.json wrong shape"}
    c = _find(clients, ident)
    if not c:
        return {"error": f"no client matches {ident!r}"}
    plan = c.get("plan", "")
    if PLANS.get(plan, {}).get("price_mo", 0) > 0:
        return {"error": f"{plan} is recurring — use cancel instead of fulfill"}
    c["status"]       = "fulfilled"
    c["fulfilled_at"] = _now()
    _save(CLIENT_FILE, clients)
    _log("fulfilled", c.get("email", ""), plan=plan)
    return {"status": "fulfilled", "email": c.get("email"), "plan": plan}


def cancel(ident: str, reason: str = "") -> dict:
    clients = _load(CLIENT_FILE, [])
    if not isinstance(clients, list):
        return {"error": "cf_clients.json wrong shape"}
    c = _find(clients, ident)
    if not c:
        return {"error": f"no client matches {ident!r}"}
    c["status"]     = "churned"
    c["churned_at"] = _now()
    if reason:
        c["notes"] = (c.get("notes", "") + f"\n[{_now()[:10]}] churn: {reason}").strip()
    _save(CLIENT_FILE, clients)
    _log("cancelled", c.get("email", ""), reason=reason)
    return {"status": "churned", "email": c.get("email")}


def listing() -> dict:
    clients = _load(CLIENT_FILE, [])
    if not isinstance(clients, list):
        return {"total": 0, "active": 0, "pending": 0, "fulfilled": 0, "churned": 0,
                "mrr": 0.0, "one_time_collected": 0, "by_plan": {}, "clients": []}
    by_status = {"active": 0, "pending": 0, "fulfilled": 0, "churned": 0}
    by_plan: dict[str, int] = {}
    mrr = 0.0
    one_time = 0
    for c in clients:
        st = c.get("status", "pending")
        by_status[st] = by_status.get(st, 0) + 1
        plan = c.get("plan", "")
        by_plan[plan] = by_plan.get(plan, 0) + 1
        p = PLANS.get(plan, {})
        if st == "active":
            mrr += p.get("price_mo", 0)
        if st in ("active", "fulfilled"):
            one_time += p.get("one_time", 0)
    return {
        "total":              len(clients),
        **by_status,
        "mrr":                round(mrr, 2),
        "one_time_collected": one_time,
        "by_plan":            by_plan,
        "clients":            clients,
    }


def _cli():
    import argparse
    p = argparse.ArgumentParser(description="CareerForge client lifecycle")
    sub = p.add_subparsers(dest="cmd", required=True)
    p_add = sub.add_parser("add", help="Create a pending client")
    p_add.add_argument("email")
    p_add.add_argument("plan", choices=list(PLANS))
    p_add.add_argument("--name", default="")
    p_add.add_argument("--user-id", default="")
    p_add.add_argument("--notes", default="")
    p_act = sub.add_parser("activate", help="Flip pending to active (after payment)")
    p_act.add_argument("ident", help="email OR user_id")
    p_ful = sub.add_parser("fulfill", help="Mark one-time plan as fulfilled")
    p_ful.add_argument("ident")
    p_can = sub.add_parser("cancel", help="Churn client")
    p_can.add_argument("ident")
    p_can.add_argument("--reason", default="")
    sub.add_parser("list", help="List clients + MRR + one-time + by-plan")
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
        for c in out["clients"]:
            print(f"  {c.get('status','?'):>9s}  {c.get('plan',''):<16s}  "
                  f"{c.get('user_id',''):<20s}  {c.get('email','')}")


if __name__ == "__main__":
    _cli()
