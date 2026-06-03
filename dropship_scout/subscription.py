"""
Minimal subscription helper for DropshipScout.

deliver_subscribers() iterates ds_subscribers.json on every Monday cycle but
the file had no writer — sub-list was consume-only. This module closes that
gap with three owner CLI subcommands:

  add        — create a pending subscriber for an email
  activate   — flip a pending subscriber to active (after PayPal proof)
  cancel     — flip an active subscriber to churned

Single plan today: $47/mo per the public landing page (website/dropship_scout_trends.html).

State file: data/ds_subscribers.json — list of:
  {
    "email":        "buyer@example.com",
    "name":         "Buyer Name",
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

DATA_DIR  = Path(__file__).parent.parent / "data"
SUBS_FILE = DATA_DIR / "ds_subscribers.json"
LOG_FILE  = DATA_DIR / "ds_subscription_log.json"

PLAN_PRICE = 47


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


def add(email: str, name: str = "", notes: str = "") -> dict:
    email = _norm(email)
    if not email or "@" not in email:
        return {"error": "invalid email"}
    subs = _load(SUBS_FILE, [])
    if not isinstance(subs, list):
        subs = []
    if any(_norm(s.get("email", "")) == email for s in subs):
        return {"error": f"{email} already on file"}
    subs.append({
        "email": email,
        "name": name,
        "status": "pending",
        "added_at": _now(),
        "activated_at": "",
        "churned_at": "",
        "notes": notes,
    })
    _save(SUBS_FILE, subs)
    _log("added", email)
    return {"status": "added", "email": email, "stage": "pending",
            "price_mo": PLAN_PRICE}


def activate(email: str) -> dict:
    email = _norm(email)
    subs = _load(SUBS_FILE, [])
    if not isinstance(subs, list):
        return {"error": "ds_subscribers.json wrong shape"}
    for s in subs:
        if _norm(s.get("email", "")) == email:
            s["status"] = "active"
            s["activated_at"] = _now()
            _save(SUBS_FILE, subs)
            _log("activated", email)
            return {"status": "activated", "email": email}
    return {"error": f"{email} not found — add() first"}


def cancel(email: str, reason: str = "") -> dict:
    email = _norm(email)
    subs = _load(SUBS_FILE, [])
    if not isinstance(subs, list):
        return {"error": "ds_subscribers.json wrong shape"}
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
                "mrr": 0, "subscribers": []}
    by_status = {"active": 0, "pending": 0, "churned": 0}
    for s in subs:
        by_status[s.get("status", "pending")] = by_status.get(s.get("status", "pending"), 0) + 1
    mrr = by_status["active"] * PLAN_PRICE
    return {"total": len(subs), **by_status, "mrr": mrr, "subscribers": subs}


def _cli():
    import argparse
    p = argparse.ArgumentParser(description="DropshipScout subscriber lifecycle")
    sub = p.add_subparsers(dest="cmd", required=True)
    p_add = sub.add_parser("add", help="Create a pending subscriber")
    p_add.add_argument("email")
    p_add.add_argument("--name", default="")
    p_add.add_argument("--notes", default="")
    p_act = sub.add_parser("activate", help="Flip pending → active after payment")
    p_act.add_argument("email")
    p_can = sub.add_parser("cancel", help="Flip active → churned")
    p_can.add_argument("email")
    p_can.add_argument("--reason", default="")
    sub.add_parser("list", help="List all subscribers + MRR")
    args = p.parse_args()
    if args.cmd == "add":
        print(json.dumps(add(args.email, args.name, args.notes), indent=2))
    elif args.cmd == "activate":
        print(json.dumps(activate(args.email), indent=2))
    elif args.cmd == "cancel":
        print(json.dumps(cancel(args.email, args.reason), indent=2))
    elif args.cmd == "list":
        out = listing()
        out_summary = {k: out[k] for k in ("total", "active", "pending", "churned", "mrr")}
        print(json.dumps(out_summary, indent=2))
        for s in out["subscribers"]:
            print(f"  {s.get('status','?'):>8s}  {s.get('email','')}  ({s.get('name','')})")


if __name__ == "__main__":
    _cli()
