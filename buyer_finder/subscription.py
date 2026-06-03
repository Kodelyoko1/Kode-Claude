"""
Buyer subscription lifecycle: prospect → pitched → replied → trial → paid → churned.

The product the agent sells:
  · A weekly digest of motivated-seller leads (addresses + contact info + tags)
  · $47/mo recurring (configurable via BF_SUBSCRIPTION_PRICE)
  · 1-week free trial as the lead-in

State machine, stored on each buyer record in cash_buyers.json:

  prospect       (no fields set yet — newly scraped)
   └─→ pitched     intro_email_sent=True              [via send_pitch()]
        └─→ replied  replied=True                     [via mark_replied() — owner action or future webhook]
             └─→ trial   trial_started_at=ISO         [via start_trial()]
                  ├─→ active  subscription_status=active, subscription_started_at=ISO
                  │           subscription_price_usd=$47
                  └─→ churned subscription_status=churned, churned_at=ISO
                                          (trial expired without payment, or active cancelled)

TRIAL_DURATION_DAYS controls how long the free sample window is. After it
expires, expire_trials() flips non-converted buyers to churned and sends a
last-chance "want to keep getting these?" pitch.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))
from email_template import send_branded_email

DATA_DIR    = Path(__file__).parent.parent / "data"
BUYERS_FILE = DATA_DIR / "cash_buyers.json"
LOG_FILE    = DATA_DIR / "bf_subscription_log.json"

SUBSCRIPTION_PRICE_USD = float(os.environ.get("BF_SUBSCRIPTION_PRICE", "47"))
TRIAL_DURATION_DAYS    = int(os.environ.get("BF_TRIAL_DAYS", "7"))
PITCH_DAILY_CAP        = int(os.environ.get("BF_PITCH_DAILY_CAP", "40"))
SENDER_NAME            = "Tyreese Lumiere"
SENDER_PHONE           = "207-385-4041"
COMPANY_NAME           = "Wholesale Omniverse LLC"


# ─────────────────────────── Helpers ───────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def _log(event: str, buyer_id: str, **extra) -> None:
    rec = _load(LOG_FILE, [])
    if not isinstance(rec, list):
        rec = []
    rec.append({"ts": _now(), "event": event, "buyer_id": buyer_id, **extra})
    _save(LOG_FILE, rec)


def funnel_state(b: dict) -> str:
    if b.get("subscription_status") == "active":
        return "active_paid"
    if b.get("subscription_status") == "churned":
        return "churned"
    if b.get("trial_started_at") and not b.get("trial_converted_at"):
        return "trial"
    if b.get("replied"):
        return "replied"
    if b.get("intro_email_sent"):
        return "pitched"
    return "prospect"


def subscribe_url() -> str:
    explicit = os.environ.get("BF_SUBSCRIBE_URL", "")
    if explicit:
        return explicit
    pp = os.environ.get("PAYPAL_ME_USERNAME", "")
    if pp:
        return f"https://paypal.me/{pp}/{int(SUBSCRIPTION_PRICE_USD)}"
    return "[BF_SUBSCRIBE_URL not configured]"


# ─────────────────────────── Pitch: prospect → pitched ───────────────────────────

PITCH_SUBJECT = "{markets} cash-buyer drop — free sample this week?"

PITCH_TEMPLATE_TEXT = """Hi {name},

I'm Tyreese with Wholesale Omniverse. I aggregate off-market motivated-seller leads in {markets} — foreclosure, tax-delinquent, vacant, probate, code violations — and ship them out as a weekly drop to a small list of cash buyers.

The numbers right now in our pipeline you'd see:
  - {hot_count} distress-tagged leads
  - {phone_count} with verified phones
  - covering {markets_count} markets

The product is $47/mo, but I'm giving away a 1-week sample first — no card, no commitment. You'll get next Monday's drop in your inbox and decide from there.

Want in on the sample?  Reply YES and I'll add you to Monday's send.

