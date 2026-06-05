"""
TemplateForge — Canva/Figma-style template designer (mockup + brief).
Revenue: $19/template, $49/mo (5), $149/mo unlimited.

We can't programmatically push editable templates into Canva, but we CAN
ship the next-best thing: a finished PNG mockup the owner can use as a
visual reference + a precise design brief (sizes, colors, fonts, copy,
layout grid) so anyone can rebuild it in Canva/Figma in <10 minutes.

Owner manifest in data/td_inputs/{slug}.json:
  {
    "type": "ig_post|ig_story|media_kit|invoice|quote_card|product_card",
    "title": "Top 3 mistakes killing your DTC checkout",
    "subtitle": "Save this for later",
    "brand_color": "#FF4F2E",
    "niche": "DTC e-commerce",
    "handle": "@wholesaleomniverse"
  }

Output:
  data/td_outputs/{slug}/mockup.png  — visual reference at native canvas size
  data/td_outputs/{slug}/brief.md    — recreation instructions
  data/td_outputs/{slug}/spec.json   — machine-readable layout spec
"""
import json
import sys
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).parent.parent))
from autonomous import storage, mailer, billing, metrics
from templateforge import health

AGENT_KEY = "templateforge"
INPUTS_DIR = Path(__file__).parent.parent / "data" / "td_inputs"
OUTPUTS_DIR = Path(__file__).parent.parent / "data" / "td_outputs"

CANVAS_SIZES = {
    "ig_post":     (1080, 1080),
    "ig_story":    (1080, 1920),
    "media_kit":   (1240, 1754),    # A4 portrait @ 150dpi
    "invoice":     (1240, 1754),
    "quote_card":  (1080, 1080),
    "product_card":(1080, 1350),
}

FONT_BOLD = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]
FONT_REG = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
]


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    paths = FONT_BOLD if bold else FONT_REG
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
        if font.getlength(f"{cur} {w}") <= max_w:
            cur = f"{cur} {w}"
        else:
            lines.append(cur)
            cur = w
    lines.append(cur)
    return lines


def _hex(c: str, fallback: str = "#FF4F2E") -> str:
    c = (c or "").strip()
    if c.startswith("#") and len(c) in (4, 7):
        return c
    return fallback


def _draw_ig_post(spec: dict, size: tuple) -> Image.Image:
    w, h = size
    bg = "#0F1115"
    accent = _hex(spec.get("brand_color"))
    img = Image.new("RGB", size, bg)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, w, h // 6], fill=accent)
    title = (spec.get("title") or "Untitled").upper()
    f = _font(80, bold=True)
    lines = _wrap(title, f, w - 160)
    while sum(f.size * 1.2 for _ in lines) > h * 0.5 and f.size > 36:
        f = _font(int(f.size * 0.9), bold=True)
        lines = _wrap(title, f, w - 160)
    y = (h - len(lines) * int(f.size * 1.15)) / 2
    for line in lines:
        lw = f.getlength(line)
        d.text(((w - lw) / 2, y), line, font=f, fill="#FFFFFF")
        y += int(f.size * 1.15)
    sub = spec.get("subtitle") or ""
    if sub:
        sf = _font(40, bold=False)
        sw = sf.getlength(sub)
        d.text(((w - sw) / 2, h - 200), sub, font=sf, fill="#9AA3B2")
    handle = spec.get("handle", "")
    if handle:
        hf = _font(34, bold=True)
        d.text((60, h - 80), handle, font=hf, fill=accent)
    return img


def _draw_quote(spec: dict, size: tuple) -> Image.Image:
    w, h = size
    accent = _hex(spec.get("brand_color"))
    img = Image.new("RGB", size, "#F7F5F0")
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, 18, h], fill=accent)
    quote = spec.get("title") or "Type your quote here."
    qf = _font(72, bold=True)
    lines = _wrap(f'"{quote}"', qf, w - 240)
    while sum(qf.size * 1.2 for _ in lines) > h * 0.55 and qf.size > 32:
        qf = _font(int(qf.size * 0.9), bold=True)
        lines = _wrap(f'"{quote}"', qf, w - 240)
    y = (h - len(lines) * int(qf.size * 1.2)) / 2
    for line in lines:
        d.text((120, y), line, font=qf, fill="#1A1815")
        y += int(qf.size * 1.2)
    handle = spec.get("handle", "")
    if handle:
        hf = _font(30, bold=False)
        d.text((120, h - 80), handle, font=hf, fill=accent)
    return img


