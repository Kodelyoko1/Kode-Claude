#!/usr/bin/env python3
"""
Facebook Daily Draft — generates today's FB post (copy + image suggestion +
suggested boost settings) and emails it to the owner.

You paste it into Meta Business Suite, click Schedule or Boost, and that's it.
No API permission wars, no scope hunting.

Usage:
  python3 run_fb_draft.py                       # generate + email today's draft
  python3 run_fb_draft.py --audience sellers    # force audience
  python3 run_fb_draft.py --save-only           # save to disk, don't email
  python3 run_fb_draft.py --print-only          # print to terminal, don't email or save
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

from social_agent.content import pick_post, format_for_platform
from email_template import send_branded_email

console = Console()
DATA_DIR  = Path(__file__).parent / "data"
DRAFT_DIR = DATA_DIR / "facebook_drafts"
DRAFT_DIR.mkdir(parents=True, exist_ok=True)

# Map audience → suggested boost targeting (raw text, you paste into Boost dialog)
BOOST_TARGETING = {
    "sellers": {
        "audience": "People interested in selling their home, "
                    "homeowners with mortgage stress, distressed property owners",
        "locations": "Detroit MI, Memphis TN, Atlanta GA, Cleveland OH, Chicago IL",
        "age": "35–65",
        "interests": "Real Estate, Home Selling, Foreclosure, Probate, "
                     "First-Time Home Sellers",
        "budget_per_day": "$5–10",
        "duration": "3 days",
        "goal": "Engagement (Likes, Comments, Shares)",
    },
    "buyers": {
        "audience": "Real estate investors, cash buyers, landlords, "
                    "fix-and-flip investors",
        "locations": "Detroit MI, Memphis TN, Atlanta GA, Cleveland OH, Chicago IL",
        "age": "30–60",
        "interests": "Real Estate Investing, BiggerPockets, REIA, Rental Property, "
                     "Real Estate Investment Trust, Property Management",
        "budget_per_day": "$5–10",
        "duration": "5 days",
        "goal": "Page Engagement / Message",
    },
    "wholesalers": {
        "audience": "Real estate wholesalers, new investors, real estate licensees, "
                    "people interested in wholesale houses",
        "locations": "United States",
        "age": "25–55",
        "interests": "Real Estate Wholesaling, BiggerPockets, Real Estate Investing, "
                     "Real Estate Education, Entrepreneurship",
        "budget_per_day": "$5–10",
        "duration": "5 days",
        "goal": "Traffic to wholesaleomniverse.com",
    },
}

# Suggested local image asset to attach (you can swap in MBS)
SUGGESTED_IMAGES = {
    "sellers":     "data/body_bg2.jpg",
    "buyers":      "data/body_bg.jpg",
    "wholesalers": "data/logo.png",
}


def _audience_for_today() -> str:
    """Rotate audience by day-of-week so the Page doesn't get repetitive."""
    dow = datetime.datetime.now().weekday()  # Mon=0 .. Sun=6
    return {
        0: "sellers", 1: "wholesalers", 2: "buyers",
        3: "sellers", 4: "wholesalers", 5: "buyers", 6: "sellers",
    }[dow]


def build_draft(audience: str = "") -> dict:
    audience = audience or _audience_for_today()
    post = pick_post(audience)
    formatted = format_for_platform(post, "facebook")

    boost = BOOST_TARGETING.get(audience, BOOST_TARGETING["sellers"])
    image_hint = SUGGESTED_IMAGES.get(audience, "data/logo.png")
    return {
        "date": datetime.datetime.now().strftime("%Y-%m-%d"),
        "audience": audience,
        "title": post["title"],
        "post_text": formatted["text"],
        "suggested_image": image_hint,
        "boost": boost,
    }


def save_draft(draft: dict) -> Path:
    out = DRAFT_DIR / f"{draft['date']}-{draft['audience']}.json"
    out.write_text(json.dumps(draft, indent=2))
    return out


def render_text(draft: dict) -> str:
    b = draft["boost"]
    return (
        f"FACEBOOK DAILY DRAFT — {draft['date']}  |  audience={draft['audience']}\n"
        f"{'='*72}\n\n"
        f"┌── POST TEXT (copy this exactly into Meta Business Suite) ─────────────┐\n\n"
        f"{draft['post_text']}\n\n"
        f"└────────────────────────────────────────────────────────────────────────┘\n\n"
        f"SUGGESTED IMAGE: {draft['suggested_image']}\n"
        f"  (open the file from this server or substitute any related stock image)\n\n"
        f"┌── SUGGESTED BOOST SETTINGS ─────────────────────────────────────────────┐\n"
        f"  Goal:        {b['goal']}\n"
        f"  Audience:    {b['audience']}\n"
        f"  Locations:   {b['locations']}\n"
        f"  Age:         {b['age']}\n"
        f"  Interests:   {b['interests']}\n"
        f"  Daily budget:{b['budget_per_day']}\n"
        f"  Duration:    {b['duration']}\n"
        f"└─────────────────────────────────────────────────────────────────────────┘\n\n"
        f"WORKFLOW (~2 min):\n"
        f"  1. Open https://business.facebook.com/latest/posts\n"
        f"  2. Click [Create Post] → paste the POST TEXT above\n"
        f"  3. Attach the suggested image\n"
        f"  4. Click [Publish] OR [Boost Post] and apply the settings above\n"
    )


