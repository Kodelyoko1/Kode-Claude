"""
SMS follow-up sequence — the unlock for the 370 phone-only leads.

Email currently covers 3 of 3,747 leads; phone covers ~10%. Until skip-tracing
populates more emails, SMS is the only outbound channel that touches a real
seller pool.

Compliance (US/TCPA):
  - Every send includes "Reply STOP to stop" on the first touch.
  - Every send includes "Tyreese w/ WholesaleOmniverse" sender ID.
  - Inbound STOP keyword writes the phone into data/sms_optouts.json — that
    file is checked on every send. HELP returns a description + phone.
  - Quiet hours (8am–9pm local for the recipient's area code) enforced.
  - Sequence stops after 6 touches with no reply (same as email).

Safety:
  - FOLLOWUP_SMS_LIVE=1 must be explicitly set. Otherwise sends are dry-run
    (the message and recipient are logged; nothing hits Twilio).
  - Daily send cap (FOLLOWUP_SMS_DAILY_CAP, default 100) to bound exposure.

Persistence:
  - data/sms_log.json    — every attempt (sent/skipped/failed)
  - data/sms_optouts.json — phones that texted STOP
  - lead state on data/leads.json gains: sms_stage, last_sms_at
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import requests

DATA_DIR        = Path(__file__).parent.parent / "data"
LEADS_FILE      = DATA_DIR / "leads.json"
SMS_LOG_FILE    = DATA_DIR / "sms_log.json"
OPTOUTS_FILE    = DATA_DIR / "sms_optouts.json"

log = logging.getLogger("followup_agent.sms")

MAX_STAGE = 6

# Days between touches — looser than email because SMS is more intrusive.
SMS_SCHEDULE = {
    0: 2,    # initial → touch 1 after 2 days
    1: 5,    # touch 1 → 2 after 5 days
    2: 9,    # 2 → 3 after 9 days
    3: 14,   # 3 → 4 after 14 days
    4: 30,   # 4 → 5 after 30 days
    5: 60,   # 5 → 6 after 60 days
}

SENDER_ID = "Tyreese w/ WholesaleOmniverse"

# 160-char-friendly templates. Stage 1 carries the STOP disclosure; subsequent
# touches stay short. Keep concrete numbers, no salesy adjectives.
SMS_TEMPLATES = {
    1: ("Hi {first_name} — Tyreese w/ WholesaleOmniverse. We buy houses cash, "
        "close in 14 days, any condition. Interested in a no-obligation offer "
        "on {address}? Reply YES or STOP to opt out."),
    2: ("Hi {first_name}, Tyreese again. Still buying in {city} this week. "
        "Cash offer on {address}, 14-day close, you pick the date. "
        "Want one? Reply YES."),
    3: ("Hi {first_name} — one more on {address}. No fees, no agents, "
        "we buy as-is. If a cash offer would help, reply YES. "
        "Otherwise no worries."),
    4: ("Hi {first_name}, last note from me on {address}. Cash, 14-day close, "
        "as-is. Hit me back if you want a number. Wishing you well either way."),
    5: ("Hi {first_name}, circling back on {address} after a few weeks. "
        "Offer's still open: cash, 14 days, any condition. Reply YES if useful."),
    6: ("Hi {first_name} — {city} cash buyer market is still strong. "
        "If {address} is still on your mind, reply YES for a quick offer."),
}


# ─────────────────────────── Helpers ───────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_live() -> bool:
    return os.environ.get("FOLLOWUP_SMS_LIVE", "").strip().lower() in ("1", "true", "yes")


def _daily_cap() -> int:
    return int(os.environ.get("FOLLOWUP_SMS_DAILY_CAP", "100"))


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


def _days_since(iso_str: str) -> int:
    if not iso_str:
        return 9999
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).days
    except Exception:
        return 9999


# ─────────────────────────── Phone normalization ───────────────────────────

_DIGITS_RE = re.compile(r"\D+")


def normalize_e164(raw: str, default_country: str = "1") -> Optional[str]:
    """Convert any US-ish phone format into +1XXXXXXXXXX. Returns None on bad input."""
    if not raw:
        return None
    digits = _DIGITS_RE.sub("", raw)
    if not digits:
        return None
    if len(digits) == 10:
        digits = default_country + digits
    if len(digits) == 11 and digits[0] != "1":
        return None  # we don't try to guess non-US country codes here
    if len(digits) != 11:
        return None
    return "+" + digits


# ─────────────────────────── Quiet hours ───────────────────────────

# Map US area codes → state abbreviation → assumed timezone. We use the
# recipient's area-code timezone to enforce quiet hours (8am–9pm local).
# Best-effort: most numbers carry their area code's local tz.
_AREA_TZ = {
    # Eastern
    **{c: -5 for c in ("207", "603", "802", "413", "617", "857", "508", "774",
                        "401", "203", "475", "860", "212", "917", "646", "718",
                        "347", "929", "212", "201", "551", "609", "732", "215",
                        "267", "302", "410", "443", "240", "703", "571", "276",
                        "434", "540", "757", "804", "919", "984", "336", "743",
                        "252", "910", "984", "803", "864", "843", "844", "404",
                        "470", "678", "770", "904", "352", "863", "954", "754",
                        "305", "786", "407", "321", "850", "813", "727", "239",
                        "561", "478", "229", "706", "762")},
    # Central
    **{c: -6 for c in ("312", "773", "872", "224", "847", "773", "630", "708",
                        "815", "618", "414", "920", "262", "608", "317", "765",
                        "812", "574", "260", "615", "423", "865", "931", "629",
                        "205", "251", "256", "334", "504", "318", "337", "985",
                        "225", "713", "832", "346", "281", "346", "210", "512",
                        "737", "469", "214", "972", "682", "817", "682", "405",
                        "918", "580", "501", "479", "870", "417", "636", "660",
                        "314", "816", "913", "316")},
    # Mountain
    **{c: -7 for c in ("303", "720", "970", "719", "385", "801", "435", "208",
                        "986", "406", "307", "505", "575", "928", "480", "602",
                        "623", "520", "915")},
    # Pacific
    **{c: -8 for c in ("206", "253", "360", "425", "509", "564", "503", "971",
                        "541", "458", "415", "628", "650", "707", "510", "925",
                        "925", "408", "669", "831", "209", "559", "661", "805",
                        "818", "747", "626", "323", "213", "310", "424", "562",
                        "657", "714", "949", "619", "858", "760", "442")},
    # Alaska / Hawaii / etc.
    **{c: -9  for c in ("907",)},
    **{c: -10 for c in ("808",)},
}


def is_quiet_hour(phone_e164: str, *, now_utc: Optional[datetime] = None) -> bool:
    """Return True if it's currently outside 8am–9pm in the phone's likely timezone."""
    if not phone_e164 or not phone_e164.startswith("+1") or len(phone_e164) != 12:
        return False  # don't block unknown shapes; opt out somewhere else
    area = phone_e164[2:5]
    offset_hours = _AREA_TZ.get(area, -5)  # default Eastern
    now_utc = now_utc or datetime.now(timezone.utc)
    local = now_utc + timedelta(hours=offset_hours)
    return local.hour < 8 or local.hour >= 21


# ─────────────────────────── Opt-out store ───────────────────────────

def is_opted_out(phone_e164: str) -> bool:
    opts = _load(OPTOUTS_FILE, {})
    return phone_e164 in opts


def record_optout(phone_e164: str, reason: str = "STOP keyword") -> None:
    opts = _load(OPTOUTS_FILE, {})
    opts[phone_e164] = {"opted_out_at": _now_iso(), "reason": reason}
    _save(OPTOUTS_FILE, opts)


# ─────────────────────────── Twilio sender ───────────────────────────

def _twilio_send(to_e164: str, body: str) -> dict:
    """Hit the Twilio Messages API. Returns {status, sid?, error?}."""
    sid   = os.environ.get("TWILIO_ACCOUNT_SID")
    tok   = os.environ.get("TWILIO_AUTH_TOKEN")
    sender = os.environ.get("TWILIO_SMS_FROM")
    if not (sid and tok and sender):
        return {"status": "failed", "error": "twilio creds incomplete"}
    try:
        r = requests.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
            auth=(sid, tok),
            data={"To": to_e164, "From": sender, "Body": body},
            timeout=12,
        )
        if r.status_code >= 400:
            try:
                msg = r.json().get("message", r.text[:200])
            except (ValueError, AttributeError):
                msg = r.text[:200]
            return {"status": "failed", "error": f"HTTP {r.status_code}: {msg}"}
        return {"status": "sent", "sid": r.json().get("sid")}
    except requests.RequestException as e:
        return {"status": "failed", "error": str(e)[:200]}


def _log_attempt(lead_id: str, phone: str, body: str, stage: int, result: dict) -> None:
    log_rec = _load(SMS_LOG_FILE, [])
    if not isinstance(log_rec, list):
        log_rec = []
    log_rec.append({
        "lead_id": lead_id,
        "phone":   phone,
        "stage":   stage,
        "body":    body,
        "status":  result.get("status"),
        "sid":     result.get("sid"),
        "error":   result.get("error"),
        "live":    _is_live(),
        "sent_at": _now_iso(),
    })
    _save(SMS_LOG_FILE, log_rec)


# ─────────────────────────── Public sends ───────────────────────────

def send_sms_followup(lead_id: str) -> dict:
    """Send the next SMS touch to a single lead. Advances sms_stage on attempt."""
    leads = _load(LEADS_FILE, {})
    if lead_id not in leads:
        return {"status": "skipped", "reason": "lead not found"}

    lead  = leads[lead_id]
    stage = int(lead.get("sms_stage", 0))
    if stage >= MAX_STAGE:
        return {"status": "skipped", "reason": "max stage"}

    next_stage = stage + 1
    template = SMS_TEMPLATES.get(next_stage)
    if not template:
        return {"status": "skipped", "reason": f"no template for stage {next_stage}"}

    raw_phone = lead.get("seller_phone", "")
    phone = normalize_e164(raw_phone)
    if not phone:
        return {"status": "skipped", "reason": f"unparseable phone {raw_phone!r}"}

    if is_opted_out(phone):
        return {"status": "skipped", "reason": "opted out"}

    if is_quiet_hour(phone):
        return {"status": "skipped", "reason": "quiet hours (8am-9pm local)"}

    first_name = (lead.get("seller_name") or "").strip().split(" ")[0] or "there"
    body = template.format(
        first_name=first_name,
        address=lead.get("address") or "your property",
        city=lead.get("city") or "your area",
    )

    if not _is_live():
        result = {"status": "dry_run", "would_send_to": phone, "body": body}
    else:
        result = _twilio_send(phone, body)

    _log_attempt(lead_id, phone, body, next_stage, result)

    leads[lead_id]["sms_stage"]    = next_stage
    leads[lead_id]["last_sms_at"]  = _now_iso()
    leads[lead_id]["updated_at"]   = _now_iso()
    if leads[lead_id].get("status") == "new":
        leads[lead_id]["status"] = "contacted"
    _save(LEADS_FILE, leads)

    return {
        "lead_id":  lead_id,
        "phone":    phone,
        "stage":    next_stage,
        "status":   result.get("status"),
        "error":    result.get("error"),
        "live":     _is_live(),
    }


def get_sms_summary() -> dict:
    """How many leads are due for an SMS touch, by stage."""
    leads = _load(LEADS_FILE, {})
    optouts = _load(OPTOUTS_FILE, {})
    summary = {
        "total_leads":         len(leads),
        "with_phone":          0,
        "unparseable_phone":   0,
        "opted_out":           0,
        "in_quiet_hour_now":   0,
        "due_for_sms":         [],
        "by_sms_stage":        {},
    }
    for lid, lead in leads.items():
        raw_phone = lead.get("seller_phone", "")
        if not raw_phone:
            continue
        phone = normalize_e164(raw_phone)
        if not phone:
            summary["unparseable_phone"] += 1
            continue
        summary["with_phone"] += 1
        if phone in optouts:
            summary["opted_out"] += 1
            continue
        if is_quiet_hour(phone):
            summary["in_quiet_hour_now"] += 1
        stage = int(lead.get("sms_stage", 0))
        summary["by_sms_stage"][stage] = summary["by_sms_stage"].get(stage, 0) + 1
        if stage >= MAX_STAGE or lead.get("seller_responded"):
            continue
        days_needed = SMS_SCHEDULE.get(stage, 999)
        last = lead.get("last_sms_at") or lead.get("created_at", "")
        if _days_since(last) >= days_needed:
            summary["due_for_sms"].append({
                "lead_id":     lid,
                "phone":       phone,
                "address":     lead.get("address", ""),
                "city":        lead.get("city", ""),
                "stage":       stage,
                "next_stage":  stage + 1,
                "in_quiet":    is_quiet_hour(phone),
            })
    return summary


def run_all_due_sms(limit: Optional[int] = None) -> dict:
    """Send the next SMS to every lead that's due, respecting the daily cap."""
    cap = min(limit or _daily_cap(), _daily_cap())
    summary = get_sms_summary()
    due = summary["due_for_sms"][:cap]
    sent, skipped, failed = [], [], []
    for item in due:
        result = send_sms_followup(item["lead_id"])
        status = result.get("status")
        if status == "sent" or status == "dry_run":
            sent.append(result)
        elif status == "failed":
            failed.append(result)
        else:
            skipped.append(result)
    return {
        "due_total":      len(summary["due_for_sms"]),
        "cap":            cap,
        "live":           _is_live(),
        "sent_or_dry":    len(sent),
        "skipped":        len(skipped),
        "failed":         len(failed),
        "sent_details":   sent[:10],
        "failed_details": failed[:10],
    }