def _draw_invoice(spec: dict, size: tuple) -> Image.Image:
    w, h = size
    accent = _hex(spec.get("brand_color"))
    img = Image.new("RGB", size, "#FFFFFF")
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, w, 220], fill=accent)
    hf = _font(72, bold=True)
    d.text((80, 80), "INVOICE", font=hf, fill="#FFFFFF")
    bf = _font(36, bold=False)
    d.text((80, 280), spec.get("title") or "From: Your Business Name", font=bf, fill="#1A1815")
    d.text((80, 340), "Bill to: Client Name", font=bf, fill="#5C6470")
    d.text((80, 400), f"Invoice #: 0001    Date: {datetime.now():%Y-%m-%d}", font=_font(28), fill="#5C6470")
    d.rectangle([80, 500, w - 80, 504], fill=accent)
    rows = ["Item / Service                                  Qty   Rate     Total",
            "-" * 70,
            "[[Item 1]]                                      1     $0.00    $0.00",
            "[[Item 2]]                                      1     $0.00    $0.00"]
    mf = _font(28, bold=False)
    for i, row in enumerate(rows):
        d.text((80, 540 + i * 50), row, font=mf, fill="#1A1815")
    d.text((w - 360, h - 220), "Total: $0.00", font=_font(48, bold=True), fill=accent)
    handle = spec.get("handle", "")
    if handle:
        d.text((80, h - 80), handle, font=_font(28), fill="#5C6470")
    return img


def _draw_default(spec: dict, size: tuple) -> Image.Image:
    return _draw_ig_post(spec, size)


RENDERERS = {
    "ig_post":      _draw_ig_post,
    "ig_story":     _draw_ig_post,
    "quote_card":   _draw_quote,
    "invoice":      _draw_invoice,
    "media_kit":    _draw_invoice,
    "product_card": _draw_ig_post,
}


def _render_brief(spec: dict, size: tuple) -> str:
    t = spec.get("type", "ig_post")
    w, h = size
    return "\n".join([
        f"# {spec.get('title', 'Untitled template')}",
        "",
        f"**Type:** {t}",
        f"**Canvas size:** {w} × {h} px",
        f"**Brand color:** {_hex(spec.get('brand_color'))}",
        f"**Niche:** {spec.get('niche', 'general')}",
        "",
        "## Recreate in Canva / Figma",
        "",
        f"1. Create a new canvas at {w} × {h} px (matches the platform's native spec).",
        f"2. Set background and accent according to the mockup PNG in this bundle.",
        f"3. Use a heavy sans-serif (Inter Bold / Montserrat Black / Bebas Neue) for headlines.",
        f"4. Match the layout shown in `mockup.png` exactly — sizes are tuned for legibility on mobile feeds.",
        f"5. Replace `[[placeholder]]` text with real copy. Export as PNG (or PDF for invoices/media kits).",
        "",
        "## Copy to use",
        "",
        f"- Title: {spec.get('title', '')}",
        f"- Subtitle: {spec.get('subtitle', '')}",
        f"- Handle: {spec.get('handle', '')}",
        "",
        "## Tips",
        "",
        "- For carousels, duplicate this template, change the title, keep colors/handle locked.",
        "- For Instagram Stories, leave the top 250px and bottom 250px empty (UI overlays).",
        "",
        "---",
        f"_Generated {datetime.now():%Y-%m-%d} by TemplateForge._",
    ])