def render_html(draft: dict, image_cid: str = "") -> str:
    b = draft["boost"]
    text = draft["post_text"].replace("\n", "<br>")
    image_block = ""
    if image_cid:
        image_block = (
            f'<p style="margin-top:24px;"><strong>Suggested image '
            f'(also attached to this email):</strong></p>'
            f'<img src="cid:{image_cid}" alt="suggested post image" '
            f'style="display:block;max-width:520px;width:100%;height:auto;'
            f'border-radius:8px;border:1px solid #e5e7eb;margin:8px 0;"/>'
            f'<p style="color:#6b7280;font-size:12px;margin:4px 0 18px;">'
            f'Right-click → Save image, then upload it when you publish or boost.</p>'
        )
    boost_html = (
        f'<tr><td style="padding:4px 12px;color:#6b7280;">Goal</td>'
        f'<td style="padding:4px 12px;font-weight:600;">{b["goal"]}</td></tr>'
        f'<tr><td style="padding:4px 12px;color:#6b7280;">Audience</td>'
        f'<td style="padding:4px 12px;">{b["audience"]}</td></tr>'
        f'<tr><td style="padding:4px 12px;color:#6b7280;">Locations</td>'
        f'<td style="padding:4px 12px;">{b["locations"]}</td></tr>'
        f'<tr><td style="padding:4px 12px;color:#6b7280;">Age</td>'
        f'<td style="padding:4px 12px;">{b["age"]}</td></tr>'
        f'<tr><td style="padding:4px 12px;color:#6b7280;">Interests</td>'
        f'<td style="padding:4px 12px;">{b["interests"]}</td></tr>'
        f'<tr><td style="padding:4px 12px;color:#6b7280;">Budget</td>'
        f'<td style="padding:4px 12px;font-weight:600;">{b["budget_per_day"]}</td></tr>'
        f'<tr><td style="padding:4px 12px;color:#6b7280;">Duration</td>'
        f'<td style="padding:4px 12px;">{b["duration"]}</td></tr>'
    )
    return (
        f'<p><strong>Today\'s Facebook draft</strong> '
        f'(audience: <code>{draft["audience"]}</code>)</p>'
        f'<div style="background:#f8fafc;border:1px solid #e5e7eb;border-radius:8px;'
        f'padding:18px;margin:12px 0;">'
        f'<div style="font-size:11px;letter-spacing:1px;color:#6b7280;'
        f'text-transform:uppercase;margin-bottom:8px;">Post Text — copy as-is</div>'
        f'<div style="font-size:15px;line-height:1.7;color:#0f172a;'
        f'white-space:pre-wrap;">{text}</div>'
        f'</div>'
        f'{image_block}'
        f'<p style="margin-top:24px;"><strong>Suggested boost settings:</strong></p>'
        f'<table style="border-collapse:collapse;width:100%;background:#fff;'
        f'border:1px solid #e5e7eb;border-radius:8px;overflow:hidden;">'
        f'{boost_html}'
        f'</table>'
        f'<p style="margin-top:24px;color:#6b7280;">'
        f'Open <a href="https://business.facebook.com/latest/posts" '
        f'style="color:#f59e0b;">Meta Business Suite</a> → '
        f'Create Post → paste the text above → attach the image → '
        f'Publish or Boost.</p>'
    )


def email_draft(draft: dict) -> dict:
    to = os.environ.get("DIGEST_EMAIL") or os.environ.get("SMTP_USER", "")
    if not to:
        return {"status": "no_recipient"}
    subject = f"FB draft for {draft['date']} — {draft['audience']} audience"

    image_path = Path(__file__).parent / draft["suggested_image"]
    inline = {}
    attach = []
    image_cid = ""
    if image_path.exists():
        image_cid = "fbdraft_image"
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
    parser = argparse.ArgumentParser(description="FB Daily Draft Generator")
    parser.add_argument("--audience", default="",
                        choices=["", "sellers", "buyers", "wholesalers"],
                        help="Override the auto-rotated audience")
    parser.add_argument("--save-only", action="store_true", help="Save to disk only")
    parser.add_argument("--print-only", action="store_true", help="Print to terminal only")
    args = parser.parse_args()

    draft = build_draft(args.audience)

    if args.print_only:
        console.print(render_text(draft))
        return

    saved = save_draft(draft)
    console.print(f"[green]✓ Draft saved:[/green] {saved}")

    if args.save_only:
        return

    r = email_draft(draft)
    if r.get("status") == "sent":
        to = os.environ.get("DIGEST_EMAIL") or os.environ.get("SMTP_USER", "")
        console.print(f"[green]✓ Emailed to {to}[/green]")
    else:
        console.print(f"[yellow]Email not sent: {r.get('status')} {r.get('error','')}[/yellow]")
        console.print("\n[dim]Falling back to terminal output:[/dim]\n")
        console.print(render_text(draft))


if __name__ == "__main__":
    main()
