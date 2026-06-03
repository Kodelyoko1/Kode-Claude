"""
Minimal subscription helper for HUDScout.

The deliver_digest() loop in hudscout.tools already reads hd_subscribers.json
and emails every record where status=active. But there was no way to put a
record in there — the file was consumed but never written. This module fills
that gap with three CLI subcommands the owner runs after a payment confirms:

  add        — create a pending subscriber for an email + plan
  activate   — flip a pending subscriber to active (after payment proof)
  cancel     — flip an active subscriber to churned

Three plans, all priced from the CLAUDE.md product description:
  monthly_97       — $97/mo daily digest
  quarterly_297    — $297/qtr daily digest + bid-window calendar
  white_label_497  — $497/qtr same as quarterly + per-market market pack

State file: data/hd_subscribers.json — list of:
  {
    "email":        "buyer@example.com",
    "plan":         "monthly_97",
    "status":       "active" | "pending" | "churned",
    "added_at":     ISO,
    "activated_at": ISO,
    "churned_at":   ISO,
    "notes":        "...",
  }
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
SUBS_FILE = DATA_DIR / "hd_subscribers.json"
LOG_FILE  = DATA_DIR / "hd_subscription_log.json"

PLANS = {
    "monthly_97":      {"price_mo": 97,  "label": "Monthly digest"},
    "quarterly_297":   {"price_mo": 99,  "label": "Quarterly retainer"},
    "white_label_497": {"price_mo": 166, "label": "White-label market pack"},
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


def _norm(email: str) -> str:
    return (email or "").strip().lower()


def add(email: str, plan: str, notes: str = "") -> dict:
    email = _norm(email)
    if not email or "@" not in email:
        return {"error": "invalid email"}
    if plan not in PLANS:
        return {"error": f"unknown plan; choose from {list(PLANS)}"}
    subs = _load(SUBS_FILE, [])
    if not isinstance(subs, list):
        subs = []
    if any(_norm(s.get("email", "")) == email for s in subs):
        return {"error": f"{email} already on file"}
    subs.append({
        "email": email,
        "plan": plan,
        "status": "pending",
        "added_at": _now(),
        "activated_at": "",
        "churned_at": "",
        "notes": notes,
    })
    _save(SUBS_FILE, subs)
    _log("added", email, plan=plan)
    return {"status": "added", "email": email, "plan": plan, "stage": "pending"}


def activate(email: str) -> dict:
    email = _norm(email)
    subs = _load(SUBS_FILE, [])
    if not isinstance(subs, list):
        return {"error": "hd_subscribers.json wrong shape"}
    for s in subs:
        if _norm(s.get("email", "")) == email:
            s["status"] = "active"
            s["activated_at"] = _now()
            _save(SUBS_FILE, subs)
            _log("activated", email)
            return {"status": "activated", "email": email, "plan": s.get("plan")}
    return {"error": f"{email} not found — add() first"}


def cancel(email: str, reason: str = "") -> dict:
    email = _norm(email)
    subs = _load(SUBS_FILE, [])
    if not isinstance(subs, list):
        return {"error": "hd_subscribers.json wrong shape"}
    for s in subs:
        if _norm(s.get("email", "")) == email:
            s["status"] = "churned"
            s["churned_at"] = _now()
            if reason:
                s["notes"] = (s.get("notes", "") + f"\n[{_now()[:10]}] churn: {reason}").strip()
            _save(SUBS_FILE, subs)
            _log("cancelled", email, reason=reason)
            return {"status": "churned", "email": email}
    return {"error": f"{email} not found"}


def listing() -> dict:
    subs = _load(SUBS_FILE, [])
    if not isinstance(subs, list):
        return {"total": 0, "active": 0, "pending": 0, "churned": 0,
                "mrr": 0.0, "subscribers": []}
    by_status = {"active": 0, "pending": 0, "churned": 0}
    mrr = 0.0
    for s in subs:
        by_status[s.get("status", "pending")] = by_status.get(s.get("status", "pending"), 0) + 1
        if s.get("status") == "active":
            mrr += PLANS.get(s.get("plan", ""), {}).get("price_mo", 0)
    return {
        "total": len(subs),
        **by_status,
        "mrr": mrr,
        "subscribers": subs,
    }


def _cli():
    import argparse
    p = argparse.ArgumentParser(description="HUDScout subscriber lifecycle")
    sub = p.add_subparsers(dest="cmd", required=True)
    p_add = sub.add_parser("add", help="Create a pending subscriber")
    p_add.add_argument("email")
    p_add.add_argument("plan", choices=list(PLANS))
    p_add.add_argument("--notes", default="")
    p_act = sub.add_parser("activate", help="Flip pending subscriber to active (after payment)")
    p_act.add_argument("email")
    p_can = sub.add_parser("cancel", help="Flip active subscriber to churned")
    p_can.add_argument("email")
    p_can.add_argument("--reason", default="")
    sub.add_parser("list", help="List all subscribers + MRR")
    args = p.parse_args()
    if args.cmd == "add":
        print(json.dumps(add(args.email, args.plan, args.notes), indent=2))
    elif args.cmd == "activate":
        print(json.dumps(activate(args.email), indent=2))
    elif args.cmd == "cancel":
        print(json.dumps(cancel(args.email, args.reason), indent=2))
    elif args.cmd == "list":
        out = listing()
        out_summary = {k: out[k] for k in ("total", "active", "pending", "churned", "mrr")}
        print(json.dumps(out_summary, indent=2))
        for s in out["subscribers"]:
            print(f"  {s.get('status','?'):>8s}  {s.get('plan',''):<16s}  {s.get('email','')}")


if __name__ == "__main__":
    _cli()
