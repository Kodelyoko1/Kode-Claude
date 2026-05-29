#!/usr/bin/env python3
"""
Cash Blast — activate your existing buyer + seller lists for fastest revenue.
Zero new API setup. Uses your already-configured SMTP + PayPal.me.

MODES:
  --mode buyer-pitch    Email all cash buyers offering Priority Deal Access ($97/mo)
  --mode seller-recall  Re-engage cold seller leads with a "still want a cash offer?"
  --mode lead-resale    Sell individual hot leads to buyers at $47/each via PayPal.me

SAFEGUARDS:
  --dry-run             Preview what would send. Default if no other flag given.
  --send                Actually send. Required for live blast.
  --limit N             Send to first N recipients only (good for slow rollout)
  --no-suppress         Disable auto-suppression of addresses emailed in last 7 days

Examples:
  python3 run_cash_blast.py --mode buyer-pitch --dry-run
  python3 run_cash_blast.py --mode buyer-pitch --send --limit 20
  python3 run_cash_blast.py --mode seller-recall --send
"""
import argparse
import datetime
import json
import os
import sys
import time
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from email_template import send_branded_email

console = Console()
DATA_DIR  = Path(__file__).parent / "data"
BUYERS    = DATA_DIR / "cash_buyers.json"
LEADS     = DATA_DIR / "leads.json"
EMAIL_LOG = DATA_DIR / "email_log.json"
BLAST_LOG = DATA_DIR / "cash_blast_log.json"

PAYPAL_ME = f"https://paypal.me/{os.environ.get('PAYPAL_ME_USERNAME', 'wholesaleomniverse')}"
PRIORITY_PRICE = 97
LEAD_PRICE     = 47


def _load(path: Path, default):
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return default