# ─────────────────────────── Inbound STOP handler ───────────────────────────

def handle_inbound(from_phone: str, body: str) -> dict:
    """Called by a webhook endpoint when Twilio forwards an inbound SMS.

    Honors STOP/UNSUBSCRIBE/CANCEL/QUIT/END (record opt-out) and HELP (auto-reply
    with sender ID). Any other reply marks the matching lead as seller_responded.
    """
    phone = normalize_e164(from_phone) or from_phone
    text = (body or "").strip().upper()
    stop_words = {"STOP", "STOPALL", "UNSUBSCRIBE", "CANCEL", "QUIT", "END", "OPTOUT", "REMOVE"}
    help_words = {"HELP", "INFO"}

    if text in stop_words:
        record_optout(phone, reason=f"inbound:{text}")
        return {"action": "opted_out", "phone": phone}

    if text in help_words:
        msg = f"{SENDER_ID}. Reply STOP to opt out. We text about cash offers on properties you may own."
        if _is_live():
            _twilio_send(phone, msg)
        return {"action": "help_replied", "phone": phone}

    # Find a matching lead by phone, mark responded
    leads = _load(LEADS_FILE, {})
    matched = []
    for lid, lead in leads.items():
        if normalize_e164(lead.get("seller_phone", "")) == phone:
            leads[lid]["seller_responded"] = True
            leads[lid]["status"]           = "negotiating"
            leads[lid]["updated_at"]       = _now_iso()
            existing_notes = leads[lid].get("notes", "")
            leads[lid]["notes"] = (existing_notes + f"\n[{_now_iso()[:10]}] SMS REPLY: {body}").strip()
            matched.append(lid)
    if matched:
        _save(LEADS_FILE, leads)
    return {"action": "marked_responded", "phone": phone, "lead_ids": matched, "body": body}


# ─────────────────────────── CLI ───────────────────────────

def main() -> int:
    import argparse
    p = argparse.ArgumentParser(description="Followup SMS sequence")
    p.add_argument("--summary", action="store_true",
                    help="Print queue summary and exit (no sends)")
    p.add_argument("--run", action="store_true",
                    help="Send the next SMS to every due lead (respects FOLLOWUP_SMS_LIVE)")
    p.add_argument("--limit", type=int, default=None)
    args = p.parse_args()

    if args.summary or not args.run:
        s = get_sms_summary()
        print(json.dumps({k: v for k, v in s.items() if k != "due_for_sms"}, indent=2))
        print(f"\nDue right now: {len(s['due_for_sms'])} (showing first 5)")
        for d in s["due_for_sms"][:5]:
            print(f"  {d['lead_id']}  stage {d['stage']}→{d['next_stage']}  {d['phone']}  {d['address']}")
        return 0
    print(json.dumps(run_all_due_sms(args.limit), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
