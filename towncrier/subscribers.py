"""
Newsletter subscriber lifecycle for TownCrier.

The build_digest() loop already reads tc_subscribers.json and emails every
record whose status=active and whose city matches the digest being built.
But there was no way to put a record in there — the file was consumed
but never written.

TownCrier's subscriber list is FREE — revenue comes from sponsors
(see towncrier/sponsors.py). So this module is intentionally simpler than
the equivalent in hudscout/speedaudit: no plan, no MRR, just an audience
ledger by city.

  add <email> <city>           — create a subscriber
  cancel <email>               — flip to churned
  list [--city CITY]           — show ledger + per-city counts

State file: data/tc_subscribers.json — list of:
  {
    "email":        "reader@example.com",
    "city":         "portland-me",
    "status":       "active" | "churned",
    "added_at":     ISO,
    "churned_at":   ISO,
    "notes":        "...",
  }

Log: data/tc_subscription_log.json — append-only audit trail.
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
SUBS_FILE = DATA_DIR / "tc_subscribers.json"
LOG_FILE  = DATA_DIR / "tc_subscription_log.json"


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


def _norm_city(city: str) -> str:
    return (city or "").strip().lower().replace(" ", "-")


def add(email: str, city: str, notes: str = "") -> dict:
    email = _norm_email(email)
    city  = _norm_city(city)
    if not email or "@" not in email:
        return {"error": "invalid email"}
    if not city:
        return {"error": "city is required"}
    subs = _load(SUBS_FILE, [])
    if not isinstance(subs, list):
        subs = []
    for s in subs:
        if _norm_email(s.get("email", "")) == email and _norm_city(s.get("city", "")) == city:
            if s.get("status") == "churned":
                s["status"] = "active"
                s["added_at"] = _now()
                s["churned_at"] = ""
                _save(SUBS_FILE, subs)
                _log("re-activated", email, city=city)
                return {"status": "re-activated", "email": email, "city": city}
            return {"error": f"{email} already subscribed to {city}"}
    subs.append({
        "email":      email,
        "city":       city,
        "status":     "active",
        "added_at":   _now(),
        "churned_at": "",
        "notes":      notes,
    })
    _save(SUBS_FILE, subs)
    _log("added", email, city=city)
    return {"status": "added", "email": email, "city": city}


def cancel(email: str, city: str = "", reason: str = "") -> dict:
    """Cancel a subscriber. If city is given, only that city's row is touched;
    otherwise every active row for the email is churned."""
    email = _norm_email(email)
    city  = _norm_city(city)
    subs = _load(SUBS_FILE, [])
    if not isinstance(subs, list):
        return {"error": "tc_subscribers.json wrong shape"}
    touched = []
    for s in subs:
        if _norm_email(s.get("email", "")) != email:
            continue
        if city and _norm_city(s.get("city", "")) != city:
            continue
        if s.get("status") == "active":
            s["status"] = "churned"
            s["churned_at"] = _now()
            if reason:
                s["notes"] = (s.get("notes", "") + f"\n[{_now()[:10]}] churn: {reason}").strip()
            touched.append(s.get("city", ""))
    if not touched:
        return {"error": f"no active subscription for {email}"
                          + (f" in {city}" if city else "")}
    _save(SUBS_FILE, subs)
    _log("cancelled", email, cities=touched, reason=reason)
    return {"status": "churned", "email": email, "cities": touched}


def listing(city: str = "") -> dict:
    city = _norm_city(city)
    subs = _load(SUBS_FILE, [])
    if not isinstance(subs, list):
        return {"total": 0, "active": 0, "churned": 0, "by_city": {}, "subscribers": []}
    rows = [s for s in subs if (not city or _norm_city(s.get("city", "")) == city)]
    by_city = Counter(_norm_city(s.get("city", "")) for s in rows
                      if s.get("status") == "active")
    return {
        "total":       len(rows),
        "active":      sum(1 for s in rows if s.get("status") == "active"),
        "churned":     sum(1 for s in rows if s.get("status") == "churned"),
        "by_city":     dict(by_city),
        "subscribers": rows,
    }


def _cli():
    import argparse
    p = argparse.ArgumentParser(description="TownCrier subscriber lifecycle")
    sub = p.add_subparsers(dest="cmd", required=True)
    p_add = sub.add_parser("add", help="Create a subscriber")
    p_add.add_argument("email")
    p_add.add_argument("city")
    p_add.add_argument("--notes", default="")
    p_can = sub.add_parser("cancel", help="Flip active subscriber to churned")
    p_can.add_argument("email")
    p_can.add_argument("--city", default="",
                       help="Restrict to one city; default: cancel all of this email's active rows")
    p_can.add_argument("--reason", default="")
    p_lis = sub.add_parser("list", help="List subscribers + per-city counts")
    p_lis.add_argument("--city", default="")
    args = p.parse_args()
    if args.cmd == "add":
        print(json.dumps(add(args.email, args.city, args.notes), indent=2))
    elif args.cmd == "cancel":
        print(json.dumps(cancel(args.email, args.city, args.reason), indent=2))
    elif args.cmd == "list":
        out = listing(args.city)
        out_summary = {k: out[k] for k in ("total", "active", "churned", "by_city")}
        print(json.dumps(out_summary, indent=2))
        for s in out["subscribers"]:
            print(f"  {s.get('status','?'):>8s}  {s.get('city',''):<20s}  {s.get('email','')}")


if __name__ == "__main__":
    _cli()
