"""
Paying-client lifecycle for SalesPageDoctor.

The fulfill_cycle() loop in salespage_doctor.tools already reads
spd_clients.json and re-audits every record where status=active,
emailing a monthly full report. But the file was consumed and never
written — clients had to be hand-edited in.

Three plans matching CLAUDE.md product description:
  full_77         — $77 one-time deep audit (tracked, $0 MRR)
  monitoring_37   — $37/mo recurring (re-audit + alerts)
  launch_147      — $147 one-time launch package (3 audits over window;
                    tracked, $0 MRR but counts toward delivered revenue)

The fulfill_cycle audit cadence is 30 days (a "monthly" audit per the
mailer copy), so monitoring_37 is the only plan that should keep
running in the monthly cron — the one-time plans (full_77, launch_147)
get one audit and then should be moved to "fulfilled" by the owner.
This module exposes `fulfill` for that one-time terminal transition.

State file: data/spd_clients.json — list of:
  {
    "name":          "Jane Maker",
    "contact_email": "jane@maker.studio",
    "url":           "https://gumroad.com/l/journal",
    "slug":          "gumroad.com_l-journal",
    "plan":          "full_77" | "monitoring_37" | "launch_147",
    "status":        "active" | "pending" | "fulfilled" | "churned",
    "added_at":      ISO,
    "activated_at":  ISO,
    "fulfilled_at":  ISO,
    "churned_at":    ISO,
    "audits_done":   N,
    "notes":         "...",
  }

Log: data/spd_client_log.json — append-only audit trail.
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

DATA_DIR    = Path(__file__).parent.parent / "data"
CLIENT_FILE = DATA_DIR / "spd_clients.json"
LOG_FILE    = DATA_DIR / "spd_client_log.json"

PLANS = {
    "full_77":       {"price_mo": 0,  "one_time": 77,  "audits_promised": 1,
                      "label": "Full deep audit ($77 one-time)"},
    "monitoring_37": {"price_mo": 37, "one_time": 0,   "audits_promised": -1,
                      "label": "Monthly monitoring ($37/mo)"},
    "launch_147":    {"price_mo": 0,  "one_time": 147, "audits_promised": 3,
                      "label": "Launch package ($147 one-time, 3 audits)"},
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


def _page_slug(url: str) -> str:
    """Mirror tools._page_slug so add() can derive a consistent slug from URL."""
    p = urlparse(url or "")
    host = p.netloc.lower().replace("www.", "")
    path = p.path.strip("/").replace("/", "-")[:50]
    raw = f"{host}_{path}" if path else host
    return re.sub(r"[^a-z0-9.-]", "-", raw)[:80]


def _find(clients: list, ident: str) -> dict | None:
    """Match by email OR slug, case-insensitive."""
    ident_lc = (ident or "").strip().lower()
    if not ident_lc:
        return None
    for c in clients:
        if _norm_email(c.get("contact_email", "")) == ident_lc:
            return c
    for c in clients:
        if (c.get("slug") or "").lower() == ident_lc:
            return c
    return None


def add(email: str, url: str, plan: str, name: str = "", notes: str = "") -> dict:
    email = _norm_email(email)
    url   = (url or "").strip()
    if not email or "@" not in email:
        return {"error": "invalid email"}
    if not url or not url.startswith(("http://", "https://")):
        return {"error": "url must be http(s)://"}
    if plan not in PLANS:
        return {"error": f"unknown plan; choose from {list(PLANS)}"}
    slug = _page_slug(url)
    if not slug:
        return {"error": "could not derive slug from url"}
    clients = _load(CLIENT_FILE, [])
    if not isinstance(clients, list):
        clients = []
    for c in clients:
        if c.get("slug") == slug and _norm_email(c.get("contact_email", "")) == email:
            return {"error": f"{email} already on file for {slug}"}
    clients.append({
        "name":          name or email.split("@")[0],
        "contact_email": email,
        "url":           url,
        "slug":          slug,
        "plan":          plan,
        "status":        "pending",
        "added_at":      _now(),
        "activated_at":  "",
        "fulfilled_at":  "",
        "churned_at":    "",
        "audits_done":   0,
        "notes":         notes,
    })
    _save(CLIENT_FILE, clients)
    _log("added", email, plan=plan, slug=slug, url=url)
    return {"status": "pending", "email": email, "slug": slug, "plan": plan}


def activate(ident: str) -> dict:
    """Flip a pending client to active (after payment confirms)."""
    clients = _load(CLIENT_FILE, [])
    if not isinstance(clients, list):
        return {"error": "spd_clients.json wrong shape"}
    c = _find(clients, ident)
    if not c:
        return {"error": f"no client matches {ident!r}"}
    if c.get("status") in ("active", "fulfilled"):
        return {"error": f"{c.get('contact_email')} already {c.get('status')}"}
    c["status"]       = "active"
    c["activated_at"] = _now()
    _save(CLIENT_FILE, clients)
    _log("activated", c.get("contact_email", ""), plan=c.get("plan"))
    return {"status": "active", "email": c.get("contact_email"),
            "slug": c.get("slug"), "plan": c.get("plan")}


def fulfill(ident: str) -> dict:
    """Mark a one-time plan (full_77, launch_147) as fulfilled — owner
    runs this after the deliverables ship so fulfill_cycle stops
    re-auditing them."""
    clients = _load(CLIENT_FILE, [])
    if not isinstance(clients, list):
        return {"error": "spd_clients.json wrong shape"}
    c = _find(clients, ident)
    if not c:
        return {"error": f"no client matches {ident!r}"}
    plan = c.get("plan", "")
    if PLANS.get(plan, {}).get("price_mo", 0) > 0:
        return {"error": f"{plan} is recurring — use cancel instead of fulfill"}
    c["status"]       = "fulfilled"
    c["fulfilled_at"] = _now()
    _save(CLIENT_FILE, clients)
    _log("fulfilled", c.get("contact_email", ""), plan=plan)
    return {"status": "fulfilled", "email": c.get("contact_email"), "plan": plan}


def cancel(ident: str, reason: str = "") -> dict:
    clients = _load(CLIENT_FILE, [])
    if not isinstance(clients, list):
        return {"error": "spd_clients.json wrong shape"}
    c = _find(clients, ident)
    if not c:
        return {"error": f"no client matches {ident!r}"}
    c["status"]     = "churned"
    c["churned_at"] = _now()
    if reason:
        c["notes"] = (c.get("notes", "") + f"\n[{_now()[:10]}] churn: {reason}").strip()
    _save(CLIENT_FILE, clients)
    _log("cancelled", c.get("contact_email", ""), reason=reason)
    return {"status": "churned", "email": c.get("contact_email")}


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
    p = argparse.ArgumentParser(description="SalesPageDoctor client lifecycle")
    sub = p.add_subparsers(dest="cmd", required=True)
    p_add = sub.add_parser("add", help="Create a pending client")
    p_add.add_argument("email")
    p_add.add_argument("url")
    p_add.add_argument("plan", choices=list(PLANS))
    p_add.add_argument("--name", default="")
    p_add.add_argument("--notes", default="")
    p_act = sub.add_parser("activate", help="Flip pending to active (after payment)")
    p_act.add_argument("ident", help="email OR slug")
    p_ful = sub.add_parser("fulfill",
                            help="Mark a one-time plan (full_77, launch_147) as fulfilled")
    p_ful.add_argument("ident", help="email OR slug")
    p_can = sub.add_parser("cancel", help="Churn client")
    p_can.add_argument("ident", help="email OR slug")
    p_can.add_argument("--reason", default="")
    sub.add_parser("list", help="List clients + MRR + one-time-collected")
    args = p.parse_args()
    if args.cmd == "add":
        print(json.dumps(add(args.email, args.url, args.plan, args.name, args.notes), indent=2))
    elif args.cmd == "activate":
        print(json.dumps(activate(args.ident), indent=2))
    elif args.cmd == "fulfill":
        print(json.dumps(fulfill(args.ident), indent=2))
    elif args.cmd == "cancel":
        print(json.dumps(cancel(args.ident, args.reason), indent=2))
    elif args.cmd == "list":
        out = listing()
        out_summary = {k: out[k] for k in (
            "total", "active", "pending", "fulfilled", "churned",
            "mrr", "one_time_collected")}
        print(json.dumps(out_summary, indent=2))
        for c in out["clients"]:
            print(f"  {c.get('status','?'):>9s}  {c.get('plan',''):<14s}  "
                  f"{(c.get('contact_email','')):<30s}  {c.get('slug','')}")


if __name__ == "__main__":
    _cli()
