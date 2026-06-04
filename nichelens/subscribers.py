"""
Per-niche tiered subscription lifecycle for NicheLens.

The fulfill_cycle() loop in nichelens.tools already reads
nl_subscribers.json and emails every active subscriber the paid or free
version of their niche newsletter. But the file was consumed and never
written — subscribers had to be hand-edited in.

NicheLens has BOTH free + paid tiers (free gets 5 items + an upsell
footer; paid gets 7 items + no ads). A single email can subscribe to
multiple niches. So this CLI carries (email, niche) as the join key,
with tier on the row.

Three plans, priced from the CLAUDE.md product description:
  free               — $0/mo free 5-item newsletter (no plan activation step)
  paid_monthly_7     — $7/mo per niche, 7 items, no upsell footer
  annual_59          — $59/yr per niche (≈ $4.92/mo equivalent)

State file: data/nl_subscribers.json — list of:
  {
    "email":        "reader@example.com",
    "niche":        "indie-board-games",
    "plan":         "free" | "paid_monthly_7" | "annual_59",
    "tier":         "free" | "paid",         # what fulfill_cycle reads
    "status":       "active" | "pending" | "churned",
    "added_at":     ISO,
    "activated_at": ISO,
    "churned_at":   ISO,
    "notes":        "...",
  }

Log: data/nl_subscription_log.json — append-only audit trail.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from collections import Counter
from datetime import datetime
from pathlib import Path

DATA_DIR  = Path(__file__).parent.parent / "data"
SUBS_FILE = DATA_DIR / "nl_subscribers.json"
LOG_FILE  = DATA_DIR / "nl_subscription_log.json"

PLANS = {
    "free":           {"price_mo": 0,    "tier": "free", "label": "Free 5-item newsletter"},
    "paid_monthly_7": {"price_mo": 7,    "tier": "paid", "label": "$7/mo paid (7 items, no ads)"},
    "annual_59":      {"price_mo": 4.92, "tier": "paid", "label": "Annual prepaid ($59/yr)"},
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


def _norm_niche(niche: str) -> str:
    return (niche or "").strip().lower().replace(" ", "-")


def add(email: str, niche: str, plan: str = "free", notes: str = "") -> dict:
    email = _norm_email(email)
    niche = _norm_niche(niche)
    if not email or "@" not in email:
        return {"error": "invalid email"}
    if not niche:
        return {"error": "niche is required"}
    if plan not in PLANS:
        return {"error": f"unknown plan; choose from {list(PLANS)}"}
    subs = _load(SUBS_FILE, [])
    if not isinstance(subs, list):
        subs = []
    for s in subs:
        if _norm_email(s.get("email", "")) == email and _norm_niche(s.get("niche", "")) == niche:
            if s.get("status") == "churned":
                s["status"]     = "active" if plan == "free" else "pending"
                s["plan"]       = plan
                s["tier"]       = PLANS[plan]["tier"]
                s["added_at"]   = _now()
                s["churned_at"] = ""
                _save(SUBS_FILE, subs)
                _log("re-subscribed", email, niche=niche, plan=plan)
                return {"status": s["status"], "email": email, "niche": niche, "plan": plan}
            return {"error": f"{email} already subscribed to {niche}"}
    status = "active" if plan == "free" else "pending"
    subs.append({
        "email":        email,
        "niche":        niche,
        "plan":         plan,
        "tier":         PLANS[plan]["tier"],
        "status":       status,
        "added_at":     _now(),
        "activated_at": _now() if status == "active" else "",
        "churned_at":   "",
        "notes":        notes,
    })
    _save(SUBS_FILE, subs)
    _log("added", email, niche=niche, plan=plan, stage=status)
    return {"status": status, "email": email, "niche": niche, "plan": plan}


def activate(email: str, niche: str = "") -> dict:
    """Flip a pending paid subscriber to active. Niche optional; if multiple
    pending rows for the email, niche is required."""
    email = _norm_email(email)
    niche = _norm_niche(niche)
    subs = _load(SUBS_FILE, [])
    if not isinstance(subs, list):
        return {"error": "nl_subscribers.json wrong shape"}
    matches = [s for s in subs
               if _norm_email(s.get("email", "")) == email
               and s.get("status") == "pending"
               and (not niche or _norm_niche(s.get("niche", "")) == niche)]
    if not matches:
        return {"error": f"no pending subscription for {email}"
                          + (f" / {niche}" if niche else "")}
    if len(matches) > 1 and not niche:
        return {"error": f"{email} has multiple pending — pass --niche to disambiguate "
                         f"(niches: {[s.get('niche') for s in matches]})"}
    s = matches[0]
    s["status"]       = "active"
    s["activated_at"] = _now()
    _save(SUBS_FILE, subs)
    _log("activated", email, niche=s.get("niche", ""))
    return {"status": "active", "email": email, "niche": s.get("niche"), "plan": s.get("plan")}


def cancel(email: str, niche: str = "", reason: str = "") -> dict:
    email = _norm_email(email)
    niche = _norm_niche(niche)
    subs = _load(SUBS_FILE, [])
    if not isinstance(subs, list):
        return {"error": "nl_subscribers.json wrong shape"}
    touched = []
    for s in subs:
        if _norm_email(s.get("email", "")) != email:
            continue
        if niche and _norm_niche(s.get("niche", "")) != niche:
            continue
        if s.get("status") in ("active", "pending"):
            s["status"]     = "churned"
            s["churned_at"] = _now()
            if reason:
                s["notes"] = (s.get("notes", "") + f"\n[{_now()[:10]}] churn: {reason}").strip()
            touched.append(s.get("niche", ""))
    if not touched:
        return {"error": f"no active/pending subscription for {email}"
                          + (f" / {niche}" if niche else "")}
    _save(SUBS_FILE, subs)
    _log("cancelled", email, niches=touched, reason=reason)
    return {"status": "churned", "email": email, "niches": touched}


def listing(niche: str = "") -> dict:
    niche = _norm_niche(niche)
    subs = _load(SUBS_FILE, [])
    if not isinstance(subs, list):
        return {"total": 0, "active": 0, "pending": 0, "churned": 0,
                "active_paid": 0, "active_free": 0,
                "by_niche": {}, "mrr": 0.0, "subscribers": []}
    rows = [s for s in subs if (not niche or _norm_niche(s.get("niche", "")) == niche)]
    active = [s for s in rows if s.get("status") == "active"]
    active_paid = sum(1 for s in active if s.get("tier") == "paid")
    active_free = sum(1 for s in active if s.get("tier") != "paid")
    mrr = sum(PLANS.get(s.get("plan", ""), {}).get("price_mo", 0) for s in active)
    by_niche = Counter(_norm_niche(s.get("niche", "")) for s in active)
    return {
        "total":       len(rows),
        "active":      len(active),
        "pending":     sum(1 for s in rows if s.get("status") == "pending"),
        "churned":     sum(1 for s in rows if s.get("status") == "churned"),
        "active_paid": active_paid,
        "active_free": active_free,
        "by_niche":    dict(by_niche),
        "mrr":         round(mrr, 2),
        "subscribers": rows,
    }


def _cli():
    import argparse
    p = argparse.ArgumentParser(description="NicheLens subscriber lifecycle")
    sub = p.add_subparsers(dest="cmd", required=True)
    p_add = sub.add_parser("add", help="Subscribe an email to a niche")
    p_add.add_argument("email")
    p_add.add_argument("niche")
    p_add.add_argument("--plan", default="free", choices=list(PLANS))
    p_add.add_argument("--notes", default="")
    p_act = sub.add_parser("activate", help="Flip pending paid subscriber to active")
    p_act.add_argument("email")
    p_act.add_argument("--niche", default="")
    p_can = sub.add_parser("cancel", help="Churn active/pending subscription(s)")
    p_can.add_argument("email")
    p_can.add_argument("--niche", default="",
                       help="Restrict to one niche; default: churn all of this email's rows")
    p_can.add_argument("--reason", default="")
    p_lis = sub.add_parser("list", help="List subscribers + MRR + per-niche counts")
    p_lis.add_argument("--niche", default="")
    args = p.parse_args()
    if args.cmd == "add":
        print(json.dumps(add(args.email, args.niche, args.plan, args.notes), indent=2))
    elif args.cmd == "activate":
        print(json.dumps(activate(args.email, args.niche), indent=2))
    elif args.cmd == "cancel":
        print(json.dumps(cancel(args.email, args.niche, args.reason), indent=2))
    elif args.cmd == "list":
        out = listing(args.niche)
        out_summary = {k: out[k] for k in (
            "total", "active", "active_paid", "active_free",
            "pending", "churned", "by_niche", "mrr")}
        print(json.dumps(out_summary, indent=2))
        for s in out["subscribers"]:
            print(f"  {s.get('status','?'):>8s}  {s.get('tier','?'):<4s}  "
                  f"{s.get('niche',''):<24s}  {s.get('email','')}")


if __name__ == "__main__":
    _cli()