— Tyreese
{sender_email}
{phone}
{company}"""


PITCH_HTML = """\
<p style="margin:0 0 16px;color:#cccccc;">Hi {name},</p>
<p style="margin:0 0 16px;color:#cccccc;">
  I'm <strong>Tyreese with Wholesale Omniverse</strong>. I aggregate off-market motivated-seller
  leads in <strong>{markets}</strong> &mdash; foreclosure, tax-delinquent, vacant, probate, code
  violations &mdash; and ship them out as a weekly drop to a small list of cash buyers.
</p>
<p style="margin:0 0 8px;color:#cccccc;">Numbers in the current pipeline:</p>
<table cellpadding="0" cellspacing="0" style="margin:0 0 16px;">
  <tr><td style="padding:3px 0;color:#cccccc;"><span style="color:#FDD023;font-weight:bold;">&#10003;</span>&nbsp; {hot_count} distress-tagged leads</td></tr>
  <tr><td style="padding:3px 0;color:#cccccc;"><span style="color:#FDD023;font-weight:bold;">&#10003;</span>&nbsp; {phone_count} with verified phones</td></tr>
  <tr><td style="padding:3px 0;color:#cccccc;"><span style="color:#FDD023;font-weight:bold;">&#10003;</span>&nbsp; covering {markets_count} markets</td></tr>
</table>
<p style="margin:0 0 16px;color:#cccccc;">
  The product is <strong>$47/mo</strong>, but I'm giving away a <strong>1-week sample</strong> first &mdash;
  no card, no commitment. You'll get next Monday's drop in your inbox and decide from there.
</p>
<p style="margin:0 0 24px;color:#cccccc;">
  Want in on the sample? <strong>Reply YES</strong> and I'll add you to Monday's send.
