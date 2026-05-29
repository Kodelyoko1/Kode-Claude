#!/usr/bin/env python3
"""
Pinterest Daily Draft — manual fallback while Pinterest API approval is pending.

Generates today's pin (title + description + image + destination URL) and emails
it to you ready to paste into Pinterest. Once the API is approved, switch back
to run_pinterest_auto.py for full automation.

Usage:
  python3 run_pinterest_draft.py                    # generate + email today's draft
  python3 run_pinterest_draft.py --type affiliate   # force a specific pin type
  python3 run_pinterest_draft.py --city "Detroit,MI"
  python3 run_pinterest_draft.py --save-only        # save to disk, skip email
  python3 run_pinterest_draft.py --print-only       # print to terminal, no save/email
"""
import argparse
import datetime
import json
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

# Reuse the Pinterest content generators we already built
from run_pinterest_auto import seller_pin, buyer_pin, wholesaler_pin, affiliate_pin
from email_template import send_branded_email

console = Console()
DATA_DIR  = Path(__file__).parent / "data"
DRAFT_DIR = DATA_DIR / "pinterest_drafts"
DRAFT_DIR.mkdir(parents=True, exist_ok=True)

# Map pin type → suggested local image asset (you can swap when pasting)
SUGGESTED_IMAGES = {
    "seller":     "data/body_bg2.jpg",
    "buyer":      "data/body_bg.jpg",
    "wholesaler": "data/night_sky.jpg",
    "affiliate":  "data/logo.png",
}


def _audience_for_today() -> str:
    """Rotate pin type by day-of-week so the feed stays varied."""
    dow = datetime.datetime.now().weekday()  # Mon=0 .. Sun=6
    return {0:"seller", 1:"wholesaler", 2:"buyer",
            3:"affiliate", 4:"seller", 5:"wholesaler", 6:"buyer"}[dow]


def build_draft(forced_type: str = "", forced_city: str = "") -> dict:
    pin_type = forced_type or _audience_for_today()
    builder = {
        "seller":     lambda: seller_pin(forced_city),
        "buyer":      buyer_pin,
        "wholesaler": wholesaler_pin,
        "affiliate":  affiliate_pin,
    }.get(pin_type)
    pin = builder() if builder else seller_pin(forced_city)
    if not pin:  # affiliate may return {} if no affiliate URLs set
        pin = seller_pin(forced_city)
        pin_type = "seller"

    return {
        "date":             datetime.datetime.now().strftime("%Y-%m-%d"),
        "type":             pin["type"],
        "title":            pin["title"],
        "description":      pin["description"],
        "destination_link": pin["link"],
        "suggested_image":  SUGGESTED_IMAGES.get(pin_type.split(":")[0], "data/logo.png"),
    }


def save_draft(draft: dict) -> Path:
    out = DRAFT_DIR / f"{draft['date']}-{draft['type'].replace(':', '-')}.json"
    out.write_text(json.dumps(draft, indent=2))
    return out


def render_text(draft: dict) -> str:
    return (
        f"PINTEREST DAILY DRAFT — {draft['date']}  |  type={draft['type']}\n"
        f"{'='*72}\n\n"
        f"┌── TITLE (paste into Pinterest 'Title' field) ──────────────────────────┐\n\n"
        f"{draft['title']}\n\n"
        f"└────────────────────────────────────────────────────────────────────────┘\n\n"
        f"┌── DESCRIPTION (paste into Pinterest 'Description' field) ──────────────┐\n\n"
        f"{draft['description']}\n\n"
        f"└────────────────────────────────────────────────────────────────────────┘\n\n"
        f"DESTINATION URL (paste into Pinterest 'Add a destination link' field):\n"
        f"  {draft['destination_link']}\n\n"
        f"SUGGESTED IMAGE:\n"
        f"  {draft['suggested_image']}\n"
        f"  (Also attached to this email — save and upload as the pin image)\n\n"
        f"WORKFLOW (~60 sec):\n"
        f"  1. Go to https://www.pinterest.com/pin-creation-tool/\n"
        f"  2. Upload the attached image\n"
        f"  3. Paste TITLE, DESCRIPTION, and DESTINATION URL above\n"
        f"  4. Pick a board → Publish\n"
    )


