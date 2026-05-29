#!/usr/bin/env python3
"""
IMAP bounce auto-handler — watches Gmail for delivery-failure notifications,
extracts the bounced recipient address, and flags it across data files so
future blasts skip it.

Marks contacts as:
  email_bounced:    True
  bounced_at:       ISO timestamp
  bounce_count:     int  (so we can decide when to fully retire an address)

Affected files: data/cash_buyers.json, data/leads.json, data/prospects.json

Uses the existing SMTP_USER / SMTP_PASS env vars — same Gmail app password
that already works for sending.

Usage:
  python3 process_bounces.py               # process new bounces, archive them
  python3 process_bounces.py --dry-run     # report only, don't modify files
  python3 process_bounces.py --no-archive  # keep bounces in inbox for review
  python3 process_bounces.py --since 7     # look back N days (default 7)
"""
import argparse
import datetime
import email
import imaplib
import json
import os
import re
import sys
from email.utils import parseaddr
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

console = Console()
DATA_DIR        = Path(__file__).parent / "data"
BUYERS_FILE     = DATA_DIR / "cash_buyers.json"
LEADS_FILE      = DATA_DIR / "leads.json"
PROSPECTS_FILE  = DATA_DIR / "prospects.json"
SEEN_BOUNCES    = DATA_DIR / "bounces_seen_uids.json"
BOUNCE_LOG      = DATA_DIR / "bounce_log.json"

IMAP_HOST    = "imap.gmail.com"
IMAP_PORT    = 993
ARCHIVE_LABEL = "wholesale-bounces"

# Bounce notification senders we recognize
BOUNCE_FROM_PATTERNS = [
    r"mailer-daemon@",
    r"postmaster@",
    r"mail delivery subsystem",
    r"mail delivery system",
    r"system administrator",
]

BOUNCE_SUBJECT_PATTERNS = [
    r"delivery status notification.*failure",
    r"delivery has failed",
    r"undelivered mail returned",
    r"returned mail",
    r"mail delivery failed",
    r"address not found",
    r"undeliverable",
    r"failure notice",
]


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


def _connect():
    user = os.environ.get("SMTP_USER", "")
    pwd  = os.environ.get("SMTP_PASS", "")
    if not user or not pwd:
        raise RuntimeError("SMTP_USER / SMTP_PASS missing from .env")
    m = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    m.login(user, pwd)
    return m


def _looks_like_bounce(from_hdr: str, subject: str) -> bool:
    f = (from_hdr or "").lower()
    s = (subject or "").lower()
    if any(re.search(p, f) for p in BOUNCE_FROM_PATTERNS):
        return True
    if any(re.search(p, s) for p in BOUNCE_SUBJECT_PATTERNS):
        return True
    return False


def _extract_bounced_address(raw_email: bytes) -> str:
    """
    Pull the bounced recipient from a DSN. Strategy in order:
      1. Look for 'Final-Recipient:' header in the message/delivery-status part
      2. Look for 'X-Failed-Recipients:' header on the outer message
      3. Body text scan for 'failed:' or 'no such user' followed by an address
    """
    msg = email.message_from_bytes(raw_email)

    failed_hdr = msg.get("X-Failed-Recipients", "")
    if failed_hdr:
        _, addr = parseaddr(failed_hdr)
        if "@" in addr:
            return addr.strip().lower()

    for part in msg.walk():
        ct = (part.get_content_type() or "").lower()
        if ct == "message/delivery-status" or "delivery-status" in ct:
            try:
                payload = part.get_payload(decode=True)
                if not payload:
                    continue
                text = payload.decode("utf-8", errors="ignore")
            except Exception:
                continue
            m = re.search(r"Final-Recipient:\s*rfc822;\s*([^\s<>]+@[^\s<>]+)", text, re.I)
            if m:
                return m.group(1).strip().lower()
            m = re.search(r"Original-Recipient:\s*rfc822;\s*([^\s<>]+@[^\s<>]+)", text, re.I)
            if m:
                return m.group(1).strip().lower()

    body_text = ""
    for part in msg.walk():
        if part.get_content_type() == "text/plain":
            try:
                p = part.get_payload(decode=True)
                if p:
                    body_text += p.decode("utf-8", errors="ignore") + "\n"
            except Exception:
                pass

    # Patterns like "<addr@domain> ... 550 No such user" or "failed: <addr>"
    candidates = re.findall(r"[\w.+-]+@[\w-]+\.[a-zA-Z]{2,}", body_text)
    skip = {"mailer-daemon", "postmaster", "wholesaleomniverse@gmail.com",
            "noreply", "no-reply"}
    for c in candidates:
        cl = c.lower()
        if any(s in cl for s in skip):
            continue
        return cl
    return ""


def _flag_contact(address: str, dry_run: bool) -> dict:
    """Mark email_bounced=True on any matching contact across all data files."""
    address_lc = address.lower()
    hits = []
    for path, kind in [(BUYERS_FILE, "buyer"),
                       (LEADS_FILE,  "lead"),
                       (PROSPECTS_FILE, "prospect")]:
        if not path.exists():
            continue
        data = _load(path, {})
        for rec_id, rec in data.items():
            rec_email = (rec.get("email") or rec.get("seller_email") or "").lower()
            if rec_email != address_lc:
                continue
            if not dry_run:
                rec["email_bounced"] = True
                rec["bounced_at"]    = _now()
                rec["bounce_count"]  = rec.get("bounce_count", 0) + 1
            hits.append({"file": path.name, "kind": kind, "id": rec_id,
                         "name": rec.get("name") or rec.get("seller_name", "")})
        if hits and not dry_run:
            _save(path, data)
    return {"address": address_lc, "matched_records": hits}


