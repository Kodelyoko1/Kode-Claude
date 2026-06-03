"""
Retainer renewal-reminder + monthly counter reset for outreach_service.

The OAS billing cycle:
  · A client paid → status=active, payment_verified=True, next_billing_date=+30d
  · 30 days later, the PayPal subscription auto-charges (if configured) or the
    invoice ages out and they need a chase email
  · campaigns_run_this_month tracks per-cycle usage and must reset on day 1

This module ships two operations that the runner exposes as CLI flags:
  send_renewal_reminders()  — email clients whose next_billing_date is within
                              OAS_RENEWAL_WINDOW_DAYS (default 3) or already past,
                              with the live PayPal.me link for their tier price.
                              Idempotent per billing cycle via
                              renewal_reminder_sent_for=<next_billing_date>.
  monthly_reset()           — zero campaigns_run_this_month for every client
                              whose last_reset_month != current YYYY-MM. Safe to
                              call daily; only the first call of a new month does
                              work.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from email_template import send_branded_email
from paywall.paypal import paypalme_link

DATA_DIR    = Path(__file__).parent.parent / "data"
OAS_FILE    = DATA_DIR / "outreach_clients.json"
LOG_FILE    = DATA_DIR / "oas_renewal_log.json"

RENEWAL_WINDOW_DAYS = int(os.environ.get("OAS_RENEWAL_WINDOW_DAYS", "3"))


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


def _log(event: str, client_id: str, **extra) -> None:
    rec = _load(LOG_FILE, [])
    if not isinstance(rec, list):
        rec = []
    rec.append({"ts": _now(), "event": event, "client_id": client_id, **extra})
    _save(LOG_FILE, rec)


# ─────────────────────────── Renewal reminders ───────────────────────────

def _payment_url_for(client: dict) -> str:
    """Pick the best payment URL: stored URL > PayPal.me fallback."""
    stored = client.get("payment_url", "")
    if stored:
        return stored
    fee = float(client.get("monthly_fee", 0))
    return paypalme_link(fee, f"Wholesale Omniverse — {client.get('tier','').title()} renewal")


def _reminder_text(client: dict, payment_url: str, days_until: int) -> str:
    first = (client.get("name") or "there").split()[0]
    when = "today" if days_until <= 0 else f"in {days_until} day{'s' if days_until != 1 else ''}"
    fee = float(client.get("monthly_fee", 0))
    return (
        f"Hi {first},\n\n"
        f"Quick reminder — your Wholesale Omniverse outreach retainer renews {when} "
        f"(${fee:.0f}).\n\n"
        f"Recent service:\n"
        f"  · Campaigns run:   {client.get('total_campaigns_run', 0)}\n"
        f"  · Leads delivered: {client.get('total_leads_found', 0)}\n"
        f"  · Outreach emails: {client.get('total_emails_sent', 0)}\n\n"
        f"Renew here: {payment_url}\n\n"
        f"As soon as PayPal confirms, the next batch of campaigns runs on the "
        f"normal cron — nothing else to do on your end.\n\n"
        f"Questions or want to change tier? Just reply.\n\n"
        f"— Tyreese Lumiere, Wholesale Omniverse LLC"
    )


def _reminder_html(client: dict, payment_url: str, days_until: int) -> str:
    first = (client.get("name") or "there").split()[0]
    when = "today" if days_until <= 0 else f"in <strong>{days_until} day{'s' if days_until != 1 else ''}</strong>"
    fee = float(client.get("monthly_fee", 0))
    return (
        f"<p>Hi <strong>{first}</strong>,</p>"
        f"<p>Quick reminder — your Wholesale Omniverse outreach retainer renews "
        f"{when} (<strong>${fee:.0f}</strong>).</p>"
        f"<p><strong>Recent service:</strong></p>"
        f"<ul style='font-size:14px;'>"
        f"<li>Campaigns run: <strong>{client.get('total_campaigns_run', 0)}</strong></li>"
        f"<li>Leads delivered: <strong>{client.get('total_leads_found', 0)}</strong></li>"
        f"<li>Outreach emails: <strong>{client.get('total_emails_sent', 0)}</strong></li>"
        f"</ul>"
        f"<p style='margin-top:18px;'>"
        f"<a href='{payment_url}' "
        f"style='display:inline-block;padding:10px 24px;background:#c9a84c;color:#0f172a;"
        f"font-weight:700;text-decoration:none;border-radius:6px;'>Renew ${fee:.0f}</a></p>"
        f"<p style='font-size:13px;color:#666;'>"
        f"As soon as PayPal confirms, the next batch of campaigns runs on the normal "
        f"cron — nothing else to do on your end.</p>"
    )


def _eligible_for_reminder(client: dict, today: date) -> tuple[bool, int]:
    if client.get("status") != "active":
        return False, 0
    nb = client.get("next_billing_date", "")
    if not nb:
        return False, 0
    try:
        bd = date.fromisoformat(nb)
    except ValueError:
        return False, 0
    days_until = (bd - today).days
    if days_until > RENEWAL_WINDOW_DAYS:
        return False, days_until
    # Idempotency: don't re-send for the same billing date
    if client.get("renewal_reminder_sent_for") == nb:
        return False, days_until
    return True, days_until


def send_renewal_reminders(dry_run: bool = False) -> dict:
    clients = _load(OAS_FILE, {})
    if not isinstance(clients, dict):
        return {"error": "outreach_clients.json wrong shape", "sent": 0}

    today = date.today()
    queue = []
    for cid, c in clients.items():
        eligible, days_until = _eligible_for_reminder(c, today)
        if eligible:
            queue.append((cid, c, days_until))

    sent, failures = 0, []
    previews = []

    for cid, c, days_until in queue:
        payment_url = _payment_url_for(c)
        subject = (f"Your Wholesale Omniverse retainer renews "
                   f"{'today' if days_until <= 0 else f'in {days_until}d'} — ${c.get('monthly_fee', 0):.0f}")
        body_text = _reminder_text(c, payment_url, days_until)
        body_html = _reminder_html(c, payment_url, days_until)

        if dry_run:
            previews.append({"client_id": cid, "email": c.get("email"),
                             "subject": subject, "days_until": days_until,
                             "payment_url": payment_url})
            continue

        result = send_branded_email(
            to_email=c["email"], subject=subject,
            body_text=body_text, body_html_inner=body_html,
        )
        if result.get("status") == "sent":
            clients[cid]["renewal_reminder_sent_for"] = c.get("next_billing_date", "")
            clients[cid]["renewal_reminder_sent_at"]  = _now()
            sent += 1
            _log("reminder_sent", cid, days_until=days_until,
                 next_billing_date=c.get("next_billing_date"))
        else:
            failures.append({"client_id": cid,
                             "error": result.get("error") or result.get("status")})
            _log("reminder_failed", cid,
                 error=result.get("error") or result.get("status"))

    if sent:
        _save(OAS_FILE, clients)

    return {
        "attempted": len(queue),
        "sent": sent,
        "failures": failures,
        "dry_run": dry_run,
        "previews": previews if dry_run else [],
    }


# ─────────────────────────── Monthly counter reset ───────────────────────────

def monthly_reset() -> dict:
    """Zero campaigns_run_this_month at the start of each month. Idempotent."""
    clients = _load(OAS_FILE, {})
    if not isinstance(clients, dict):
        return {"error": "outreach_clients.json wrong shape", "reset_count": 0}

    cur_month = date.today().strftime("%Y-%m")
    reset = []
    for cid, c in clients.items():
        if c.get("last_reset_month") == cur_month:
            continue
        clients[cid]["campaigns_run_this_month"] = 0
        clients[cid]["last_reset_month"] = cur_month
        reset.append(cid)
        _log("monthly_reset", cid, month=cur_month)

    if reset:
        _save(OAS_FILE, clients)
    return {"reset_count": len(reset), "month": cur_month, "client_ids": reset}


def _cli():
    import argparse
    parser = argparse.ArgumentParser(description="OAS renewals + monthly reset")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_rem = sub.add_parser("send", help="Send renewal-reminder emails")
    p_rem.add_argument("--dry-run", action="store_true")
    sub.add_parser("reset", help="Reset campaigns_run_this_month for the new month")
    args = parser.parse_args()
    if args.cmd == "send":
        print(json.dumps(send_renewal_reminders(dry_run=args.dry_run), indent=2))
    elif args.cmd == "reset":
        print(json.dumps(monthly_reset(), indent=2))


if __name__ == "__main__":
    _cli()
