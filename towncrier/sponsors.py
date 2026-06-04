"""
Sponsor-slot lifecycle for TownCrier.

This is TownCrier's revenue surface. CLAUDE.md priced three SKUs:
  $50  — single sponsor send
  $200 — 4-week sponsor run (one slot per weekly digest)
  $25  — featured event placement (one send)

build_digest() already reads tc_sponsors.json and selects the first
record with status="paid" and sends_remaining > 0, then decrements after
emailing. But there was no way to put a record in there — sponsors had
to be hand-edited into the JSON. This module fills that gap and adds a
status the digest loop ignores ("pending") so the owner can park a
quote before payment confirms.

State file: data/tc_sponsors.json — list of:
  {
    "name":            "Otter Creek Brewing",
    "city":            "portland-me",
    "tagline":         "Pints + trivia every Thursday",
    "plan":            "single_50" | "run_200" | "featured_25",
    "status":          "pending" | "paid" | "cancelled" | "fulfilled",
    "sends_total":     1 | 4 | 1,
    "sends_remaining": int (decremented by build_digest),
    "invoice_id":      "",
    "contact_email":   "",
    "added_at":        ISO,
    "activated_at":    ISO,
    "cancelled_at":    ISO,
    "notes":           "",
  }

Log: data/tc_sponsor_log.json — append-only audit trail.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

DATA_DIR  = Path(__file__).parent.parent / "data"
SPON_FILE = DATA_DIR / "tc_sponsors.json"
LOG_FILE  = DATA_DIR / "tc_sponsor_log.json"

PLANS = {
    "single_50":   {"price": 50,  "sends": 1, "label": "Single send"},
    "run_200":     {"price": 200, "sends": 4, "label": "4-week run"},
    "featured_25": {"price": 25,  "sends": 1, "label": "Featured event"},
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


def _log(event: str, name: str, **extra) -> None:
    rec = _load(LOG_FILE, [])
    if not isinstance(rec, list):
        rec = []
    rec.append({"ts": _now(), "event": event, "sponsor": name, **extra})
    _save(LOG_FILE, rec)


def _norm_city(city: str) -> str:
    return (city or "").strip().lower().replace(" ", "-")


def _find(sponsors: list, ident: str) -> dict | None:
    """Match by invoice_id OR by name (case-insensitive). Invoice wins."""
    ident_lc = (ident or "").strip().lower()
    if not ident_lc:
        return None
    for s in sponsors:
        if (s.get("invoice_id") or "").strip().lower() == ident_lc:
            return s
    for s in sponsors:
        if (s.get("name") or "").strip().lower() == ident_lc:
            return s
    return None


def add(name: str, city: str, plan: str, tagline: str = "",
        invoice_id: str = "", contact_email: str = "", notes: str = "") -> dict:
    name = (name or "").strip()
    city = _norm_city(city)
    if not name:
        return {"error": "name is required"}
    if not city:
        return {"error": "city is required"}
    if plan not in PLANS:
        return {"error": f"unknown plan; choose from {list(PLANS)}"}
    sponsors = _load(SPON_FILE, [])
    if not isinstance(sponsors, list):
        sponsors = []
    if invoice_id and any((s.get("invoice_id") or "") == invoice_id for s in sponsors):
        return {"error": f"invoice_id {invoice_id} already on file"}
    sponsors.append({
        "name":            name,
        "city":            city,
        "tagline":         tagline,
        "plan":            plan,
        "status":          "pending",
        "sends_total":     PLANS[plan]["sends"],
        "sends_remaining": 0,
        "invoice_id":      invoice_id,
        "contact_email":   (contact_email or "").strip().lower(),
        "added_at":        _now(),
        "activated_at":    "",
        "cancelled_at":    "",
        "notes":           notes,
    })
    _save(SPON_FILE, sponsors)
    _log("added", name, city=city, plan=plan, invoice_id=invoice_id)
    return {"status": "pending", "name": name, "city": city, "plan": plan,
            "price": PLANS[plan]["price"], "sends_when_paid": PLANS[plan]["sends"]}


def activate(ident: str) -> dict:
    """Flip a pending sponsor to paid + arm sends_remaining = plan's send count."""
    sponsors = _load(SPON_FILE, [])
    if not isinstance(sponsors, list):
        return {"error": "tc_sponsors.json wrong shape"}
    s = _find(sponsors, ident)
    if not s:
        return {"error": f"no sponsor matches {ident!r}"}
    if s.get("status") in ("paid", "fulfilled"):
        return {"error": f"{s.get('name')} already {s.get('status')}"}
    plan = s.get("plan", "")
    sends = PLANS.get(plan, {}).get("sends", 0)
    s["status"]          = "paid"
    s["sends_remaining"] = sends
    s["activated_at"]    = _now()
    _save(SPON_FILE, sponsors)
    _log("activated", s.get("name", ""), plan=plan, sends=sends)
    return {"status": "paid", "name": s.get("name"), "plan": plan,
            "sends_remaining": sends}