</p>
<p style="margin:0;color:#cccccc;">&mdash; Tyreese</p>"""


def _live_pipeline_stats() -> dict:
    """Pull current lead-pool stats for the pitch — these are the proof points."""
    try:
        leads = _load(DATA_DIR / "leads.json", {})
    except Exception:
        leads = {}
    if not isinstance(leads, dict):
        leads = {}
    from followup_agent.escalation import ALL_DISTRESS  # reuse the same set
    hot_count = sum(1 for l in leads.values()
                    if any(t in (l.get("motivation") or "").lower() for t in ALL_DISTRESS))
    phone_count = sum(1 for l in leads.values() if l.get("seller_phone"))
    markets = {(l.get("city",""), l.get("state","")) for l in leads.values()
               if l.get("city") and l.get("state")}
    return {
        "hot_count":     hot_count,
        "phone_count":   phone_count,
        "markets_count": len(markets),
    }


def send_pitch(buyer_id: str) -> dict:
    buyers = _load(BUYERS_FILE, {})
    if buyer_id not in buyers:
        return {"status": "skipped", "reason": "not found"}
    b = buyers[buyer_id]
    if b.get("intro_email_sent"):
        return {"status": "skipped", "reason": "already pitched"}
    email = b.get("email", "")
    if not email:
        return {"status": "skipped", "reason": "no email"}

    stats = _live_pipeline_stats()
    markets = b.get("markets", "your target markets")
    name    = (b.get("name") or "Investor").strip().split("—")[0].strip()[:60]
    smtp_user = os.environ.get("SMTP_USER", "info@wholesaleomniverse.com")

    ctx = dict(
        name=name, markets=markets,
        hot_count=stats["hot_count"], phone_count=stats["phone_count"],
        markets_count=stats["markets_count"],
        sender_email=smtp_user, phone=SENDER_PHONE, company=COMPANY_NAME,
    )
    subject = PITCH_SUBJECT.format(markets=markets)
    body_text = PITCH_TEMPLATE_TEXT.format(**ctx)
    body_html = PITCH_HTML.format(**ctx)

    result = send_branded_email(
        to_email=email, subject=subject,
        body_text=body_text, body_html_inner=body_html,
    )
    if result.get("status") == "sent":
        buyers[buyer_id]["intro_email_sent"] = True
        buyers[buyer_id]["intro_email_sent_at"] = _now()
        buyers[buyer_id]["updated_at"] = _now()
        _save(BUYERS_FILE, buyers)
        _log("pitched", buyer_id, markets=markets)
    return {"buyer_id": buyer_id, "email": email,
             "status": result.get("status"),
             "error": result.get("error")}


def run_pitch_pass(limit: Optional[int] = None) -> dict:
    """Pitch every prospect (has email, not yet pitched), up to the daily cap."""
    cap = min(limit or PITCH_DAILY_CAP, PITCH_DAILY_CAP)
    buyers = _load(BUYERS_FILE, {})
    if not isinstance(buyers, dict):
        return {"error": "cash_buyers.json shape"}
    candidates = [bid for bid, b in buyers.items()
                  if b.get("email") and not b.get("intro_email_sent")]
    candidates = candidates[:cap]
    sent, skipped, failed = 0, 0, 0
    details = []
    for bid in candidates:
        r = send_pitch(bid)
        s = r.get("status")
        details.append(r)
        if s == "sent":
            sent += 1
        elif s == "failed":
            failed += 1
        else:
            skipped += 1
    return {
        "candidates_total": len([b for b in buyers.values()
                                   if b.get("email") and not b.get("intro_email_sent")]),
        "cap": cap, "sent": sent, "skipped": skipped, "failed": failed,
        "details": details[:10],
    }


# ─────────────────────────── Trial lifecycle ───────────────────────────

def mark_replied(buyer_id: str, note: str = "") -> dict:
    buyers = _load(BUYERS_FILE, {})
    if buyer_id not in buyers:
        return {"error": "not found"}
    buyers[buyer_id]["replied"] = True
    buyers[buyer_id]["replied_at"] = _now()
    buyers[buyer_id]["updated_at"] = _now()
    if note:
        prior = buyers[buyer_id].get("notes", "")
        buyers[buyer_id]["notes"] = (prior + f"\n[{_now()[:10]}] REPLIED: {note}").strip()
    _save(BUYERS_FILE, buyers)
    _log("replied", buyer_id, note=note)
    return {"ok": True, "buyer_id": buyer_id, "state": funnel_state(buyers[buyer_id])}


def start_trial(buyer_id: str) -> dict:
    buyers = _load(BUYERS_FILE, {})
    if buyer_id not in buyers:
        return {"error": "not found"}
    b = buyers[buyer_id]
    if b.get("trial_started_at"):
        return {"status": "skipped", "reason": "trial already started",
                "trial_started_at": b["trial_started_at"]}
    trial_start = _now()
    trial_end = (datetime.now(timezone.utc) + timedelta(days=TRIAL_DURATION_DAYS)).isoformat()
    buyers[buyer_id]["trial_started_at"] = trial_start
    buyers[buyer_id]["trial_ends_at"]    = trial_end
    buyers[buyer_id]["updated_at"]       = _now()
    _save(BUYERS_FILE, buyers)
    _log("trial_started", buyer_id, ends_at=trial_end)
    return {"ok": True, "buyer_id": buyer_id, "trial_ends_at": trial_end}


def mark_paid(buyer_id: str, *, plan_price_usd: Optional[float] = None) -> dict:
    buyers = _load(BUYERS_FILE, {})
    if buyer_id not in buyers:
        return {"error": "not found"}
    price = plan_price_usd if plan_price_usd is not None else SUBSCRIPTION_PRICE_USD
    buyers[buyer_id]["subscription_status"]   = "active"
    buyers[buyer_id]["subscription_started_at"] = _now()
    buyers[buyer_id]["subscription_price_usd"]  = price
    buyers[buyer_id]["trial_converted_at"]    = _now()
    buyers[buyer_id]["updated_at"]            = _now()
    _save(BUYERS_FILE, buyers)
    _log("subscribed", buyer_id, price_usd=price)
    return {"ok": True, "buyer_id": buyer_id, "price": price}


def mark_churned(buyer_id: str, reason: str = "") -> dict:
    buyers = _load(BUYERS_FILE, {})
    if buyer_id not in buyers:
        return {"error": "not found"}
    buyers[buyer_id]["subscription_status"] = "churned"
    buyers[buyer_id]["churned_at"]         = _now()
    buyers[buyer_id]["churn_reason"]       = reason
    buyers[buyer_id]["updated_at"]         = _now()
    _save(BUYERS_FILE, buyers)
    _log("churned", buyer_id, reason=reason)
    return {"ok": True, "buyer_id": buyer_id, "reason": reason}


def expire_trials() -> dict:
    """Find trials that ended → flip to churned + send last-chance subscribe email."""
    buyers = _load(BUYERS_FILE, {})
    now = datetime.now(timezone.utc)
    expired = []
    for bid, b in buyers.items():
        if not b.get("trial_started_at") or b.get("trial_converted_at"):
            continue
        if b.get("subscription_status") in ("active", "churned"):
            continue
        end = b.get("trial_ends_at")
        if not end:
            continue
        try:
            end_dt = datetime.fromisoformat(end.replace("Z", "+00:00"))
        except Exception:
            continue
        if now < end_dt:
            continue
        expired.append(bid)

    sent_lastchance = 0
    for bid in expired:
        # Last-chance subscribe email
        b = buyers[bid]
        email = b.get("email", "")
        name = (b.get("name") or "Investor").strip().split("—")[0].strip()[:60]
        if email:
            body = (
                f"Hi {name},\n\n"
                f"Your free sample week of the {b.get('markets','your-market')} "
                f"motivated-seller drop just ended.\n\n"
                f"If the leads were useful, keep them coming for ${SUBSCRIPTION_PRICE_USD:.0f}/mo: "
                f"{subscribe_url()}\n\n"
                f"No worries either way — reply STOP if you'd rather not hear from me again.\n\n"
                f"— Tyreese\n{COMPANY_NAME}"
            )
            r = send_branded_email(
                to_email=email,
                subject=f"Sample ended — keep getting {b.get('markets','the')} drops?",
                body_text=body,
                body_html_inner=body.replace("\n", "<br>"),
            )
            if r.get("status") == "sent":
                sent_lastchance += 1
                _log("trial_lastchance_sent", bid)
        mark_churned(bid, reason="trial_expired_no_conversion")
    return {"expired": len(expired), "lastchance_sent": sent_lastchance,
            "expired_ids": expired}


def state_summary() -> dict:
    buyers = _load(BUYERS_FILE, {})
    if not isinstance(buyers, dict):
        return {"error": "shape"}
    stages = {}
    for b in buyers.values():
        s = funnel_state(b)
        stages[s] = stages.get(s, 0) + 1
    active = [b for b in buyers.values() if b.get("subscription_status") == "active"]
    return {
        "total_buyers":  len(buyers),
        "by_stage":      stages,
        "mrr_usd":       round(sum(float(b.get("subscription_price_usd", SUBSCRIPTION_PRICE_USD))
                                    for b in active), 2),
        "active_subscribers": len(active),
    }


# ─────────────────────────── CLI ───────────────────────────

def main() -> int:
    import argparse, json as _json
    p = argparse.ArgumentParser(description="Buyer subscription tooling")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("summary")
    sub.add_parser("expire-trials")
    pp = sub.add_parser("pitch"); pp.add_argument("--limit", type=int, default=None)
    rp = sub.add_parser("mark-replied"); rp.add_argument("buyer_id"); rp.add_argument("--note", default="")
    st = sub.add_parser("start-trial"); st.add_argument("buyer_id")
    mp = sub.add_parser("mark-paid"); mp.add_argument("buyer_id"); mp.add_argument("--price", type=float, default=None)
    mc = sub.add_parser("mark-churned"); mc.add_argument("buyer_id"); mc.add_argument("--reason", default="")
    args = p.parse_args()

    if args.cmd == "summary" or args.cmd is None:
        print(_json.dumps(state_summary(), indent=2))
    elif args.cmd == "pitch":
        print(_json.dumps(run_pitch_pass(args.limit), indent=2))
    elif args.cmd == "mark-replied":
        print(_json.dumps(mark_replied(args.buyer_id, args.note), indent=2))
    elif args.cmd == "start-trial":
        print(_json.dumps(start_trial(args.buyer_id), indent=2))
    elif args.cmd == "mark-paid":
        print(_json.dumps(mark_paid(args.buyer_id, plan_price_usd=args.price), indent=2))
    elif args.cmd == "mark-churned":
        print(_json.dumps(mark_churned(args.buyer_id, args.reason), indent=2))
    elif args.cmd == "expire-trials":
        print(_json.dumps(expire_trials(), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
