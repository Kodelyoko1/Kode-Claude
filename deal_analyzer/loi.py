"""
Letter of Intent generator — turns a lead with deal math into a sendable LOI.

When a hot seller replies, the owner needs to put a number in front of them
inside the same day. This module:
  1. Pulls the lead's analyze_deal() math (ARV / repairs / MAO).
  2. Picks an offer figure: MAO by default, or owner-provided override.
  3. Renders a 14-day-close LOI text + branded HTML.
  4. Optionally emails it to the seller if seller_email is on file; otherwise
     writes it to data/da_lois/<lead_id>-<ts>.txt for the owner to copy
     into a phone call or SMS.

The LOI is plain-English, non-binding, 14-day close. It anchors the negotiation
on a real number rather than another "I'd love to chat" reply that goes nowhere.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

DATA_DIR    = Path(__file__).parent.parent / "data"
LEADS_FILE  = DATA_DIR / "leads.json"
LOIS_DIR    = DATA_DIR / "da_lois"
LOI_LOG     = DATA_DIR / "da_loi_log.json"

sys.path.insert(0, str(Path(__file__).parent.parent))
from tools import analyze_deal as _analyze_deal

DEFAULT_ASSIGNMENT_FEE = float(os.environ.get("DA_DEFAULT_ASSIGNMENT_FEE", "10000"))
CLOSE_DAYS             = int(os.environ.get("DA_CLOSE_DAYS", "14"))
COMPANY_NAME           = "Wholesale Omniverse LLC"
SENDER_NAME            = "Tyreese Lumiere"
SENDER_PHONE           = "207-385-4041"


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
    import tempfile
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


# ─────────────────────────── Render ───────────────────────────

def _offer_figure(lead: dict, *, override: Optional[float] = None,
                   assignment_fee: float = DEFAULT_ASSIGNMENT_FEE) -> float:
    """The number we put in the LOI. Owner override wins; else compute MAO."""
    if override is not None and override > 0:
        return float(override)
    arv     = float(lead.get("estimated_arv") or 0)
    repairs = float(lead.get("estimated_repairs") or 0)
    return max(0.0, round((arv * 0.70) - repairs - assignment_fee, 2))


LOI_TEXT_TEMPLATE = """Dear {seller_name},

Thank you for taking the time to speak with me about your property at {address}, {city}, {state}.

Based on our conversation and what I'm seeing in the {city} market, I'd like to put a written offer on the table so we can move forward quickly if it works for you.

PROPERTY:  {address}, {city}, {state}
OFFER:     ${offer:,.0f}  (cash, all-in)
CLOSE:     {close_days} days from acceptance
CONDITION: As-is — no repairs, no inspections required, no contingencies on financing
COSTS:     I pay all standard closing costs and title fees

To be clear about how I work:
  - I'm a real estate buyer, not an agent. No commissions.
  - The offer is non-binding until we both sign a purchase agreement.
  - We can close as fast as title can clear — usually {close_days} days, often faster.
  - You don't need to clean out the property. Leave anything you don't want.

If this offer doesn't work, tell me your number — I can usually meet sellers somewhere in between if there's a real reason.

If it does work, reply "YES" or call {phone} and we'll get the contract over to you today.

Either way, I appreciate you considering it.

Thanks,
{sender_name}
{sender_email}
{phone}
{company}

(Offer valid for 7 days. After that, market conditions may have moved.)
"""


LOI_HTML_TEMPLATE = """\
<p style="margin:0 0 14px;color:#cccccc;">Dear {seller_name},</p>
<p style="margin:0 0 14px;color:#cccccc;">
  Thank you for taking the time to speak with me about your property at
  <strong>{address}, {city}, {state}</strong>.
</p>
<p style="margin:0 0 14px;color:#cccccc;">
  Based on our conversation and what I'm seeing in the {city} market, I'd like to put a written
  offer on the table so we can move forward quickly if it works for you.
