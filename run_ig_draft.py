#!/usr/bin/env python3
"""
Instagram Daily Draft — manual workflow fallback for IG posting.

Generates today's Instagram post (caption + image + hashtags + suggested URL
for the link-in-bio swap) and emails it ready to paste into Instagram on your phone.

Once your Page + IG are linked and the token has instagram_content_publish scope,
we can switch to run_ig_auto.py for full automation. Until then, this gets you
on Instagram tonight.

Usage:
  python3 run_ig_draft.py                       # generate + email today's draft
  python3 run_ig_draft.py --audience sellers    # force audience
  python3 run_ig_draft.py --save-only           # save to disk, no email
  python3 run_ig_draft.py --print-only          # print to terminal
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
DRAFT_DIR = DATA_DIR / "instagram_drafts"
DRAFT_DIR.mkdir(parents=True, exist_ok=True)

# Map audience → suggested local image to attach as the IG post image
SUGGESTED_IMAGES = {
    "sellers":     "data/body_bg2.jpg",
    "buyers":      "data/body_bg.jpg",
    "wholesalers": "data/night_sky.jpg",
}

# Link-in-bio rotation — IG only allows one bio link, so we recommend swapping
# it based on the day's audience.
BIO_LINKS = {
    "sellers":     "https://wholesaleomniverse.netlify.app/sell.html",
    "buyers":      "https://wholesaleomniverse.netlify.app/buyers.html",
    "wholesalers": "https://wholesaleomniverse.netlify.app/tools.html",
}

# IG strips hashtags from being clickable in the caption past a certain count,
# so put the heavy tag block at the end. Caption hard limit: 2,200 chars.
EXTRA_HASHTAGS_BY_AUDIENCE = {
    "sellers":     "#WeBuyHouses #CashHomeBuyers #SellMyHouseFast #RealEstate "
                   "#Foreclosure #Probate #InheritedHouse #DistressedProperty "
                   "#MotivatedSeller #CashOffer #HouseBuyer #SellMyHome "
                   "#WholesaleHouses #RealEstateInvesting",
    "buyers":     "#CashBuyers #RealEstateInvestor #FixAndFlip #BRRRR "
                  "#WholesaleDeals #OffMarket #InvestmentProperty #PropertyInvestor "
                  "#RentalProperty #REIA #RealEstate #HouseFlipping #Landlord",
    "wholesalers":"#Wholesaling #RealEstateWholesaling #RealEstateInvesting "
                  "#WholesaleHouses #DealAnalysis #PropTech #BiggerPockets "
                  "#Entrepreneurship #PassiveIncome #FinancialFreedom "
                  "#RealEstateEducation #SmallBusiness",
}


def _audience_for_today() -> str:
    """Day-of-week rotation."""
    dow = datetime.datetime.now().weekday()  # Mon=0 .. Sun=6
    return {0:"sellers", 1:"wholesalers", 2:"buyers",
            3:"sellers", 4:"wholesalers", 5:"buyers", 6:"sellers"}[dow]


def build_draft(audience: str = "") -> dict:
    audience = audience or _audience_for_today()
    post = pick_post(audience)
    audience = post["audience"]  # normalize

    # IG caption: title on first line, blank line, body, blank line, CTA, hashtags
    caption = (
        f"{post['title']}\n\n"
        f"{post['body']}\n\n"
        f"{post['cta']}\n\n"
        f"{EXTRA_HASHTAGS_BY_AUDIENCE.get(audience, ' '.join(post['hashtags']))}"
    )
    caption = caption[:2200]  # IG hard limit

    return {
        "date":            datetime.datetime.now().strftime("%Y-%m-%d"),
        "audience":        audience,
        "title":           post["title"],
        "caption":         caption,
        "bio_link":        BIO_LINKS.get(audience, BIO_LINKS["sellers"]),
        "suggested_image": SUGGESTED_IMAGES.get(audience, "data/logo.png"),
    }


def save_draft(draft: dict) -> Path:
    out = DRAFT_DIR / f"{draft['date']}-{draft['audience']}.json"
    out.write_text(json.dumps(draft, indent=2))
    return out


def render_text(draft: dict) -> str:
    return (
        f"INSTAGRAM DAILY DRAFT — {draft['date']}  |  audience={draft['audience']}\n"
        f"{'='*72}\n\n"
        f"┌── CAPTION (paste this into the IG post caption field) ─────────────────┐\n\n"
        f"{draft['caption']}\n\n"
        f"└────────────────────────────────────────────────────────────────────────┘\n\n"
        f"SUGGESTED IMAGE:  {draft['suggested_image']}  (attached to this email)\n\n"
        f"BIO LINK FOR TODAY (swap your IG bio link to this):\n"
        f"  {draft['bio_link']}\n\n"
        f"WORKFLOW (~60 sec):\n"
        f"  1. Open Instagram app → tap the + at the bottom → Post\n"
        f"  2. Pick the image attached to this email (save to phone first)\n"
        f"  3. Tap 'Next' → 'Next' to skip filters\n"
        f"  4. Paste the CAPTION above into the caption field\n"
        f"  5. Tap 'Share'\n"
        f"  6. Update your bio link to today's URL (Edit Profile → Website)\n"
    )


def render_html(draft: dict, image_cid: str = "") -> str:
    caption = draft["caption"].replace("\n", "<br>")
    img_block = ""
    if image_cid:
        img_block = (
            f'<p style="margin-top:24px;"><strong>Suggested image (attached):</strong></p>'
            f'<img src="cid:{image_cid}" alt="suggested IG image" '
            f'style="display:block;max-width:520px;width:100%;height:auto;border-radius:8px;'
            f'border:1px solid #e5e7eb;margin:8px 0;"/>'
            f'<p style="color:#6b7280;font-size:12px;margin:4px 0 18px;">'
            f'Save to phone → upload as the post image.</p>'
        )
    return (
        f'<p><strong>Today\'s Instagram draft</strong> '
        f'(audience: <code>{draft["audience"]}</code>)</p>'
        f'<div style="background:#f8fafc;border:1px solid #e5e7eb;border-radius:8px;'
        f'padding:18px;margin:12px 0;">'
        f'<div style="font-size:11px;letter-spacing:1px;color:#6b7280;'
        f'text-transform:uppercase;margin-bottom:8px;">Caption — paste as-is</div>'
        f'<div style="font-size:14px;line-height:1.7;color:#0f172a;'
        f'white-space:pre-wrap;">{caption}</div>'
        f'</div>'
        f'{img_block}'
        f'<p style="margin-top:16px;"><strong>Bio link for today:</strong><br>'
        f'<a href="{draft["bio_link"]}" style="color:#f59e0b;">{draft["bio_link"]}</a><br>'
        f'<span style="color:#6b7280;font-size:13px;">'
        f'Update via Edit Profile → Website. Match the link to the audience the post is targeting.</span></p>'
        f'<p style="margin-top:24px;color:#6b7280;">'
        f'Open Instagram app → tap + → Post → upload image → paste caption → Share.</p>'
    )


def email_draft(draft: dict) -> dict:
    to = os.environ.get("DIGEST_EMAIL") or os.environ.get("SMTP_USER", "")
    if not to:
        return {"status": "no_recipient"}
    subject = f"IG draft for {draft['date']} — {draft['audience']} audience"

    image_path = Path(__file__).parent / draft["suggested_image"]
    inline, attach, image_cid = {}, [], ""
    if image_path.exists():
        image_cid = "ig_draft_image"
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
    parser = argparse.ArgumentParser(description="Instagram Daily Draft Generator")
    parser.add_argument("--audience", default="",
                        choices=["", "sellers", "buyers", "wholesalers"])
    parser.add_argument("--save-only", action="store_true")
    parser.add_argument("--print-only", action="store_true")
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
        console.print("\n[dim]Terminal output:[/dim]\n")
        console.print(render_text(draft))


if __name__ == "__main__":
    main()