def _save(path: Path, data):
    DATA_DIR.mkdir(exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def _now():
    return datetime.datetime.now().isoformat()


def _emails_sent_recently(within_days: int = 7, mode: str = "") -> set:
    """
    Return addresses already pitched THIS SAME blast mode within N days.
    Intentionally does NOT scan email_log — the follow-up agent and other
    engines send different content; suppression only prevents duplicate
    sends of the same offer.
    """
    cutoff = datetime.datetime.now() - datetime.timedelta(days=within_days)
    out = set()
    blast = _load(BLAST_LOG, [])
    for entry in blast:
        if mode and entry.get("mode") != mode:
            continue
        ts = entry.get("sent_at", "")
        try:
            if datetime.datetime.fromisoformat(ts[:19]) >= cutoff:
                out.add((entry.get("email") or "").lower())
        except Exception:
            pass
    return out


def _log_send(mode: str, recipient: str, subject: str, success: bool, error: str = ""):
    log = _load(BLAST_LOG, [])
    log.append({
        "mode": mode, "email": recipient, "subject": subject,
        "success": success, "error": error, "sent_at": _now(),
    })
    _save(BLAST_LOG, log)


# ── BUYER PITCH ──────────────────────────────────────────────────────────────
def _buyer_pitch(buyer: dict) -> dict:
    """
    Pitch: weekly motivated-seller property lists delivered to your inbox.
    Buyer pays $97/mo, gets curated address lists + owner contacts + ARV/repair
    estimates and makes offers directly to sellers. We source the leads, they
    do the deals.
    """
    name      = buyer.get("name") or "investor"
    first     = name.split()[0]
    markets   = buyer.get("markets") or buyer.get("buy_box") or "your target markets"
    primary_market = markets.split(",")[0].strip()
    buy_box   = buyer.get("buy_box") or "your buy box"
    pay_url   = f"{PAYPAL_ME}/{PRIORITY_PRICE}"

    subject = f"{first}, fresh motivated-seller list for {primary_market} — ${PRIORITY_PRICE}/mo"
    text = (
        f"Hi {first},\n\n"
        f"Tyreese from Wholesale Omniverse. You're on my buyers list as active in "
        f"{markets}.\n\n"
        f"Here's what I'm offering:\n\n"
        f"Every week I drop a curated property list to subscribers — distressed, "
        f"pre-foreclosure, probate, tax delinquent, absentee owner, and other off-market "
        f"motivated sellers in your target markets. Each entry includes:\n\n"
        f"  - Property address + ZIP\n"
        f"  - Owner name + phone (when available)\n"
        f"  - ARV estimate based on current comps\n"
        f"  - Repair estimate by condition tier\n"
        f"  - Distress signal (why they're motivated)\n\n"
        f"You take the list and make offers directly. I source the leads, you close "
        f"the deals. No assignment fee owed to me — just the monthly subscription.\n\n"
        f"${PRIORITY_PRICE}/month flat. Cancel anytime, no contract.\n\n"
        f"Your buy box on file: {buy_box}\n\n"
        f"If you want in:\n"
        f"  1. Pay ${PRIORITY_PRICE}: {pay_url}\n"
        f"  2. Reply with your buy box so I send only properties that match.\n\n"
        f"First list drops within 7 days of payment.\n\n"
        f"Reply 'NO' and I'll stop pitching.\n\n"
        f"— Tyreese Lumiere, Wholesale Omniverse LLC\n"
        f"207-385-4041"
    )
    html = (
        f"Hi <strong>{first}</strong>,<br><br>"
        f"Tyreese from Wholesale Omniverse. You're on my buyers list as active in "
        f"<strong>{markets}</strong>.<br><br>"
        f"<strong>Here's what I'm offering:</strong><br><br>"
        f"Every week I drop a curated property list to subscribers — distressed, "
        f"pre-foreclosure, probate, tax delinquent, absentee owner, and other "
        f"off-market motivated sellers in your target markets. Each entry includes:"
        f"<ul style=\"margin:8px 0 16px 0;padding-left:22px;\">"
        f"<li>Property address + ZIP</li>"
        f"<li>Owner name + phone (when available)</li>"
        f"<li>ARV estimate based on current comps</li>"
        f"<li>Repair estimate by condition tier</li>"
        f"<li>Distress signal (why they're motivated)</li>"
        f"</ul>"
        f"You take the list and <strong>make offers directly</strong>. I source the leads, "
        f"you close the deals. No assignment fee owed to me — just the flat monthly subscription.<br><br>"
        f"<strong>${PRIORITY_PRICE}/month.</strong> Cancel anytime, no contract.<br><br>"
        f"Your buy box on file: <em>{buy_box}</em><br><br>"
        f"If you want in:<br>"
        f"&nbsp;&nbsp;1. Pay <strong>${PRIORITY_PRICE}</strong>: "
        f'<a href="{pay_url}" style="display:inline-block;padding:10px 22px;background:#f59e0b;'
        f'color:#0f172a;font-weight:700;text-decoration:none;border-radius:6px;'
        f'margin:6px 0;">Subscribe ${PRIORITY_PRICE}</a><br>'
        f"&nbsp;&nbsp;2. Reply with your buy box so I send only properties that match.<br><br>"
        f"<em>First list drops within 7 days of payment.</em><br><br>"
        f"Reply <strong>NO</strong> and I'll stop pitching."
    )
    return {"subject": subject, "text": text, "html": html, "email": buyer.get("email", "")}


# ── SELLER RECALL ────────────────────────────────────────────────────────────
def _seller_recall(lead: dict) -> dict:
    name    = lead.get("seller_name") or "there"
    first   = name.split()[0]
    address = lead.get("address") or "your property"
    city    = lead.get("city") or ""
    state   = lead.get("state") or ""

    subject = f"Still need to sell {address}? List free, talk to cash buyers."
    text = (
        f"Hi {first},\n\n"
        f"Tyreese with Wholesale Omniverse. A while back I reached out about "
        f"{address}{', ' + city if city else ''}{', ' + state if state else ''}. "
        f"Wanted to quickly check in — is the property still available?\n\n"
        f"If yes, here's how I can help: I run a free seller marketplace. We add your "
        f"property to a weekly list sent to 100+ active cash buyers in your area. "
        f"They contact you directly with offers. There's no charge to sellers, ever — "
        f"we make money from investor subscriptions, not seller fees.\n\n"
        f"Most listed properties get 2-5 buyer inquiries within the first week.\n\n"
        f"Reply 'YES' and I'll add you to Monday's list. Or reply 'NO' and I won't "
        f"follow up again.\n\n"
        f"Thanks,\n"
        f"Tyreese Lumiere\n"
        f"Wholesale Omniverse LLC\n"
        f"207-385-4041"
    )
    html = (
        f"Hi <strong>{first}</strong>,<br><br>"
        f"Tyreese with Wholesale Omniverse. A while back I reached out about "
        f"<strong>{address}</strong>{', ' + city if city else ''}{', ' + state if state else ''}. "
        f"Wanted to quickly check in — is the property still available?<br><br>"
        f"If yes, here's how I can help: I run a <strong>free seller marketplace</strong>. "
        f"We add your property to a weekly list sent to <strong>100+ active cash buyers</strong> "
        f"in your area. They contact you directly with offers. <strong>Zero fees to sellers, ever</strong> — "
        f"we make money from investor subscriptions.<br><br>"
        f"Most listed properties get 2-5 buyer inquiries within the first week.<br><br>"
        f"Reply <strong>YES</strong> and I'll add you to Monday's list. Or reply "
        f"<strong>NO</strong> and I won't follow up again."
    )
    return {"subject": subject, "text": text, "html": html, "email": lead.get("seller_email", "")}


# ── LEAD RESALE ──────────────────────────────────────────────────────────────
def _lead_resale_to_buyer(buyer: dict, leads: list) -> dict:
    name  = buyer.get("name") or "investor"
    first = name.split()[0]
    pay   = f"{PAYPAL_ME}/{LEAD_PRICE}"

    rows_text = []
    rows_html = []
    for L in leads[:5]:
        snippet_text = (
            f"  • {L.get('city','?')}, {L.get('state','?')}  — "
            f"ARV ~${L.get('estimated_arv',0):,}  "
            f"Asking ~${L.get('asking_price',0):,}  "
            f"Motivation: {L.get('motivation', '—')[:60]}"
        )
        rows_text.append(snippet_text)
        rows_html.append(
            f"<li><strong>{L.get('city','?')}, {L.get('state','?')}</strong> — "
            f"ARV ~${L.get('estimated_arv',0):,}, Asking ~${L.get('asking_price',0):,}<br>"
            f"<em>Motivation:</em> {L.get('motivation','—')[:80]}</li>"
        )

    subject = f"{len(leads)} fresh leads — ${LEAD_PRICE}/each, first come first served"
    text = (
        f"Hi {first},\n\n"
        f"Tyreese with Wholesale Omniverse. I just dropped these motivated seller leads "
        f"into my pipeline — none of them have been shopped to buyers yet:\n\n"
        + "\n".join(rows_text) +
        f"\n\nEach lead is ${LEAD_PRICE} (full contact + address + repair estimate). "
        f"PayPal: {pay}\n\n"
        f"Reply with which one(s) you want. First in gets it.\n\n"
        f"— Tyreese"
    )
    html = (
        f"Hi <strong>{first}</strong>,<br><br>"
        f"Tyreese with Wholesale Omniverse. I just dropped these motivated seller leads "
        f"into my pipeline — none of them have been shopped to buyers yet:<br>"
        f"<ul>{''.join(rows_html)}</ul>"
        f"Each lead is <strong>${LEAD_PRICE}</strong> (full contact + address + repair estimate).<br>"
        f'<a href="{pay}" style="display:inline-block;padding:10px 22px;background:#f59e0b;'
        f'color:#0f172a;font-weight:700;text-decoration:none;border-radius:6px;'
        f'margin:8px 0;">Pay ${LEAD_PRICE}</a><br>'
        f"Reply with which one(s) you want — first in gets it."
    )
    return {"subject": subject, "text": text, "html": html, "email": buyer.get("email", "")}


# ── DISPATCH ─────────────────────────────────────────────────────────────────
def run_blast(mode: str, dry_run: bool, limit: int, suppress: bool):
    if mode == "buyer-pitch":
        buyers = list(_load(BUYERS, {}).values())
        # Skip known bounces
        buyers = [b for b in buyers if b.get("email") and not b.get("email_bounced")]
        targets = [(b, _buyer_pitch(b)) for b in buyers]
    elif mode == "seller-recall":
        leads = list(_load(LEADS, {}).values())
        leads = [L for L in leads if L.get("seller_email") and
                 not L.get("email_bounced") and
                 L.get("status") not in ("closed", "assigned", "dead")]
        targets = [(L, _seller_recall(L)) for L in leads]
    elif mode == "lead-resale":
        buyers = list(_load(BUYERS, {}).values())
        leads_all = list(_load(LEADS, {}).values())
        # Pick top 5 unsold / open leads by motivation freshness
        hot = [L for L in leads_all
               if L.get("status") in ("new", "contacted", "warm")][:5]
        if not hot:
            console.print("[yellow]No hot leads available for resale.[/yellow]")
            return
        targets = [(b, _lead_resale_to_buyer(b, hot)) for b in buyers if b.get("email")]
    else:
        console.print(f"[red]Unknown mode: {mode}[/red]"); return

    # Suppress: skip buyers who got THIS SAME pitch in the last 7 days
    suppressed = _emails_sent_recently(7, mode=mode) if suppress else set()
    targets = [t for t in targets if t[1]["email"].lower() not in suppressed]

    if limit > 0:
        targets = targets[:limit]

    console.print(Panel(
        Text.from_markup(
            f"[bold]Cash Blast — {mode}[/bold]\n"
            f"  Recipients:  [white]{len(targets)}[/white]\n"
            f"  Suppressed:  [white]{len(suppressed)}[/white]  (auto-skipped, emailed in last 7d)\n"
            f"  Mode:        {'[yellow]DRY RUN[/yellow]' if dry_run else '[red]LIVE — actually sending[/red]'}\n"
        ),
        border_style="blue",
        title="[bold blue]Wholesale Omniverse — Cash Blast[/bold blue]",
    ))

    if not targets:
        console.print("[dim]Nothing to send.[/dim]"); return

    if dry_run:
        # Show first 3 previews
        for entity, msg in targets[:3]:
            console.print(f"\n[cyan]──── {msg['email']} ────[/cyan]")
            console.print(f"[bold]Subject:[/bold] {msg['subject']}")
            console.print(msg["text"][:500] + ("..." if len(msg["text"]) > 500 else ""))
        console.print(f"\n[dim]...and {len(targets) - 3} more if {len(targets) > 3} > 0[/dim]")
        console.print(f"\n[yellow]Run with --send to actually fire.[/yellow]")
        return

    # LIVE SEND
    sent, failed = 0, 0
    for i, (entity, msg) in enumerate(targets, 1):
        result = send_branded_email(
            to_email=msg["email"],
            subject=msg["subject"],
            body_text=msg["text"],
            body_html_inner=msg["html"],
        )
        ok = result.get("status") == "sent"
        _log_send(mode, msg["email"], msg["subject"], ok, result.get("error", ""))
        if ok:
            sent += 1
            console.print(f"  [green]✓[/green] {i:>3}/{len(targets)}  {msg['email']}")
        else:
            failed += 1
            console.print(f"  [red]✗[/red] {i:>3}/{len(targets)}  {msg['email']}  {result.get('error', '')[:60]}")
        time.sleep(1.5)  # be polite to Gmail rate limits

    console.print(Panel(
        Text.from_markup(
            f"[bold green]Blast complete[/bold green]\n\n"
            f"  Sent:    [green]{sent}[/green]\n"
            f"  Failed:  [red]{failed}[/red]\n\n"
            f"  Replies will land in your Gmail. Watch for 'YES', 'TRIAL', or buy-box info.\n"
            f"  Track activations: [bold]python3 onboard_client.py --list[/bold]"
        ),
        border_style="green",
    ))


def main():
    parser = argparse.ArgumentParser(description="Cash Blast — activate existing lists for fastest revenue")
    parser.add_argument("--mode", choices=["buyer-pitch", "seller-recall", "lead-resale"],
                        required=True, help="Which blast to run")
    parser.add_argument("--dry-run", action="store_true", help="Preview only (default)")
    parser.add_argument("--send",    action="store_true", help="Actually send the blast")
    parser.add_argument("--limit",   type=int, default=0, help="Send to first N only (0 = all)")
    parser.add_argument("--no-suppress", action="store_true", help="Don't auto-skip recently emailed")
    args = parser.parse_args()

    # Default to dry-run unless --send is explicit
    dry_run = not args.send or args.dry_run
    run_blast(args.mode, dry_run, args.limit, suppress=not args.no_suppress)


if __name__ == "__main__":
    main()