</p>
<table cellpadding="6" cellspacing="0"
       style="margin:8px 0 16px;border-collapse:collapse;background:#1a1a1a;color:#cccccc;">
  <tr><td style="padding:6px 12px;color:#9a9a9a;">PROPERTY</td>
      <td style="padding:6px 12px;"><strong>{address}, {city}, {state}</strong></td></tr>
  <tr><td style="padding:6px 12px;color:#9a9a9a;">OFFER</td>
      <td style="padding:6px 12px;"><strong style="color:#FDD023;">${offer:,.0f}</strong> (cash, all-in)</td></tr>
  <tr><td style="padding:6px 12px;color:#9a9a9a;">CLOSE</td>
      <td style="padding:6px 12px;">{close_days} days from acceptance</td></tr>
  <tr><td style="padding:6px 12px;color:#9a9a9a;">CONDITION</td>
      <td style="padding:6px 12px;">As-is — no repairs, no inspections, no financing contingency</td></tr>
  <tr><td style="padding:6px 12px;color:#9a9a9a;">COSTS</td>
      <td style="padding:6px 12px;">I pay all standard closing costs and title fees</td></tr>
</table>
<p style="margin:0 0 8px;color:#cccccc;">To be clear about how I work:</p>
<table cellpadding="0" cellspacing="0" style="margin:0 0 14px;">
  <tr><td style="padding:3px 0;color:#cccccc;"><span style="color:#FDD023;font-weight:bold;">&#10003;</span>&nbsp; Real estate buyer, not an agent. No commissions.</td></tr>
  <tr><td style="padding:3px 0;color:#cccccc;"><span style="color:#FDD023;font-weight:bold;">&#10003;</span>&nbsp; Offer is non-binding until we both sign a purchase agreement.</td></tr>
  <tr><td style="padding:3px 0;color:#cccccc;"><span style="color:#FDD023;font-weight:bold;">&#10003;</span>&nbsp; Close in {close_days} days, often faster.</td></tr>
  <tr><td style="padding:3px 0;color:#cccccc;"><span style="color:#FDD023;font-weight:bold;">&#10003;</span>&nbsp; You don't need to clean out the property.</td></tr>
</table>
<p style="margin:0 0 14px;color:#cccccc;">
  If this offer doesn't work, tell me your number — I can usually meet sellers somewhere
  in between if there's a real reason.
</p>
<p style="margin:0 0 18px;color:#cccccc;">
  If it does work, <strong>reply "YES"</strong> or call <strong>{phone}</strong> and we'll get the contract
  over to you today.
</p>
<p style="margin:0;color:#cccccc;">Thanks,<br>{sender_name}</p>
<p style="margin:14px 0 0;color:#9a9a9a;font-size:11px;">
  Offer valid for 7 days. After that, market conditions may have moved.
