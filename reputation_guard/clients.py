"""
Paying-client lifecycle for ReputationGuard.

The fulfill_cycle() loop in reputation_guard.tools already reads
rg_clients.json and drafts replies for every record where
status=active. But the file was consumed and never written — clients
had to be hand-edited in.

Two plans matching CLAUDE.md product description:
  monthly_79      — $79/mo per location, weekly reply drafts
  deep_audit_497  — $497 one-time deep audit (tracked, $0 MRR)

The deep audit is a one-time deliverable; once shipped, the owner
should `fulfill` to stop fulfill_cycle from re-running on it. The
monthly plan keeps running through the cron until cancelled.

State file: data/rg_clients.json — list of:
  {
    "business_slug":  "joes-pizza-portland",
    "business_name":  "Joe's Pizza Portland",
    "owner_name":     "Joe",
    "contact_email":  "joe@joespizzapdx.com",
    "contact_phone":  "207-555-0100",
    "plan":           "monthly_79" | "deep_audit_497",
    "status":         "active" | "pending" | "fulfilled" | "churned",
    "added_at":       ISO,
    "activated_at":   ISO,
    "fulfilled_at":   ISO,
    "churned_at":     ISO,
    "notes":          "...",
  }

Log: data/rg_client_log.json — append-only audit trail.
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
CLIENT_FILE = DATA_DIR / "rg_clients.json"
LOG_FILE    = DATA_DIR / "rg_client_log.json"

PLANS = {
    "monthly_79":     {"price_mo": 79, "one_time": 0,
                       "label": "Monthly reply drafts ($79/mo)"},
    "deep_audit_497": {"price_mo": 0,  "one_time": 497,
                       "label": "Deep audit ($497 one-time)"},
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


def _log(event: str, slug: str, **extra) -> None:
    rec = _load(LOG_FILE, [])
    if not isinstance(rec, list):
        rec = []
    rec.append({"ts": _now(), "event": event, "business_slug": slug, **extra})
    _save(LOG_FILE, rec)


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")


def _find(clients: list, ident: str) -> dict | None:
    """Match by business_slug OR contact_email, case-insensitive."""
    ident_lc = (ident or "").strip().lower()
    if not ident_lc:
        return None
    for c in clients:
        if (c.get("business_slug") or "").lower() == ident_lc:
            return c
    for c in clients:
        if (c.get("contact_email") or "").lower() == ident_lc:
            return c
    return None


def add(business_name: str, contact_email: str, plan: str,
        owner_name: str = "", contact_phone: str = "", notes: str = "") -> dict:
    business_name = (business_name or "").strip()
    contact_email = (contact_email or "").strip().lower()
    if not business_name:
        return {"error": "business_name is required"}
    if not contact_email or "@" not in contact_email:
        return {"error": "invalid contact_email"}
    if plan not in PLANS:
        return {"error": f"unknown plan; choose from {list(PLANS)}"}
    slug = _slug(business_name)
    if not slug:
        return {"error": "could not derive slug from business_name"}
    clients = _load(CLIENT_FILE, [])
    if not isinstance(clients, list):
        clients = []
    if any(c.get("business_slug") == slug for c in clients):
        return {"error": f"{slug} already on file"}
    clients.append({
        "business_slug": slug,
        "business_name": business_name,
        "owner_name":    owner_name or "",
        "contact_email": contact_email,
        "contact_phone": contact_phone or "",
        "plan":          plan,
        "status":        "pending",
        "added_at":      _now(),
        "activated_at":  "",
        "fulfilled_at":  "",
        "churned_at":    "",
        "notes":         notes,
    })
    _save(CLIENT_FILE, clients)
    _log("added", slug, plan=plan, contact_email=contact_email)
    return {"status": "pending", "business_slug": slug,
            "business_name": business_name, "plan": plan}


def activate(ident: str) -> dict:
    clients = _load(CLIENT_FILE, [])
    if not isinstance(clients, list):
        return {"error": "rg_clients.json wrong shape"}
    c = _find(clients, ident)
    if not c:
        return {"error": f"no client matches {ident!r}"}
    if c.get("status") in ("active", "fulfilled"):
        return {"error": f"{c.get('business_slug')} already {c.get('status')}"}
    c["status"]       = "active"
    c["activated_at"] = _now()
    _save(CLIENT_FILE, clients)
    _log("activated", c.get("business_slug", ""), plan=c.get("plan"))
    return {"status": "active", "business_slug": c.get("business_slug"),
            "plan": c.get("plan")}


def fulfill(ident: str) -> dict:
    """Terminal status for the one-time deep_audit_497 plan."""
    clients = _load(CLIENT_FILE, [])
    if not isinstance(clients, list):
        return {"error": "rg_clients.json wrong shape"}
    c = _find(clients, ident)
    if not c:
        return {"error": f"no client matches {ident!r}"}
    plan = c.get("plan", "")
    if PLANS.get(plan, {}).get("price_mo", 0) > 0:
        return {"error": f"{plan} is recurring — use cancel instead of fulfill"}
    c["status"]       = "fulfilled"
    c["fulfilled_at"] = _now()
    _save(CLIENT_FILE, clients)
    _log("fulfilled", c.get("business_slug", ""), plan=plan)
    return {"status": "fulfilled", "business_slug": c.get("business_slug"), "plan": plan}


def cancel(ident: str, reason: str = "") -> dict:
    clients = _load(CLIENT_FILE, [])
    if not isinstance(clients, list):
        return {"error": "rg_clients.json wrong shape"}
    c = _find(clients, ident)
    if not c:
        return {"error": f"no client matches {ident!r}"}
    c["status"]     = "churned"
    c["churned_at"] = _now()
    if reason:
        c["notes"] = (c.get("notes", "") + f"\n[{_now()[:10]}] churn: {reason}").strip()
    _save(CLIENT_FILE, clients)
    _log("cancelled", c.get("business_slug", ""), reason=reason)
    return {"status": "churned", "business_slug": c.get("business_slug")}


def listing() -> dict:
    clients = _load(CLIENT_FILE, [])
    if not isinstance(clients, list):
        return {"total": 0, "active": 0, "pending": 0, "fulfilled": 0, "churned": 0,
                "mrr": 0.0, "one_time_collected": 0, "clients": []}
    by_status = {"active": 0, "pending": 0, "fulfilled": 0, "churned": 0}
    mrr = 0.0
    one_time = 0
    for c in clients:
        st = c.get("status", "pending")
        by_status[st] = by_status.get(st, 0) + 1
        plan = PLANS.get(c.get("plan", ""), {})
        if st == "active":
            mrr += plan.get("price_mo", 0)
        if st in ("active", "fulfilled"):
            one_time += plan.get("one_time", 0)
    return {
        "total":              len(clients),
        **by_status,
        "mrr":                round(mrr, 2),
        "one_time_collected": one_time,
        "clients":            clients,
    }


def _cli():
    import argparse
    p = argparse.ArgumentParser(description="ReputationGuard client lifecycle")
    sub = p.add_subparsers(dest="cmd", required=True)
    p_add = sub.add_parser("add", help="Create a pending client")
    p_add.add_argument("business_name")
    p_add.add_argument("contact_email")
    p_add.add_argument("plan", choices=list(PLANS))
    p_add.add_argument("--owner-name", default="")
    p_add.add_argument("--contact-phone", default="")
    p_add.add_argument("--notes", default="")
    p_act = sub.add_parser("activate", help="Flip pending to active (after payment)")
    p_act.add_argument("ident", help="business_slug OR contact_email")
    p_ful = sub.add_parser("fulfill",
                            help="Mark deep_audit_497 as fulfilled (terminal)")
    p_ful.add_argument("ident")
    p_can = sub.add_parser("cancel", help="Churn client")
    p_can.add_argument("ident")
    p_can.add_argument("--reason", default="")
    sub.add_parser("list", help="List clients + MRR + one-time collected")
    args = p.parse_args()
    if args.cmd == "add":
        print(json.dumps(add(args.business_name, args.contact_email, args.plan,
                             args.owner_name, args.contact_phone, args.notes), indent=2))
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
            "mrr", "one_time_collected")}
        print(json.dumps(summ, indent=2))
        for c in out["clients"]:
            print(f"  {c.get('status','?'):>9s}  {c.get('plan',''):<16s}  "
                  f"{c.get('business_slug',''):<28s}  {c.get('contact_email','')}")


if __name__ == "__main__":
    _cli()