def render_html(draft: dict, image_cid: str = "") -> str:
    img_block = ""
    if image_cid:
        img_block = (
            f'<p style="margin-top:24px;"><strong>Suggested image (also attached):</strong></p>'
            f'<img src="cid:{image_cid}" alt="suggested pin image" '
            f'style="display:block;max-width:520px;width:100%;height:auto;border-radius:8px;'
            f'border:1px solid #e5e7eb;margin:8px 0;"/>'
            f'<p style="color:#6b7280;font-size:12px;margin:4px 0 18px;">'
            f'Right-click → Save image, then upload it when you create the pin.</p>'
        )
    desc = draft["description"].replace("\n", "<br>")
    return (
        f'<p><strong>Today\'s Pinterest draft</strong> '
        f'(<code>{draft["type"]}</code>)</p>'
        f'<div style="background:#f8fafc;border:1px solid #e5e7eb;border-radius:8px;'
        f'padding:18px;margin:12px 0;">'
        f'<div style="font-size:11px;letter-spacing:1px;color:#6b7280;'
        f'text-transform:uppercase;margin-bottom:8px;">Title</div>'
        f'<div style="font-size:18px;font-weight:700;color:#0f172a;">{draft["title"]}</div>'
        f'</div>'
        f'<div style="background:#f8fafc;border:1px solid #e5e7eb;border-radius:8px;'
        f'padding:18px;margin:12px 0;">'
        f'<div style="font-size:11px;letter-spacing:1px;color:#6b7280;'
        f'text-transform:uppercase;margin-bottom:8px;">Description</div>'
        f'<div style="font-size:14px;line-height:1.7;color:#0f172a;white-space:pre-wrap;">{desc}</div>'
        f'</div>'
        f'<p style="margin-top:24px;"><strong>Destination URL:</strong><br>'
        f'<a href="{draft["destination_link"]}" style="color:#f59e0b;">{draft["destination_link"]}</a></p>'
        f'{img_block}'
        f'<p style="margin-top:24px;color:#6b7280;">'
        f'Open <a href="https://www.pinterest.com/pin-creation-tool/" '
        f'style="color:#f59e0b;">Pinterest Pin Creation Tool</a> → upload the image, '
        f'paste the title + description + URL, pick a board, publish.</p>'
    )


def email_draft(draft: dict) -> dict:
    to = os.environ.get("DIGEST_EMAIL") or os.environ.get("SMTP_USER", "")
    if not to:
        return {"status": "no_recipient"}
    subject = f"Pinterest draft for {draft['date']} — {draft['type']}"

    image_path = Path(__file__).parent / draft["suggested_image"]
    inline, attach, image_cid = {}, [], ""
    if image_path.exists():
        image_cid = "pinterest_image"
        inline[image_cid] = str(image_path)
        attach.append(str(image_path))

    return send_branded_email(
        to_email=to,
        subject=subject,
        body_text=render_text(draft),
        body_html_inner=render_html(draft, image_cid=image_cid),
        inline_images=inline,
        attachments=attach,
    )


def main():
    parser = argparse.ArgumentParser(description="Pinterest Daily Draft Generator")
    parser.add_argument("--type",  default="",
                        choices=["", "seller", "buyer", "wholesaler", "affiliate"],
                        help="Force a specific pin type (default: day-of-week rotation)")
    parser.add_argument("--city",  default="", help="Force a market for seller pins")
    parser.add_argument("--save-only",  action="store_true", help="Save to disk, skip email")
    parser.add_argument("--print-only", action="store_true", help="Print to terminal only")
    args = parser.parse_args()

    draft = build_draft(args.type, args.city)

    if args.print_only:
        console.print(render_text(draft))
        return

    saved = save_draft(draft)
    console.print(f"[green]✓ Draft saved:[/green] {saved}")

    if args.save_only:
        return

    r = email_draft(draft)
    to = os.environ.get("DIGEST_EMAIL") or os.environ.get("SMTP_USER", "")
    if r.get("status") == "sent":
        console.print(f"[green]✓ Emailed to {to}[/green]")
    else:
        console.print(f"[yellow]Email not sent: {r.get('status')} {r.get('error','')}[/yellow]")
        console.print("\n[dim]Terminal output:[/dim]\n")
        console.print(render_text(draft))


if __name__ == "__main__":
    main()
