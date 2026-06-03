"""
Minimal subscriber lifecycle for SpeedAudit.

fulfill_cycle() reads sa_subscribers.json on every cycle but the file had no
writer. Owner had to hand-edit the JSON to onboard a paying customer. This
module is that writer.

Three plans matching CLAUDE.md product description:
  audit_77       $77 one-time deep audit (tracked, $0 MRR)
  monthly_37     $37/mo monitoring — monthly re-audit + alerts
  retainer_297   $297/qtr — implementation review (~$99/mo equivalent)

State file: data/sa_subscribers.json — list of:
  {
    "email":         "client@example.com",
    "name":          "Client Name",
    "site":          "https://example.com",
    "plan":          "audit_77" | "monthly_37" | "retainer_297",
    "status":        "active" | "pending" | "churned",
    "added_at":      ISO,
    "activated_at":  ISO,
    "churned_at":    ISO,
    "notes":         "...",
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
SUBS_FILE = DATA_DIR / "sa_subscribers.json"
LOG_FILE  = DATA_DIR / "sa_subscriber_log.json"

PLANS = {
    "audit_77":      {"price_mo": 0,  "label": "One-time audit ($77)"},
    "monthly_37":    {"price_mo": 37, "label": "Monthly monitoring"},
    "retainer_297":  {"price_mo": 99, "label": "Quarterly retainer ($297/qtr)"},
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


def add(email: str, site: str, plan: str, name: str = "", notes: str = "") -> dict:
    email = _norm(email)
    if not email or "@" not in email:
        return {"error": "invalid email"}
    if not site:
        return {"error": "site required"}
    if plan not in PLANS:
        return {"error": f"unknown plan; choose from {list(PLANS)}"}
    subs = _load(SUBS_FILE, [])
    if not isinstance(subs, list):
        subs = []
    if any(_norm(s.get("email", "")) == email and s.get("site") == site for s in subs):
        return {"error": f"{email} already subscribed for {site}"}
    subs.append({
        "email": email,
        "name": name,
        "site": site,
        "plan": plan,
        "status": "pending",
        "added_at": _now(),
        "activated_at": "",
        "churned_at": "",
        "notes": notes,
    })
    _save(SUBS_FILE, subs)
    _log("added", email, site=site, plan=plan)
    return {"status": "added", "email": email, "site": site,
            "plan": plan, "stage": "pending"}


def activate(email: str, site: str = "") -> dict:
    email = _norm(email)
    subs = _load(SUBS_FILE, [])
    if not isinstance(subs, list):
        return {"error": "sa_subscribers.json wrong shape"}
    for s in subs:
        if _norm(s.get("email", "")) == email and (not site or s.get("site") == site):
            s["status"] = "active"
            s["activated_at"] = _now()
            _save(SUBS_FILE, subs)
            _log("activated", email, site=s.get("site"))
            return {"status": "activated", "email": email,
                    "site": s.get("site"), "plan": s.get("plan")}
    return {"error": f"{email} not found — add() first"}


def cancel(email: str, site: str = "", reason: str = "") -> dict:
    email = _norm(email)
    subs = _load(SUBS_FILE, [])
    if not isinstance(subs, list):
        return {"error": "sa_subscribers.json wrong shape"}
    for s in subs:
        if _norm(s.get("email", "")) == email and (not site or s.get("site") == site):
            s["status"] = "churned"
            s["churned_at"] = _now()
            if reason:
                s["notes"] = (s.get("notes", "") + f"\n[{_now()[:10]}] churn: {reason}").strip()
            _save(SUBS_FILE, subs)
            _log("cancelled", email, site=s.get("site"), reason=reason)
            return {"status": "churned", "email": email, "site": s.get("site")}
    return {"error": f"{email} not found"}


def listing() -> dict:
    subs = _load(SUBS_FILE, [])
    if not isinstance(subs, list):
        return {"total": 0, "active": 0, "pending": 0, "churned": 0,
                "mrr": 0, "subscribers": []}
    by_status = {"active": 0, "pending": 0, "churned": 0}
    mrr = 0
    for s in subs:
        by_status[s.get("status", "pending")] = by_status.get(s.get("status", "pending"), 0) + 1
        if s.get("status") == "active":
            mrr += PLANS.get(s.get("plan", ""), {}).get("price_mo", 0)
    return {"total": len(subs), **by_status, "mrr": mrr, "subscribers": subs}


def _cli():
    import argparse
    p = argparse.ArgumentParser(description="SpeedAudit subscriber lifecycle")
    sub = p.add_subparsers(dest="cmd", required=True)
    p_add = sub.add_parser("add", help="Create a pending subscriber")
    p_add.add_argument("email")
    p_add.add_argument("site")
    p_add.add_argument("plan", choices=list(PLANS))
    p_add.add_argument("--name", default="")
    p_add.add_argument("--notes", default="")
    p_act = sub.add_parser("activate", help="Flip pending → active")
    p_act.add_argument("email")
    p_act.add_argument("--site", default="")
    p_can = sub.add_parser("cancel", help="Flip active → churned")
    p_can.add_argument("email")
    p_can.add_argument("--site", default="")
    p_can.add_argument("--reason", default="")
    sub.add_parser("list", help="List + MRR")
    args = p.parse_args()
    if args.cmd == "add":
        print(json.dumps(add(args.email, args.site, args.plan, args.name, args.notes), indent=2))
    elif args.cmd == "activate":
        print(json.dumps(activate(args.email, args.site), indent=2))
    elif args.cmd == "cancel":
        print(json.dumps(cancel(args.email, args.site, args.reason), indent=2))
    elif args.cmd == "list":
        out = listing()
        summary = {k: out[k] for k in ("total", "active", "pending", "churned", "mrr")}
        print(json.dumps(summary, indent=2))
        for s in out["subscribers"]:
            print(f"  {s.get('status','?'):>8s}  {s.get('plan',''):<14s}  "
                  f"{s.get('email',''):<30s}  {s.get('site','')}")


if __name__ == "__main__":
    _cli()