def _ensure_label(imap, label):
    """Create the bounces label if it doesn't exist (Gmail folders are labels)."""
    try:
        imap.create(label)
    except Exception:
        pass


def run(dry_run: bool, archive: bool, since_days: int) -> dict:
    console.print(Panel(
        Text.from_markup(
            f"[bold]Bounce Auto-Handler[/bold]\n"
            f"  Looking back: {since_days} day(s)\n"
            f"  Mode: {'[yellow]DRY RUN[/yellow]' if dry_run else '[green]LIVE[/green]'}\n"
            f"  Archive bounces after processing: {archive}"
        ),
        border_style="blue",
    ))

    try:
        m = _connect()
    except Exception as e:
        console.print(f"[red]IMAP login failed: {e}[/red]")
        sys.exit(1)

    try:
        m.select("INBOX")
        since_date = (datetime.datetime.now() - datetime.timedelta(days=since_days)
                      ).strftime("%d-%b-%Y")
        # Broad search — we'll filter precisely after fetching headers
        typ, raw = m.search(None, f'(SINCE "{since_date}")')
        if typ != "OK":
            console.print(f"[red]IMAP search failed: {raw}[/red]")
            return {"processed": 0, "flagged": 0}

        uids = raw[0].split() if raw and raw[0] else []
        console.print(f"  Inbox messages in window: [white]{len(uids)}[/white]")

        seen = set(_load(SEEN_BOUNCES, []))
        flagged = []
        processed = 0

        for uid in uids:
            uid_str = uid.decode() if isinstance(uid, bytes) else str(uid)
            if uid_str in seen:
                continue

            # Cheap header pull first
            typ, h = m.fetch(uid, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT)])")
            if typ != "OK" or not h or not h[0]:
                continue
            hdrs = h[0][1].decode("utf-8", errors="ignore") if isinstance(h[0], tuple) else ""
            from_hdr  = re.search(r"From:\s*(.+)", hdrs, re.I)
            subj_hdr  = re.search(r"Subject:\s*(.+)", hdrs, re.I)
            from_val  = from_hdr.group(1).strip() if from_hdr else ""
            subj_val  = subj_hdr.group(1).strip() if subj_hdr else ""

            if not _looks_like_bounce(from_val, subj_val):
                continue

            # Real fetch
            typ, full = m.fetch(uid, "(RFC822)")
            if typ != "OK" or not full or not full[0]:
                continue
            raw_msg = full[0][1] if isinstance(full[0], tuple) else b""
            addr = _extract_bounced_address(raw_msg)
            if not addr:
                console.print(f"  [yellow]· {uid_str}  could not parse bounced address[/yellow]")
                continue

            result = _flag_contact(addr, dry_run)
            matched_n = len(result["matched_records"])
            flagged.append({"uid": uid_str, "address": addr,
                            "matched": matched_n, "at": _now()})
            processed += 1
            console.print(f"  [green]✓[/green] {addr}  →  {matched_n} record(s) flagged")

            if not dry_run:
                seen.add(uid_str)
                _save(SEEN_BOUNCES, sorted(list(seen)))
                if archive:
                    try:
                        _ensure_label(m, ARCHIVE_LABEL)
                        m.copy(uid, ARCHIVE_LABEL)
                        m.store(uid, "+FLAGS", "\\Deleted")
                    except Exception as e:
                        console.print(f"  [dim]archive failed: {e}[/dim]")

        if archive and not dry_run:
            try:
                m.expunge()
            except Exception:
                pass

        if flagged and not dry_run:
            log = _load(BOUNCE_LOG, [])
            log.extend(flagged)
            _save(BOUNCE_LOG, log)

        console.print(Panel(
            Text.from_markup(
                f"[bold green]Done[/bold green]\n\n"
                f"  Bounces processed: [white]{processed}[/white]\n"
                f"  Addresses flagged: [white]{sum(1 for f in flagged if f['matched'])}[/white]\n"
                f"  Archive label:     [white]{ARCHIVE_LABEL if archive else '—'}[/white]\n\n"
                f"  Future blasts will skip these addresses automatically."
            ),
            border_style="green",
        ))

    finally:
        try: m.close()
        except Exception: pass
        m.logout()

    return {"processed": processed, "flagged_with_match": sum(1 for f in flagged if f['matched'])}


def main():
    p = argparse.ArgumentParser(description="IMAP bounce auto-handler")
    p.add_argument("--dry-run",    action="store_true", help="Report only, don't modify files")
    p.add_argument("--no-archive", action="store_true", help="Keep bounces in inbox")
    p.add_argument("--since",      type=int, default=7,  help="Days to look back")
    args = p.parse_args()
    run(dry_run=args.dry_run, archive=not args.no_archive, since_days=args.since)


if __name__ == "__main__":
    main()
