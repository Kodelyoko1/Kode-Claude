"""
CarouselForge — autonomous LinkedIn / Instagram carousel designer.
Revenue: $29/carousel, $99/mo (4 carousels), $297/mo unlimited.

Two input sources:
  1. Owner-dropped JSON in data/cr_inputs/{slug}.json:
       {
         "title": "5 Mistakes Killing Your DTC Checkout",
         "platform": "ig|li|pinterest",       # default ig (1080x1080)
         "theme": "dark|light|brand",
         "handle": "@yourbrand",
         "cta": "Save this post 🔖",
         "slides": [
           {"heading": "1. Hidden fees", "body": "Surprise shipping at step 3 kills 40% of carts."},
           {"heading": "2. Slow load",   "body": "Each +1s = -7% conversions."}
         ]
       }
  2. Auto-ingest from data/sn_outputs/*.md (ShowNotes): the "Key takeaways"
     bullets become slide bodies. Marker file {slug}.carousel.skip blocks
     auto-ingest for a given show notes file.

Outputs:
  data/cr_outputs/{slug}/cover.png
  data/cr_outputs/{slug}/slide_01.png ... slide_NN.png
  data/cr_outputs/{slug}/cta.png
"""
import json
import re
import sys
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).parent.parent))
from autonomous import storage, mailer, billing, metrics
from carouselforge import health

AGENT_KEY = "carouselforge"
INPUTS_DIR = Path(__file__).parent.parent / "data" / "cr_inputs"
OUTPUTS_DIR = Path(__file__).parent.parent / "data" / "cr_outputs"
SN_OUTPUTS_DIR = Path(__file__).parent.parent / "data" / "sn_outputs"

PLATFORM_SIZES = {
    "ig":        (1080, 1080),
    "li":        (1080, 1350),
    "pinterest": (1000, 1500),
}

FONT_CANDIDATES_BOLD = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
]
FONT_CANDIDATES_REG = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
]

THEMES = {
    "dark":  {"bg": "#0F1115", "fg": "#FFFFFF", "muted": "#9AA3B2", "accent": "#FFD24A", "card": "#1A1D24"},
    "light": {"bg": "#F7F5F0", "fg": "#0F1115", "muted": "#5C6470", "accent": "#FF4F2E", "card": "#FFFFFF"},
    "brand": {"bg": "#1A237E", "fg": "#FFFFFF", "muted": "#B8C0DC", "accent": "#FFC107", "card": "#283593"},
}


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    paths = FONT_CANDIDATES_BOLD if bold else FONT_CANDIDATES_REG
    for p in paths:
        if Path(p).exists():
            return ImageFont.truetype(p, size=size)
    return ImageFont.load_default()


def _wrap(text: str, font: ImageFont.FreeTypeFont, max_w: int) -> list:
    words = text.split()
    if not words:
        return []
    lines, cur = [], words[0]
    for w in words[1:]:
        test = f"{cur} {w}"
        if font.getlength(test) <= max_w:
            cur = test
        else:
            lines.append(cur)
            cur = w
    lines.append(cur)
    return lines


def _fit(text: str, max_w: int, max_h: int, start: int, mn: int,
         bold: bool = True, line_gap: float = 1.15) -> tuple:
    size = start
    while size >= mn:
        f = _font(size, bold=bold)
        lines = _wrap(text, f, max_w)
        lh = int(size * line_gap)
        if lh * len(lines) <= max_h and all(f.getlength(l) <= max_w for l in lines):
            return lines, f, lh
        size -= 4
    f = _font(mn, bold=bold)
    return _wrap(text, f, max_w), f, int(mn * line_gap)


def _draw_lines(draw: ImageDraw.ImageDraw, lines: list, font: ImageFont.FreeTypeFont,
                lh: int, x: int, y: int, w: int, color: str, align: str = "left"):
    for i, line in enumerate(lines):
        lw = font.getlength(line)
        if align == "center":
            xx = x + (w - lw) / 2
        elif align == "right":
            xx = x + w - lw
        else:
            xx = x
        draw.text((xx, y + i * lh), line, font=font, fill=color)


