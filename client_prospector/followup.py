"""
Client Prospector second-touch follow-up.

The original pitch in client_prospector/tools.py runs once per prospect. Cold
outreach without a follow-up leaves ~30-40% of would-be replies on the table —
prospects skim the first email, mean to come back to it, never do.

State machine on each prospect record in data/prospects.json:

  new          (just scraped)
   └─→ pitched          status=pitched, pitched_at=ISO          [tools.pitch_prospect]
        ├─→ replied      replied=True, replied_at=ISO            [mark_replied — owner action]
        │    └─→ converted converted_client_id=SAAS-NNNN/OAS-NNNN  [onboard_client.py]
        └─→ followed_up  followup_count=1, followup_1_sent_at=ISO  [send_followups]
             ├─→ replied   replied=True                              [mark_replied]
             └─→ stale     status=stale, marked_stale_at=ISO        [expire_stale]
                            (no reply STALE_AFTER_DAYS after follow-up)

Env vars:
  PROSPECTOR_FOLLOWUP_DAYS    default 5     — days after pitch before 2nd touch
  PROSPECTOR_STALE_DAYS       default 10    — days after follow-up before marking stale
  PROSPECTOR_FOLLOWUP_CAP     default 25    — max follow-ups sent per cycle
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from email_template import send_branded_email
from client_prospector.tools import PRODUCT_INFO

DATA_DIR        = Path(__file__).parent.parent / "data"
PROSPECTS_FILE  = DATA_DIR / "prospects.json"
LOG_FILE        = DATA_DIR / "cp_followup_log.json"

FOLLOWUP_DAYS = int(os.environ.get("PROSPECTOR_FOLLOWUP_DAYS", "5"))
STALE_DAYS    = int(os.environ.get("PROSPECTOR_STALE_DAYS",    "10"))
DAILY_CAP     = int(os.environ.get("PROSPECTOR_FOLLOWUP_CAP",  "25"))


# ─────────────────────────── Helpers ───────────────────────────

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


def _log(event: str, prospect_id: str, **extra) -> None:
    rec = _load(LOG_FILE, [])
    if not isinstance(rec, list):
        rec = []
    rec.append({"ts": _now(), "event": event, "prospect_id": prospect_id, **extra})
    _save(LOG_FILE, rec)


def _parse_iso(s: str):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00").split("+")[0])
    except ValueError:
        return None


# ─────────────────────────── Follow-up templates ───────────────────────────

def _followup_text(name: str, market: str, product: str) -> str:
    info = PRODUCT_INFO.get(product, PRODUCT_INFO["saas"])
    first = name.split()[0] if name else "there"
    return (
        f"Hi {first},\n\n"
        f"Following up on my note last week about {info['name']} for wholesalers in {market}.\n\n"
        f"Quick recap: {info['hook']}\n\n"
        f"If you're closing 2+ deals a month already, {info['price']} pays for itself on the first "
        f"contract — analysis that takes hours by hand runs in 30 seconds.\n\n"
        f"Worth a 5-minute look? Just reply 'YES' and I'll get you set up today.\n\n"
        f"If it's not for you, totally fine — this is my last note and I won't bug you again.\n\n"
        f"— Tyreese Lumiere, Wholesale Omniverse LLC"
    )


def _followup_html(name: str, market: str, product: str) -> str:
    info = PRODUCT_INFO.get(product, PRODUCT_INFO["saas"])
    first = name.split()[0] if name else "there"
    return (
        f"Hi <strong>{first}</strong>,<br><br>"
        f"Following up on my note last week about <strong>{info['name']}</strong> for wholesalers "
        f"in <strong>{market}</strong>.<br><br>"
        f"Quick recap: {info['hook']}<br><br>"
        f"If you're closing 2+ deals a month already, <strong>{info['price']}</strong> pays for "
        f"itself on the first contract — analysis that takes hours by hand runs in 30 seconds.<br><br>"
        f"Worth a 5-minute look? Just reply <strong>'YES'</strong> and I'll get you set up today.<br><br>"
        f"<em>If it's not for you, totally fine — this is my last note and I won't bug you again.</em>"
    )


# ─────────────────────────── Eligibility + send ───────────────────────────

def _eligible_for_followup(p: dict) -> bool:
    if p.get("replied") or p.get("converted_client_id"):
        return False
    if p.get("email_bounced"):
        return False
    if p.get("followup_count", 0) > 0:
        return False
    if p.get("status") not in ("pitched",):
        return False
    if not p.get("email"):
        return False
    sent = _parse_iso(p.get("pitched_at", ""))
    if not sent:
        return False
    return sent < datetime.now() - timedelta(days=FOLLOWUP_DAYS)


def _eligible_for_stale(p: dict) -> bool:
    if p.get("replied") or p.get("converted_client_id"):
        return False
    if p.get("status") != "followed_up":
        return False
    sent = _parse_iso(p.get("followup_1_sent_at", ""))
    if not sent:
        return False
    return sent < datetime.now() - timedelta(days=STALE_DAYS)


def send_followups(limit: int = DAILY_CAP, dry_run: bool = False) -> dict:
    prospects = _load(PROSPECTS_FILE, {})
    if not isinstance(prospects, dict):
        return {"error": "prospects.json wrong shape", "sent": 0, "attempted": 0}

    queue = [pid for pid, p in prospects.items() if _eligible_for_followup(p)][:limit]
    sent = 0
    failures = []
    dry = []

    for pid in queue:
        p = prospects[pid]
        product = p.get("product_pitched", "saas")
        subject = f"Re: Quick question about your deals in {p.get('market', '')}"
        body_text = _followup_text(p.get("name", ""), p.get("market", ""), product)
        body_html = _followup_html(p.get("name", ""), p.get("market", ""), product)

        if dry_run:
            dry.append({"prospect_id": pid, "email": p.get("email"),
                        "subject": subject, "preview": body_text[:120]})
            continue

        result = send_branded_email(
            to_email=p["email"],
            subject=subject,
            body_text=body_text,
            body_html_inner=body_html,
        )
        if result.get("status") == "sent":
            prospects[pid]["followup_count"] = p.get("followup_count", 0) + 1
            prospects[pid]["followup_1_sent_at"] = _now()
            prospects[pid]["status"] = "followed_up"
            sent += 1
            _log("followup_sent", pid, product=product, email=p["email"])
        else:
            failures.append({"prospect_id": pid,
                             "error": result.get("error") or result.get("status")})
            _log("followup_failed", pid,
                 error=result.get("error") or result.get("status"))

    if sent:
        _save(PROSPECTS_FILE, prospects)

    return {
        "attempted": len(queue),
        "sent": sent,
        "failures": failures,
        "dry_run": dry_run,
        "previews": dry if dry_run else [],
    }


def expire_stale(dry_run: bool = False) -> dict:
    """Flip followed_up prospects with no reply past STALE_DAYS to status=stale."""
    prospects = _load(PROSPECTS_FILE, {})
    if not isinstance(prospects, dict):
        return {"error": "prospects.json wrong shape", "expired": 0}

    queue = [pid for pid, p in prospects.items() if _eligible_for_stale(p)]
    if dry_run:
        return {"would_expire": len(queue), "ids": queue, "dry_run": True}

    for pid in queue:
        prospects[pid]["status"] = "stale"
        prospects[pid]["marked_stale_at"] = _now()
        _log("marked_stale", pid)

    if queue:
        _save(PROSPECTS_FILE, prospects)
    return {"expired": len(queue), "ids": queue, "dry_run": False}


# ─────────────────────────── Owner-action helpers ───────────────────────────

def mark_replied(prospect_id: str, notes: str = "") -> dict:
    prospects = _load(PROSPECTS_FILE, {})
    if prospect_id not in prospects:
        return {"error": f"Prospect {prospect_id} not found"}
    prospects[prospect_id]["replied"] = True
    prospects[prospect_id]["status"] = "replied"
    prospects[prospect_id]["replied_at"] = _now()
    if notes:
        prospects[prospect_id]["reply_notes"] = notes
    _save(PROSPECTS_FILE, prospects)
    _log("marked_replied", prospect_id, notes=notes)
    return {"status": "marked_replied", "prospect_id": prospect_id}


def mark_converted(prospect_id: str, client_id: str) -> dict:
    prospects = _load(PROSPECTS_FILE, {})
    if prospect_id not in prospects:
        return {"error": f"Prospect {prospect_id} not found"}
    prospects[prospect_id]["converted_client_id"] = client_id
    prospects[prospect_id]["status"] = "converted"
    prospects[prospect_id]["converted_at"] = _now()
    _save(PROSPECTS_FILE, prospects)
    _log("marked_converted", prospect_id, client_id=client_id)
    return {"status": "converted", "prospect_id": prospect_id, "client_id": client_id}


# ─────────────────────────── CLI ───────────────────────────

def _cli():
    import argparse
    parser = argparse.ArgumentParser(description="Client Prospector follow-up + state helpers")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("send", help="Send 2nd-touch follow-ups to eligible prospects")
    sub.add_parser("expire-stale", help="Mark followed-up prospects with no reply as stale")
    p_rep = sub.add_parser("mark-replied", help="Mark a prospect as having replied")
    p_rep.add_argument("prospect_id")
    p_rep.add_argument("--notes", default="")
    p_conv = sub.add_parser("mark-converted", help="Link a prospect to a paying client record")
    p_conv.add_argument("prospect_id")
    p_conv.add_argument("client_id")
    args = parser.parse_args()

    if args.cmd == "send":
        out = send_followups()
        print(json.dumps(out, indent=2))
    elif args.cmd == "expire-stale":
        out = expire_stale()
        print(json.dumps(out, indent=2))
    elif args.cmd == "mark-replied":
        out = mark_replied(args.prospect_id, args.notes)
        print(json.dumps(out, indent=2))
    elif args.cmd == "mark-converted":
        out = mark_converted(args.prospect_id, args.client_id)
        print(json.dumps(out, indent=2))


if __name__ == "__main__":
    _cli()
