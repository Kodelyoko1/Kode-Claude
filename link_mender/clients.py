"""
Minimal client lifecycle for LinkMender.

fulfill_cycle() reads lm_clients.json on every cycle and runs a full audit +
email for every active client. But the file was never written by code — when
a prospect paid for the one-time $97 audit OR the $47/mo monitoring, the
owner had no CLI to flip them into the consumer-side list. This module is
that CLI.

Three plans matching CLAUDE.md product description:
  audit_97       $97 one-time audit  (tracked but $0 MRR)
  monthly_47     $47/mo monitoring   (recurring)
  agency_197     $197 one-time agency lead list  (tracked but $0 MRR)

State file: data/lm_clients.json — list of:
  {
    "site_slug":      "<slug>",
    "url":            "https://...",
    "contact_name":   "First Last",
    "contact_email":  "x@y.com",
    "plan":           "audit_97" | "monthly_47" | "agency_197",
    "status":         "active" | "pending" | "churned",
    "added_at":       ISO,
    "activated_at":   ISO,
    "churned_at":     ISO,
    "notes":          "...",
  }
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

DATA_DIR     = Path(__file__).parent.parent / "data"
CLIENTS_FILE = DATA_DIR / "lm_clients.json"
LOG_FILE     = DATA_DIR / "lm_client_log.json"

PLANS = {
    "audit_97":    {"price_mo": 0,  "label": "One-time audit ($97)"},
    "monthly_47":  {"price_mo": 47, "label": "Monthly monitoring"},
    "agency_197":  {"price_mo": 0,  "label": "Agency lead list ($197)"},
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
    rec.append({"ts": _now(), "event": event, "site_slug": slug, **extra})
    _save(LOG_FILE, rec)


def add(site_slug: str, contact_email: str, plan: str, url: str = "",
        contact_name: str = "", notes: str = "") -> dict:
    """Promote a prospect (or seed a fresh client). site_slug + contact_email required."""
    site_slug = (site_slug or "").strip()
    contact_email = (contact_email or "").strip().lower()
    if not site_slug:
        return {"error": "site_slug required"}
    if not contact_email or "@" not in contact_email:
        return {"error": "invalid contact_email"}
    if plan not in PLANS:
        return {"error": f"unknown plan; choose from {list(PLANS)}"}
    clients = _load(CLIENTS_FILE, [])
    if not isinstance(clients, list):
        clients = []
    if any(c.get("site_slug") == site_slug for c in clients):
        return {"error": f"{site_slug} already a client"}
    clients.append({
        "site_slug": site_slug,
        "url": url,
        "contact_name": contact_name,
        "contact_email": contact_email,
        "plan": plan,
        "status": "pending",
        "added_at": _now(),
        "activated_at": "",
        "churned_at": "",
        "notes": notes,
    })
    _save(CLIENTS_FILE, clients)
    _log("added", site_slug, plan=plan, contact_email=contact_email)
    return {"status": "added", "site_slug": site_slug, "plan": plan, "stage": "pending"}


def activate(site_slug: str) -> dict:
    clients = _load(CLIENTS_FILE, [])
    if not isinstance(clients, list):
        return {"error": "lm_clients.json wrong shape"}
    for c in clients:
        if c.get("site_slug") == site_slug:
            c["status"] = "active"
            c["activated_at"] = _now()
            _save(CLIENTS_FILE, clients)
            _log("activated", site_slug)
            return {"status": "activated", "site_slug": site_slug, "plan": c.get("plan")}
    return {"error": f"{site_slug} not found — add() first"}


def cancel(site_slug: str, reason: str = "") -> dict:
    clients = _load(CLIENTS_FILE, [])
    if not isinstance(clients, list):
        return {"error": "lm_clients.json wrong shape"}
    for c in clients:
        if c.get("site_slug") == site_slug:
            c["status"] = "churned"
            c["churned_at"] = _now()
            if reason:
                c["notes"] = (c.get("notes", "") + f"\n[{_now()[:10]}] churn: {reason}").strip()
            _save(CLIENTS_FILE, clients)
            _log("cancelled", site_slug, reason=reason)
            return {"status": "churned", "site_slug": site_slug}
    return {"error": f"{site_slug} not found"}


def listing() -> dict:
    clients = _load(CLIENTS_FILE, [])
    if not isinstance(clients, list):
        return {"total": 0, "active": 0, "pending": 0, "churned": 0,
                "mrr": 0, "clients": []}
    by_status = {"active": 0, "pending": 0, "churned": 0}
    mrr = 0
    for c in clients:
        by_status[c.get("status", "pending")] = by_status.get(c.get("status", "pending"), 0) + 1
        if c.get("status") == "active":
            mrr += PLANS.get(c.get("plan", ""), {}).get("price_mo", 0)
    return {"total": len(clients), **by_status, "mrr": mrr, "clients": clients}


def _cli():
    import argparse
    p = argparse.ArgumentParser(description="LinkMender client lifecycle")
    sub = p.add_subparsers(dest="cmd", required=True)
    p_add = sub.add_parser("add", help="Create a pending client")
    p_add.add_argument("site_slug")
    p_add.add_argument("contact_email")
    p_add.add_argument("plan", choices=list(PLANS))
    p_add.add_argument("--url", default="")
    p_add.add_argument("--name", default="")
    p_add.add_argument("--notes", default="")
    p_act = sub.add_parser("activate", help="Flip pending → active")
    p_act.add_argument("site_slug")
    p_can = sub.add_parser("cancel", help="Flip active → churned")
    p_can.add_argument("site_slug")
    p_can.add_argument("--reason", default="")
    sub.add_parser("list", help="List + MRR")
    args = p.parse_args()
    if args.cmd == "add":
        print(json.dumps(add(args.site_slug, args.contact_email, args.plan,
                              url=args.url, contact_name=args.name, notes=args.notes), indent=2))
    elif args.cmd == "activate":
        print(json.dumps(activate(args.site_slug), indent=2))
    elif args.cmd == "cancel":
        print(json.dumps(cancel(args.site_slug, args.reason), indent=2))
    elif args.cmd == "list":
        out = listing()
        summary = {k: out[k] for k in ("total", "active", "pending", "churned", "mrr")}
        print(json.dumps(summary, indent=2))
        for c in out["clients"]:
            print(f"  {c.get('status','?'):>8s}  {c.get('plan',''):<14s}  "
                  f"{c.get('site_slug','')}  ({c.get('contact_email','')})")


if __name__ == "__main__":
    _cli()