</p>"""


def generate_loi(lead_id: str, *, offer_override: Optional[float] = None,
                  assignment_fee: float = DEFAULT_ASSIGNMENT_FEE,
                  close_days: int = CLOSE_DAYS) -> dict:
    """Build the LOI bundle for a lead. Doesn't send — that's a separate step."""
    leads = _load(LEADS_FILE, {})
    if lead_id not in leads:
        return {"error": f"lead {lead_id} not found"}
    lead = leads[lead_id]
    offer = _offer_figure(lead, override=offer_override, assignment_fee=assignment_fee)
    if offer <= 0:
        return {"error": "could not compute offer (need estimated_arv or owner override)"}

    seller = (lead.get("seller_name") or "Homeowner").strip()
    smtp_user = os.environ.get("SMTP_USER", "info@wholesaleomniverse.com")
    ctx = dict(
        seller_name=seller,
        address=lead.get("address") or "(your property)",
        city=lead.get("city") or "",
        state=lead.get("state") or "",
        offer=offer,
        close_days=close_days,
        sender_name=SENDER_NAME,
        sender_email=smtp_user,
        phone=SENDER_PHONE,
        company=COMPANY_NAME,
    )
    body_text = LOI_TEXT_TEMPLATE.format(**ctx)
    body_html = LOI_HTML_TEMPLATE.format(**ctx)
    return {
        "lead_id":      lead_id,
        "offer":        offer,
        "close_days":   close_days,
        "assignment_fee": assignment_fee,
        "seller_email": lead.get("seller_email", ""),
        "seller_phone": lead.get("seller_phone", ""),
        "body_text":    body_text,
        "body_html":    body_html,
    }


def _log(event: str, lead_id: str, **extra) -> None:
    rec = _load(LOI_LOG, [])
    if not isinstance(rec, list):
        rec = []
    rec.append({"ts": _now(), "event": event, "lead_id": lead_id, **extra})
    _save(LOI_LOG, rec)


def send_loi(lead_id: str, *, offer_override: Optional[float] = None,
              assignment_fee: float = DEFAULT_ASSIGNMENT_FEE,
              close_days: int = CLOSE_DAYS, dry_run: bool = False) -> dict:
    """Generate + (optionally) email. If no seller email, write to disk for owner."""
    loi = generate_loi(lead_id, offer_override=offer_override,
                        assignment_fee=assignment_fee, close_days=close_days)
    if "error" in loi:
        return loi

    LOIS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
    txt_path = LOIS_DIR / f"{lead_id}-{ts}.txt"
    txt_path.write_text(loi["body_text"])
    loi["disk_path"] = str(txt_path)

    if not loi["seller_email"]:
        _log("written_no_email", lead_id, offer=loi["offer"], path=str(txt_path))
        return {**loi, "sent": False, "reason": "no seller_email; LOI written to disk for manual send"}

    if dry_run:
        _log("dry_run", lead_id, offer=loi["offer"])
        return {**loi, "sent": False, "reason": "dry_run"}

    from email_template import send_branded_email
    subject = f"Cash Offer — {loi.get('address','')} — ${loi['offer']:,.0f}"
    # Override subject with the real address
    leads = _load(LEADS_FILE, {})
    addr = leads.get(lead_id, {}).get("address", "")
    if addr:
        subject = f"Cash Offer — {addr} — ${loi['offer']:,.0f}"
    r = send_branded_email(
        to_email=loi["seller_email"], subject=subject,
        body_text=loi["body_text"], body_html_inner=loi["body_html"],
    )
    sent = r.get("status") == "sent"
    if sent:
        # Mark the lead so we don't double-LOI
        leads[lead_id]["loi_sent_at"] = _now()
        leads[lead_id]["loi_offer_usd"] = loi["offer"]
        leads[lead_id]["updated_at"]   = _now()
        if leads[lead_id].get("status") not in ("under_contract", "assigned"):
            leads[lead_id]["status"] = "negotiating"
        _save(LEADS_FILE, leads)
        _log("sent", lead_id, offer=loi["offer"], to=loi["seller_email"])
    else:
        _log("send_failed", lead_id, offer=loi["offer"], error=r.get("error"))
    return {**loi, "sent": sent, "status": r.get("status"), "error": r.get("error")}


# ─────────────────────────── CLI ───────────────────────────

def main() -> int:
    import argparse
    p = argparse.ArgumentParser(description="Generate + send a Letter of Intent for a lead")
    p.add_argument("lead_id", help="The lead to LOI (e.g. LEAD-0007)")
    p.add_argument("--offer", type=float, default=None,
                    help="Override the computed MAO offer (USD)")
    p.add_argument("--assignment-fee", type=float, default=DEFAULT_ASSIGNMENT_FEE,
                    help=f"Assignment fee used in MAO math (default {DEFAULT_ASSIGNMENT_FEE:.0f})")
    p.add_argument("--close-days", type=int, default=CLOSE_DAYS,
                    help=f"Days to close in the offer (default {CLOSE_DAYS})")
    p.add_argument("--preview", action="store_true",
                    help="Just print the rendered LOI text, don't write or send")
    p.add_argument("--dry-run", action="store_true",
                    help="Write LOI to disk but don't email even if seller_email is set")
    args = p.parse_args()

    if args.preview:
        loi = generate_loi(args.lead_id, offer_override=args.offer,
                            assignment_fee=args.assignment_fee, close_days=args.close_days)
        if "error" in loi:
            print(json.dumps(loi, indent=2)); return 1
        print(loi["body_text"]); return 0

    result = send_loi(args.lead_id, offer_override=args.offer,
                       assignment_fee=args.assignment_fee, close_days=args.close_days,
                       dry_run=args.dry_run)
    print(json.dumps({k: v for k, v in result.items() if k not in ("body_text","body_html")},
                       indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