def cancel(ident: str, reason: str = "") -> dict:
    sponsors = _load(SPON_FILE, [])
    if not isinstance(sponsors, list):
        return {"error": "tc_sponsors.json wrong shape"}
    s = _find(sponsors, ident)
    if not s:
        return {"error": f"no sponsor matches {ident!r}"}
    s["status"]          = "cancelled"
    s["sends_remaining"] = 0
    s["cancelled_at"]    = _now()
    if reason:
        s["notes"] = (s.get("notes", "") + f"\n[{_now()[:10]}] cancel: {reason}").strip()
    _save(SPON_FILE, sponsors)
    _log("cancelled", s.get("name", ""), reason=reason)
    return {"status": "cancelled", "name": s.get("name")}


def listing() -> dict:
    sponsors = _load(SPON_FILE, [])
    if not isinstance(sponsors, list):
        return {"total": 0, "pending": 0, "paid": 0, "cancelled": 0, "fulfilled": 0,
                "committed_revenue": 0, "delivered_revenue": 0,
                "slots_remaining": 0, "sponsors": []}
    by_status = {"pending": 0, "paid": 0, "cancelled": 0, "fulfilled": 0}
    committed = 0  # owner-side: paid sponsors' invoice totals
    delivered = 0  # owner-side: paid + fulfilled sponsors' invoice totals
    slots_remaining = 0
    for s in sponsors:
        st = s.get("status", "pending")
        by_status[st] = by_status.get(st, 0) + 1
        price = PLANS.get(s.get("plan", ""), {}).get("price", 0)
        if st == "paid":
            committed       += price
            delivered       += price
            slots_remaining += s.get("sends_remaining", 0)
        elif st == "fulfilled":
            delivered       += price
    return {
        "total":             len(sponsors),
        **by_status,
        "committed_revenue": committed,
        "delivered_revenue": delivered,
        "slots_remaining":   slots_remaining,
        "sponsors":          sponsors,
    }


def _cli():
    import argparse
    p = argparse.ArgumentParser(description="TownCrier sponsor lifecycle")
    sub = p.add_subparsers(dest="cmd", required=True)
    p_add = sub.add_parser("add", help="Create a pending sponsor")
    p_add.add_argument("name")
    p_add.add_argument("city")
    p_add.add_argument("plan", choices=list(PLANS))
    p_add.add_argument("--tagline", default="")
    p_add.add_argument("--invoice", default="")
    p_add.add_argument("--contact", default="")
    p_add.add_argument("--notes", default="")
    p_act = sub.add_parser("activate", help="Flip pending sponsor to paid (after payment)")
    p_act.add_argument("ident", help="invoice_id OR sponsor name")
    p_can = sub.add_parser("cancel", help="Cancel sponsor (sends_remaining → 0)")
    p_can.add_argument("ident", help="invoice_id OR sponsor name")
    p_can.add_argument("--reason", default="")
    sub.add_parser("list", help="List sponsors + committed revenue + slots remaining")
    args = p.parse_args()
    if args.cmd == "add":
        print(json.dumps(add(args.name, args.city, args.plan, args.tagline,
                             args.invoice, args.contact, args.notes), indent=2))
    elif args.cmd == "activate":
        print(json.dumps(activate(args.ident), indent=2))
    elif args.cmd == "cancel":
        print(json.dumps(cancel(args.ident, args.reason), indent=2))
    elif args.cmd == "list":
        out = listing()
        out_summary = {k: out[k] for k in (
            "total", "pending", "paid", "cancelled", "fulfilled",
            "committed_revenue", "delivered_revenue", "slots_remaining")}
        print(json.dumps(out_summary, indent=2))
        for s in out["sponsors"]:
            print(f"  {s.get('status','?'):>9s}  {s.get('plan',''):<12s}  "
                  f"{s.get('city',''):<18s}  {s.get('name','')}"
                  + (f"  (sends_left={s.get('sends_remaining',0)})"
                     if s.get("status") == "paid" else ""))


if __name__ == "__main__":
    _cli()