def _footer(draw: ImageDraw.ImageDraw, w: int, h: int, handle: str, page: str, theme: dict):
    f = _font(28, bold=False)
    pad = 50
    if handle:
        draw.text((pad, h - pad - 28), handle, font=f, fill=theme["muted"])
    if page:
        tw = f.getlength(page)
        draw.text((w - pad - tw, h - pad - 28), page, font=f, fill=theme["muted"])


def render_cover(title: str, theme: dict, size: tuple, handle: str = "", swipe_hint: str = "Swipe →") -> Image.Image:
    w, h = size
    img = Image.new("RGB", size, theme["bg"])
    draw = ImageDraw.Draw(img)

    bar_w = max(14, w // 80)
    draw.rectangle([0, 0, bar_w, h], fill=theme["accent"])

    pad = 80
    box_w = w - 2 * pad - bar_w
    box_h = int(h * 0.55)
    lines, font, lh = _fit(title.upper(), box_w, box_h, start=140, mn=56, bold=True)
    block_h = lh * len(lines)
    y0 = (h - block_h) // 2 - int(h * 0.04)
    _draw_lines(draw, lines, font, lh, bar_w + pad, y0, box_w, theme["fg"])

    hint_font = _font(36, bold=True)
    hint = swipe_hint.upper()
    hw = hint_font.getlength(hint)
    pill_w = hw + 60
    pill_h = 70
    pill_x = (w - pill_w) // 2
    pill_y = h - 160
    draw.rounded_rectangle([pill_x, pill_y, pill_x + pill_w, pill_y + pill_h],
                           radius=pill_h // 2, fill=theme["accent"])
    draw.text((pill_x + 30, pill_y + 16), hint, font=hint_font, fill="#000000")

    _footer(draw, w, h, handle, "", theme)
    return img


def render_body_slide(heading: str, body: str, idx: int, total: int,
                      theme: dict, size: tuple, handle: str = "") -> Image.Image:
    w, h = size
    img = Image.new("RGB", size, theme["bg"])
    draw = ImageDraw.Draw(img)

    pad = 80
    card_x, card_y = pad, pad + 20
    card_w, card_h = w - 2 * pad, h - 2 * pad - 80
    draw.rounded_rectangle([card_x, card_y, card_x + card_w, card_y + card_h],
                           radius=36, fill=theme["card"])

    inner_pad = 60
    inner_w = card_w - 2 * inner_pad
    head_lines, head_font, head_lh = _fit(heading.upper(), inner_w, int(card_h * 0.35),
                                          start=84, mn=42, bold=True)
    body_lines, body_font, body_lh = _fit(body, inner_w, int(card_h * 0.5),
                                          start=46, mn=28, bold=False, line_gap=1.3)

    y = card_y + inner_pad
    _draw_lines(draw, head_lines, head_font, head_lh,
                card_x + inner_pad, y, inner_w, theme["fg"])
    y += head_lh * len(head_lines) + 40

    accent_h = 8
    draw.rectangle([card_x + inner_pad, y, card_x + inner_pad + 120, y + accent_h],
                   fill=theme["accent"])
    y += accent_h + 36

    _draw_lines(draw, body_lines, body_font, body_lh,
                card_x + inner_pad, y, inner_w, theme["muted"])

    _footer(draw, w, h, handle, f"{idx} / {total}", theme)
    return img


def render_cta_slide(cta: str, handle: str, theme: dict, size: tuple) -> Image.Image:
    w, h = size
    img = Image.new("RGB", size, theme["accent"])
    draw = ImageDraw.Draw(img)

    pad = 100
    box_w = w - 2 * pad
    lines, font, lh = _fit(cta.upper(), box_w, int(h * 0.5), start=120, mn=48, bold=True)
    block_h = lh * len(lines)
    y0 = (h - block_h) // 2 - 60
    _draw_lines(draw, lines, font, lh, pad, y0, box_w, "#000000", align="center")

    if handle:
        hf = _font(40, bold=True)
        hw = hf.getlength(handle)
        draw.text(((w - hw) / 2, h - 200), handle, font=hf, fill="#000000")
    return img


def build_carousel(spec: dict, slug: str) -> dict:
    platform = spec.get("platform", "ig")
    size = PLATFORM_SIZES.get(platform, PLATFORM_SIZES["ig"])
    theme = THEMES.get(spec.get("theme", "dark"), THEMES["dark"])
    handle = spec.get("handle", "")
    cta = spec.get("cta", "Save this post 🔖")
    slides = spec.get("slides", [])
    if not slides:
        return {"slug": slug, "error": "no slides provided"}

    out_dir = OUTPUTS_DIR / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    total_body = len(slides)
    produced = []

    cover = render_cover(spec.get("title", "UNTITLED"), theme, size, handle=handle)
    cover_path = out_dir / "cover.png"
    cover.save(cover_path, "PNG", optimize=True)
    produced.append(str(cover_path))

    for i, s in enumerate(slides, 1):
        heading = s.get("heading", "")
        body = s.get("body", "")
        img = render_body_slide(heading, body, i, total_body, theme, size, handle=handle)
        p = out_dir / f"slide_{i:02d}.png"
        img.save(p, "PNG", optimize=True)
        produced.append(str(p))

    cta_img = render_cta_slide(cta, handle, theme, size)
    cta_path = out_dir / "cta.png"
    cta_img.save(cta_path, "PNG", optimize=True)
    produced.append(str(cta_path))

    return {"slug": slug, "produced": produced, "slide_count": len(produced)}


def _spec_from_shownotes(md_path: Path) -> dict:
    text = md_path.read_text(errors="ignore")
    title_m = re.search(r"^#\s+(.+)$", text, re.M)
    title = title_m.group(1).strip() if title_m else md_path.stem
    block_m = re.search(r"##\s+Key takeaways\s*\n(.+?)(?:\n##|\Z)", text, re.S)
    if not block_m:
        return {}
    bullets = re.findall(r"^\s*-\s+(.+)$", block_m.group(1), re.M)
    if not bullets:
        return {}
    slides = []
    for i, b in enumerate(bullets[:6], 1):
        sent = re.split(r"(?<=[.!?])\s+", b)[0]
        heading = f"{i}. " + (sent[:55].rsplit(" ", 1)[0] if len(sent) > 55 else sent.rstrip(".,!?"))
        slides.append({"heading": heading, "body": b})
    return {
        "title": title,
        "platform": "ig",
        "theme": "dark",
        "handle": "@wholesaleomniverse",
        "cta": "Save this 🔖   Follow for more",
        "slides": slides,
    }


def build_queue() -> dict:
    INPUTS_DIR.mkdir(parents=True, exist_ok=True)
    produced_carousels = 0
    failed = 0

    for spec_path in sorted(INPUTS_DIR.glob("*.json")):
        slug = spec_path.stem
        if (OUTPUTS_DIR / slug).exists():
            continue
        try:
            spec = json.loads(spec_path.read_text())
        except Exception as e:
            failed += 1
            health.record_carousel(slug, "spec_invalid", source="cr_inputs",
                                   detail=f"{type(e).__name__}: {str(e)[:60]}")
            continue
        try:
            r = build_carousel(spec, slug)
        except Exception as e:
            failed += 1
            health.record_carousel(slug, "build_failed", source="cr_inputs",
                                   platform=spec.get("platform", ""),
                                   detail=f"{type(e).__name__}: {str(e)[:80]}")
            continue
        if "error" in r:
            failed += 1
            health.record_carousel(slug, "no_slides", source="cr_inputs",
                                   platform=spec.get("platform", ""),
                                   detail=r["error"])
        else:
            produced_carousels += 1
            health.record_carousel(slug, "success", source="cr_inputs",
                                   platform=spec.get("platform", "ig"),
                                   slide_count=r.get("slide_count", 0))

    if SN_OUTPUTS_DIR.exists():
        for md in SN_OUTPUTS_DIR.glob("*.md"):
            slug = md.stem
            if (OUTPUTS_DIR / slug).exists():
                continue
            if (SN_OUTPUTS_DIR / f"{slug}.carousel.skip").exists():
                continue
            spec = _spec_from_shownotes(md)
            if not spec:
                continue
            try:
                r = build_carousel(spec, slug)
            except Exception as e:
                health.record_carousel(slug, "build_failed", source="sn_outputs",
                                       platform=spec.get("platform", ""),
                                       detail=f"{type(e).__name__}: {str(e)[:80]}")
                continue
            if "error" not in r:
                produced_carousels += 1
                health.record_carousel(slug, "success", source="sn_outputs",
                                       platform=spec.get("platform", "ig"),
                                       slide_count=r.get("slide_count", 0))
            else:
                health.record_carousel(slug, "no_slides", source="sn_outputs",
                                       platform=spec.get("platform", ""),
                                       detail=r["error"])
    return {"carousels_produced": produced_carousels, "failures": failed}


def fulfill_cycle() -> dict:
    subs = storage.load("cr_subscribers.json", [])
    log = storage.load("cr_delivery_log.json", {})
    sent = 0
    for sub in subs:
        if sub.get("status") != "active":
            continue
        email = sub.get("email")
        if not email:
            continue
        already = set(log.get(email, []))
        new_dirs = [d for d in OUTPUTS_DIR.iterdir() if d.is_dir() and d.name not in already]
        if not new_dirs:
            continue
        body_parts = [f"Hi {sub.get('name', 'there')},\n",
                      f"{len(new_dirs)} new carousel(s) ready:\n"]
        for d in new_dirs[:8]:
            slides = sorted(d.glob("*.png"))
            body_parts.append(f"\n--- {d.name} ({len(slides)} slides) ---")
            for p in slides:
                body_parts.append(f"  data/cr_outputs/{d.name}/{p.name}")
        body = "\n".join(body_parts) + "\n"
        r = mailer.send(AGENT_KEY, email,
                        f"Carousels ready — {len(new_dirs)} new",
                        body, purpose="fulfillment")
        if r.get("status") == "sent":
            log[email] = list(already | {d.name for d in new_dirs})
            sent += 1
            health.record_delivery(email, "success", slugs=len(new_dirs))
        else:
            health.record_delivery(email, "mail_failed", slugs=len(new_dirs),
                                   detail=f"mailer={r.get('status','?')}: "
                                          f"{(r.get('reason') or r.get('error',''))[:80]}")
    storage.save("cr_delivery_log.json", log)
    return {"fulfillment_sent": sent}


def acquire_cycle() -> dict:
    leads = storage.load("cr_leads.json", [])
    sent = 0
    for lead in leads:
        if lead.get("trial_sent"):
            continue
        email = lead.get("email")
        if not email:
            continue
        body = (
            f"Hi {lead.get('name', 'there')},\n\n"
            f"I run an automated carousel design service for LinkedIn and Instagram creators.\n"
            f"Send me your next post idea (or even just a tweet you want to expand) and you'll\n"
            f"get back a full multi-slide carousel within 24 hours — first one free.\n\n"
            f"Pricing after the trial:\n"
            f"  $29 per carousel (one-off)\n"
            f"  $99/mo for 4 carousels\n"
            f"  $297/mo unlimited\n\n"
            f"Reply with a topic + your handle and I'll send a sample.\n"
        )
        r = mailer.send(AGENT_KEY, email,
                        "Free LinkedIn/IG carousel for your next post",
                        body, purpose="outreach")
        if r.get("status") == "sent":
            lead["trial_sent"] = datetime.now().isoformat()
            sent += 1
    storage.save("cr_leads.json", leads)
    return {"outreach_sent": sent}


def run_full_cycle() -> dict:
    q = build_queue()
    a = acquire_cycle()
    f = fulfill_cycle()
    rev = billing.revenue_summary(AGENT_KEY)
    subs = storage.load("cr_subscribers.json", [])
    metrics.record(
        AGENT_KEY,
        products_produced=q["carousels_produced"],
        outreach_sent=a["outreach_sent"],
        fulfillment_sent=f["fulfillment_sent"],
        active_subs=sum(1 for s in subs if s.get("status") == "active"),
        mrr=rev["mrr"],
        total_revenue=rev["total_paid"],
    )
    return {**q, **a, **f, **rev}