def build_template(spec: dict, slug: str) -> dict:
    t = spec.get("type", "ig_post")
    size = CANVAS_SIZES.get(t, CANVAS_SIZES["ig_post"])
    out_dir = OUTPUTS_DIR / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    renderer = RENDERERS.get(t, _draw_default)
    img = renderer(spec, size)
    img.save(out_dir / "mockup.png", "PNG", optimize=True)
    (out_dir / "brief.md").write_text(_render_brief(spec, size))
    (out_dir / "spec.json").write_text(json.dumps({
        "slug": slug, "type": t, "canvas": size,
        "brand_color": _hex(spec.get("brand_color")),
        "copy": {k: spec.get(k, "") for k in ("title", "subtitle", "handle")},
        "built_at": datetime.now().isoformat(),
    }, indent=2))
    return {"slug": slug, "type": t, "out_dir": str(out_dir)}


def build_queue() -> dict:
    INPUTS_DIR.mkdir(parents=True, exist_ok=True)
    produced = 0
    failed = 0
    for spec_path in sorted(INPUTS_DIR.glob("*.json")):
        slug = spec_path.stem
        if (OUTPUTS_DIR / slug / "mockup.png").exists():
            continue
        try:
            spec = json.loads(spec_path.read_text())
        except Exception:
            failed += 1
            continue
        build_template(spec, slug)
        produced += 1
    return {"templates_produced": produced, "failures": failed}


def fulfill_cycle() -> dict:
    subs = storage.load("td_subscribers.json", [])
    log = storage.load("td_delivery_log.json", {})
    sent = 0
    for sub in subs:
        if sub.get("status") != "active":
            continue
        email = sub.get("email")
        if not email:
            continue
        already = set(log.get(email, []))
        new = [d for d in OUTPUTS_DIR.iterdir()
               if d.is_dir() and d.name not in already and (d / "mockup.png").exists()]
        if not new:
            continue
        body_parts = [f"Hi {sub.get('name', 'there')},\n",
                      f"{len(new)} new template bundle(s) ready:\n"]
        for d in new[:8]:
            body_parts.append(f"\n--- {d.name} ---")
            body_parts.append(f"  Mockup: data/td_outputs/{d.name}/mockup.png")
            body_parts.append(f"  Brief: data/td_outputs/{d.name}/brief.md")
        body = "\n".join(body_parts) + "\n"
        r = mailer.send(AGENT_KEY, email,
                        f"Templates ready — {len(new)} new",
                        body, purpose="fulfillment")
        if r.get("status") == "sent":
            log[email] = list(already | {d.name for d in new})
            sent += 1
    storage.save("td_delivery_log.json", log)
    return {"fulfillment_sent": sent}


def acquire_cycle() -> dict:
    leads = storage.load("td_leads.json", [])
    sent = 0
    for lead in leads:
        if lead.get("trial_sent"):
            continue
        email = lead.get("email")
        if not email:
            continue
        body = (
            f"Hi {lead.get('name', 'there')},\n\n"
            f"TemplateForge ships a mockup + recreation brief for IG posts, stories, "
            f"quote cards, media kits, invoices, and product cards. You get the visual "
            f"reference + the precise spec (sizes, colors, copy, layout) so you can "
            f"rebuild it in Canva or Figma in under 10 minutes.\n\n"
            f"Send me 3 template ideas + your brand color and I'll send back the bundles — free first 3.\n\n"
            f"Pricing:\n"
            f"  $19 per template\n"
            f"  $49/mo for 5 templates\n"
            f"  $149/mo unlimited (great for content agencies)\n"
        )
        r = mailer.send(AGENT_KEY, email,
                        "Free template bundles (3 of your choice)",
                        body, purpose="outreach")
        if r.get("status") == "sent":
            lead["trial_sent"] = datetime.now().isoformat()
            sent += 1
    storage.save("td_leads.json", leads)
    return {"outreach_sent": sent}


def run_full_cycle() -> dict:
    q = build_queue()
    a = acquire_cycle()
    f = fulfill_cycle()
    rev = billing.revenue_summary(AGENT_KEY)
    subs = storage.load("td_subscribers.json", [])
    metrics.record(
        AGENT_KEY,
        products_produced=q["templates_produced"],
        outreach_sent=a["outreach_sent"],
        fulfillment_sent=f["fulfillment_sent"],
        active_subs=sum(1 for s in subs if s.get("status") == "active"),
        mrr=rev["mrr"],
        total_revenue=rev["total_paid"],
    )
    return {**q, **a, **f, **rev}
