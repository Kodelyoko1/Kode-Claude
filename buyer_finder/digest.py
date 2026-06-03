"""
Weekly motivated-seller digest — the product itself.

For each subscriber (active OR in-trial), build a personalized list of
recent motivated-seller leads filtered by their buy box (markets +
optional max_price + property type), email it, and log the delivery.
Trial buyers get an upsell footer; paid buyers get an unsubscribe footer.

Lead-priority order in each digest:
  1. Distress-tagged (foreclosure/vacant/probate/tax-delinquent/...)
  2. Has phone number
  3. Has email
  4. Recency (newest first)

Hot leads come first so the buyer sees value above the fold.
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))
from email_template import send_branded_email

DATA_DIR     = Path(__file__).parent.parent / "data"
BUYERS_FILE  = DATA_DIR / "cash_buyers.json"
LEADS_FILE   = DATA_DIR / "leads.json"
DIGEST_LOG   = DATA_DIR / "bf_digest_log.json"
DIGESTS_DIR  = DATA_DIR / "bf_digests"

SUBSCRIPTION_PRICE_USD = float(os.environ.get("BF_SUBSCRIPTION_PRICE", "47"))
MAX_LEADS_PER_DIGEST   = int(os.environ.get("BF_MAX_LEADS_PER_DIGEST", "30"))
FRESH_WINDOW_DAYS      = int(os.environ.get("BF_FRESH_WINDOW_DAYS", "21"))


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


def _subscribe_url() -> str:
    explicit = os.environ.get("BF_SUBSCRIBE_URL", "")
    if explicit:
        return explicit
    pp = os.environ.get("PAYPAL_ME_USERNAME", "")
    return f"https://paypal.me/{pp}/{int(SUBSCRIPTION_PRICE_USD)}" if pp else "[no subscribe URL]"


# ─────────────────────────── Buy-box matching ───────────────────────────

def _parse_buyer_markets(markets_raw: str) -> list[tuple[str, str]]:
    """'Detroit, MI; Cleveland, OH' → [('detroit','mi'), ('cleveland','oh')]"""
    if not markets_raw:
        return []
    out = []
    for chunk in re.split(r"[;|]+", markets_raw):
        parts = [p.strip() for p in chunk.split(",")]
        if len(parts) >= 2 and parts[0] and parts[1]:
            out.append((parts[0].lower(), parts[1].lower()))
        elif len(parts) == 1 and parts[0]:
            # Just a state code?
            out.append(("", parts[0].lower()))
    return out


def _lead_matches_buyer(lead: dict, markets: list[tuple[str, str]]) -> bool:
    if not markets:
        return True  # "all markets" — no buyer-side filter
    lc = (lead.get("city") or "").lower()
    ls = (lead.get("state") or "").lower()
    for bc, bs in markets:
        if bc and bs:
            if bc in lc and bs == ls[:2]:
                return True
        elif bs:
            if bs == ls[:2]:
                return True
        elif bc:
            if bc in lc:
                return True
    return False


# ─────────────────────────── Lead scoring + filtering ───────────────────────────

DISTRESS_TAGS = {
    "foreclosure", "pre_foreclosure", "pre-foreclosure",
    "tax_delinquent", "tax-delinquent",
    "code_violations", "code-violations",
    "vacant", "vacant_abandoned",
    "probate", "inherited", "estate",
    "divorce", "bankruptcy",
}


def _lead_priority(lead: dict) -> int:
    """Higher = better. Distress tag is the biggest signal."""
    motivation = (lead.get("motivation") or "").lower()
    score = 0
    if any(tag in motivation for tag in DISTRESS_TAGS):
        score += 100
    if lead.get("seller_phone"):
        score += 30
    if lead.get("seller_email"):
        score += 10
    created = lead.get("created_at", "")
    if created and created[:7] >= "2026-05":
        score += 5
    return score


def _is_fresh(lead: dict) -> bool:
    """Within FRESH_WINDOW_DAYS of being added."""
    created = lead.get("created_at", "")
    if not created:
        return True  # missing date — include
    try:
        dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - dt).days
        return age <= FRESH_WINDOW_DAYS
    except Exception:
        return True


def select_leads_for_buyer(buyer: dict, all_leads: dict, *,
                            limit: int = MAX_LEADS_PER_DIGEST) -> list[dict]:
    markets = _parse_buyer_markets(buyer.get("markets", ""))
    pool = [
        l for l in all_leads.values()
        if _is_fresh(l) and _lead_matches_buyer(l, markets)
        # Skip already-assigned / cold / dead
        and l.get("status") not in ("assigned", "dead", "cold")
    ]
    pool.sort(key=lambda l: (-_lead_priority(l), l.get("created_at", "")))
    return pool[:limit]


# ─────────────────────────── Render ───────────────────────────

def _render_text(buyer: dict, leads: list[dict], *, is_trial: bool) -> str:
    name = (buyer.get("name") or "Investor").strip().split("—")[0].strip()[:60]
    markets = buyer.get("markets") or "your markets"
    today = datetime.now(timezone.utc).strftime("%B %d, %Y")
    distress_count = sum(1 for l in leads if any(t in (l.get("motivation") or "").lower()
                                                    for t in DISTRESS_TAGS))
    header = [
        f"Wholesale Omniverse — weekly drop  ({today})",
        f"For: {name}   ·   Markets: {markets}",
        "",
        f"This week: {len(leads)} leads ({distress_count} distress-tagged).",
        "",
        "─" * 64,
        "",
    ]
    body = []
    for i, l in enumerate(leads, 1):
        addr = l.get("address") or "(no address)"
        city = l.get("city") or ""
        st   = l.get("state") or ""
        motivation = (l.get("motivation") or "—")[:80]
        seller = l.get("seller_name") or "(no name)"
        phone  = l.get("seller_phone") or ""
        email  = l.get("seller_email") or ""
        body.append(f"{i:>2}. {addr}, {city}, {st}")
        body.append(f"    motivation: {motivation}")
        body.append(f"    seller: {seller}   phone: {phone or '—'}   email: {email or '—'}")
        body.append("")

    footer_trial = [
        "─" * 64,
        "",
        f"This was your FREE sample week. If these leads are useful,",
        f"keep them coming for ${SUBSCRIPTION_PRICE_USD:.0f}/mo: {_subscribe_url()}",
        "",
        "Reply STOP to opt out.  Reply YES to start your subscription.",
    ]
    footer_paid = [
        "─" * 64,
        "",
        f"You're an active subscriber — next drop next Monday.",
        "Reply with any feedback or to update your buy box.",
    ]
    footer = footer_trial if is_trial else footer_paid
    return "\n".join(header + body + footer)


def _render_html(buyer: dict, leads: list[dict], *, is_trial: bool) -> str:
    name = (buyer.get("name") or "Investor").strip().split("—")[0].strip()[:60]
    markets = buyer.get("markets") or "your markets"
    today = datetime.now(timezone.utc).strftime("%B %d, %Y")
    distress = sum(1 for l in leads if any(t in (l.get("motivation") or "").lower()
                                              for t in DISTRESS_TAGS))
    rows = []
    for i, l in enumerate(leads, 1):
        addr = l.get("address") or "(no address)"
        city = l.get("city") or ""
        st   = l.get("state") or ""
        motivation = (l.get("motivation") or "—")[:80]
        seller = l.get("seller_name") or "(no name)"
        phone  = l.get("seller_phone") or "—"
        email  = l.get("seller_email") or "—"
        rows.append(
            f'<tr><td style="padding:8px 10px;border-bottom:1px solid #2a2a2a;color:#cccccc;vertical-align:top;width:32px;">{i}</td>'
            f'<td style="padding:8px 10px;border-bottom:1px solid #2a2a2a;color:#cccccc;">'
            f'<strong>{addr}, {city}, {st}</strong><br>'
            f'<span style="color:#FDD023;">{motivation}</span><br>'
            f'<span style="color:#9a9a9a;">{seller} · phone: {phone} · email: {email}</span>'
            f'</td></tr>'
        )
    table = ('<table cellpadding="0" cellspacing="0" style="width:100%;border-collapse:collapse;'
             'margin:8px 0 24px 0;">' + "".join(rows) + "</table>")

    if is_trial:
        footer = (
            f'<p style="margin:0 0 10px;color:#cccccc;">'
            f'This was your <strong>FREE sample week</strong>. If these leads are useful,'
            f' keep them coming for <strong>${SUBSCRIPTION_PRICE_USD:.0f}/mo</strong>:</p>'
            f'<p style="margin:0 0 16px;"><a href="{_subscribe_url()}" '
            f'style="background:#FDD023;color:#0a0a0a;padding:10px 18px;text-decoration:none;'
            f'font-weight:bold;border-radius:4px;">Subscribe — ${SUBSCRIPTION_PRICE_USD:.0f}/mo</a></p>'
            f'<p style="margin:0;color:#9a9a9a;font-size:12px;">Reply STOP to opt out · Reply YES to start.</p>'
        )
    else:
        footer = (
            f'<p style="margin:0;color:#9a9a9a;">You\'re an active subscriber — next drop Monday. '
            f'Reply with any buy-box updates.</p>'
        )

    return (
        f'<p style="margin:0 0 4px;color:#cccccc;">Hi {name},</p>'
        f'<p style="margin:0 0 12px;color:#cccccc;">Wholesale Omniverse weekly drop · {today}</p>'
        f'<p style="margin:0 0 12px;color:#cccccc;"><strong>{len(leads)} leads</strong> '
        f'({distress} distress-tagged) for <strong>{markets}</strong>.</p>'
        + table + footer
    )


# ─────────────────────────── Send ───────────────────────────

def _log_delivery(buyer_id: str, lead_count: int, status: str, error: Optional[str] = None) -> None:
    rec = _load(DIGEST_LOG, [])
    if not isinstance(rec, list):
        rec = []
    rec.append({
        "ts": _now(), "buyer_id": buyer_id,
        "lead_count": lead_count, "status": status, "error": error,
    })
    _save(DIGEST_LOG, rec)


def send_digest_to(buyer_id: str, *, dry_run: bool = False) -> dict:
    buyers = _load(BUYERS_FILE, {})
    if buyer_id not in buyers:
        return {"status": "skipped", "reason": "buyer not found"}
    buyer = buyers[buyer_id]
    email = buyer.get("email")
    if not email:
        return {"status": "skipped", "reason": "no email"}

    sub_status = buyer.get("subscription_status")
    in_trial   = bool(buyer.get("trial_started_at") and not buyer.get("trial_converted_at")
                       and sub_status != "churned")
    is_active  = sub_status == "active"
    if not (is_active or in_trial):
        return {"status": "skipped", "reason": f"not eligible (state={sub_status or 'prospect'})"}

    all_leads = _load(LEADS_FILE, {})
    leads = select_leads_for_buyer(buyer, all_leads)
    if not leads:
        return {"status": "skipped", "reason": "no matching leads in pool"}

    subject = (f"[Wholesale Omniverse] Weekly drop — "
               f"{buyer.get('markets','your markets')} — {len(leads)} leads")
    body_text = _render_text(buyer, leads, is_trial=in_trial)
    body_html = _render_html(buyer, leads, is_trial=in_trial)

    if dry_run:
        # Persist a copy for inspection
        DIGESTS_DIR.mkdir(parents=True, exist_ok=True)
        (DIGESTS_DIR / f"{datetime.now(timezone.utc):%Y-%m-%d}-{buyer_id}.txt").write_text(body_text)
        return {"status": "dry_run", "buyer_id": buyer_id, "leads": len(leads),
                "is_trial": in_trial, "preview_path":
                str(DIGESTS_DIR / f"{datetime.now(timezone.utc):%Y-%m-%d}-{buyer_id}.txt")}

    result = send_branded_email(
        to_email=email, subject=subject,
        body_text=body_text, body_html_inner=body_html,
    )
    status = result.get("status")
    _log_delivery(buyer_id, len(leads), status, result.get("error"))
    if status == "sent":
        buyers[buyer_id]["last_digest_sent_at"] = _now()
        _save(BUYERS_FILE, buyers)
    return {"status": status, "buyer_id": buyer_id, "leads": len(leads),
            "is_trial": in_trial, "error": result.get("error")}


def run_weekly_digest(*, dry_run: bool = False) -> dict:
    """Send the digest to every eligible buyer (active + in-trial)."""
    buyers = _load(BUYERS_FILE, {})
    if not isinstance(buyers, dict):
        return {"error": "shape"}
    eligible = []
    for bid, b in buyers.items():
        sub = b.get("subscription_status")
        in_trial = bool(b.get("trial_started_at") and not b.get("trial_converted_at")
                         and sub != "churned")
        if sub == "active" or in_trial:
            eligible.append(bid)
    sent, skipped, failed = 0, 0, 0
    details = []
    for bid in eligible:
        r = send_digest_to(bid, dry_run=dry_run)
        s = r.get("status")
        details.append(r)
        if s in ("sent", "dry_run"):
            sent += 1
        elif s == "failed":
            failed += 1
        else:
            skipped += 1
    return {"eligible": len(eligible), "sent_or_dry": sent,
             "skipped": skipped, "failed": failed,
             "dry_run": dry_run, "details": details[:15]}


# ─────────────────────────── CLI ───────────────────────────

def main() -> int:
    import argparse, json as _json
    p = argparse.ArgumentParser(description="Weekly buyer digest")
    p.add_argument("--dry-run", action="store_true",
                    help="Render digests to data/bf_digests/ instead of emailing")
    p.add_argument("--buyer", default=None, help="Only this buyer_id")
    args = p.parse_args()

    if args.buyer:
        print(_json.dumps(send_digest_to(args.buyer, dry_run=args.dry_run), indent=2))
    else:
        print(_json.dumps(run_weekly_digest(dry_run=args.dry_run), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
