"""
ThumbForge — autonomous YouTube / Shorts thumbnail designer.
Revenue: $9/thumbnail, $49/mo (10 thumbnails), $199 bulk 30-pack.

Owner workflow:
  Drop input JSON files into data/tf_inputs/{slug}.json with shape:
    {
      "title": "DO THIS BEFORE 7AM",
      "subtitle": "optional kicker",
      "niche": "motivational|tech|wellness|comedy|finance|generic",
      "accent": "#FFB300",                   # optional hex override
      "shorts": true                          # also output a 1080x1920 vertical
    }
  Agent renders {slug}.png (1280x720) and optionally {slug}.shorts.png.

Niche palettes are tuned for click-through: high-contrast text + strong accent
bar + tag pill. Owner can override any colour with hex strings.
"""
import json
import sys
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).parent.parent))
from autonomous import storage, mailer, billing, metrics
from thumbforge import health

AGENT_KEY = "thumbforge"
INPUTS_DIR = Path(__file__).parent.parent / "data" / "tf_inputs"
OUTPUTS_DIR = Path(__file__).parent.parent / "data" / "tf_outputs"

YOUTUBE_SIZE = (1280, 720)
SHORTS_SIZE = (1080, 1920)

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
]

NICHE_PALETTES = {
    "motivational": {"bg": "#0B0B0B", "text": "#FFFFFF", "accent": "#FFB300", "stroke": "#000000", "tag_bg": "#E53935"},
    "tech":         {"bg": "#0A1A2E", "text": "#FFFFFF", "accent": "#00E5FF", "stroke": "#000000", "tag_bg": "#1565C0"},
    "wellness":     {"bg": "#1B3A2F", "text": "#FFFFFF", "accent": "#A5D6A7", "stroke": "#000000", "tag_bg": "#2E7D32"},
    "comedy":       {"bg": "#2A0E5A", "text": "#FFFFFF", "accent": "#FFEB3B", "stroke": "#000000", "tag_bg": "#7B1FA2"},
    "finance":      {"bg": "#0E2722", "text": "#FFFFFF", "accent": "#00C853", "stroke": "#000000", "tag_bg": "#1B5E20"},
    "generic":      {"bg": "#111111", "text": "#FFFFFF", "accent": "#FF5722", "stroke": "#000000", "tag_bg": "#424242"},
}


def _font(size: int) -> ImageFont.FreeTypeFont:
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            return ImageFont.truetype(path, size=size)
    return ImageFont.load_default()


def _wrap_text(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list:
    words = text.split()
    if not words:
        return []
    lines, cur = [], words[0]
    for w in words[1:]:
        test = f"{cur} {w}"
        if font.getlength(test) <= max_width:
            cur = test
        else:
            lines.append(cur)
            cur = w
    lines.append(cur)
    return lines


def _fit_text(text: str, max_width: int, max_height: int,
              start_size: int, min_size: int) -> tuple:
    """Return (lines, font) that fit within the box, shrinking text until it does."""
    size = start_size
    while size >= min_size:
        font = _font(size)
        lines = _wrap_text(text, font, max_width)
        line_h = int(size * 1.1)
        if line_h * len(lines) <= max_height and all(font.getlength(l) <= max_width for l in lines):
            return lines, font
        size -= 4
    font = _font(min_size)
    return _wrap_text(text, font, max_width), font


def _resolve_palette(niche: str, accent_override: str = "") -> dict:
    pal = dict(NICHE_PALETTES.get(niche, NICHE_PALETTES["generic"]))
    if accent_override:
        pal["accent"] = accent_override
    return pal


def render_thumbnail(spec: dict, size: tuple, out_path: Path) -> dict:
    w, h = size
    niche = spec.get("niche", "generic")
    palette = _resolve_palette(niche, spec.get("accent", ""))
    title = (spec.get("title") or "UNTITLED").upper()
    subtitle = spec.get("subtitle", "")

    img = Image.new("RGB", size, palette["bg"])
    draw = ImageDraw.Draw(img)

    # Accent bar — vertical on the left, sized as a visual anchor.
    bar_w = max(12, w // 90)
    draw.rectangle([0, 0, bar_w, h], fill=palette["accent"])

    # Diagonal accent stripe at top-right for visual energy.
    stripe_pts = [(w, 0), (w, h // 4), (w - h // 4, 0)]
    draw.polygon(stripe_pts, fill=palette["accent"])

    # Title — main hook, occupies upper-center 70% of frame.
    pad = int(w * 0.06)
    title_box_w = w - 2 * pad - bar_w
    title_box_h = int(h * 0.6)
    start_size = max(120, h // 5)
    min_size = max(40, h // 16)
    lines, font = _fit_text(title, title_box_w, title_box_h, start_size, min_size)
    line_h = int(font.size * 1.1)
    block_h = line_h * len(lines)
    y0 = (h - block_h) // 2 - int(h * 0.05)
    for i, line in enumerate(lines):
        line_w = font.getlength(line)
        x = bar_w + pad + (title_box_w - line_w) / 2
        y = y0 + i * line_h
        stroke = max(3, font.size // 22)
        draw.text((x, y), line, font=font, fill=palette["text"],
                  stroke_width=stroke, stroke_fill=palette["stroke"])

    # Subtitle pill — small badge under the title.
    if subtitle:
        sub_font = _font(max(28, h // 22))
        sub_text = subtitle.upper()
        tw = sub_font.getlength(sub_text)
        ph_pad_x = 24
        ph_pad_y = 12
        pill_w = tw + 2 * ph_pad_x
        pill_h = sub_font.size + 2 * ph_pad_y
        pill_x = (w - pill_w) / 2
        pill_y = y0 + block_h + int(h * 0.03)
        draw.rounded_rectangle([pill_x, pill_y, pill_x + pill_w, pill_y + pill_h],
                               radius=pill_h // 2, fill=palette["tag_bg"])
        draw.text((pill_x + ph_pad_x, pill_y + ph_pad_y), sub_text,
                  font=sub_font, fill=palette["text"])

    # Niche tag (corner) — branding.
    tag_font = _font(max(22, h // 30))
    tag = niche.upper()
    tw = tag_font.getlength(tag)
    tag_pad = 14
    tag_w = tw + 2 * tag_pad
    tag_h = tag_font.size + 2 * tag_pad
    draw.rounded_rectangle(
        [w - tag_w - 20, h - tag_h - 20, w - 20, h - 20],
        radius=10, fill=palette["accent"])
    draw.text((w - tag_w - 20 + tag_pad, h - tag_h - 20 + tag_pad),
              tag, font=tag_font, fill="#000000")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, "PNG", optimize=True)
    return {"path": str(out_path), "size": size, "niche": niche}


def process_input(spec_path: Path) -> dict:
    slug = spec_path.stem
    try:
        spec = json.loads(spec_path.read_text())
    except Exception as e:
        return {"slug": slug, "error": f"bad json: {e}"}
    yt_path = OUTPUTS_DIR / f"{slug}.png"
    results = {"slug": slug, "outputs": []}
    if not yt_path.exists():
        r = render_thumbnail(spec, YOUTUBE_SIZE, yt_path)
        results["outputs"].append(r["path"])
    if spec.get("shorts"):
        sh_path = OUTPUTS_DIR / f"{slug}.shorts.png"
        if not sh_path.exists():
            r = render_thumbnail(spec, SHORTS_SIZE, sh_path)
            results["outputs"].append(r["path"])
    return results


def build_queue() -> dict:
    INPUTS_DIR.mkdir(parents=True, exist_ok=True)
    produced = 0
    failed = 0
    for spec_path in sorted(INPUTS_DIR.glob("*.json")):
        r = process_input(spec_path)
        if r.get("outputs"):
            produced += len(r["outputs"])
        elif "error" in r:
            failed += 1
    return {"thumbnails_produced": produced, "failures": failed}


def fulfill_cycle() -> dict:
    subs = storage.load("tf_subscribers.json", [])
    log = storage.load("tf_delivery_log.json", {})
    sent = 0
    for sub in subs:
        if sub.get("status") != "active":
            continue
        email = sub.get("email")
        if not email:
            continue
        already = set(log.get(email, []))
        new_files = [p for p in OUTPUTS_DIR.glob("*.png") if p.name not in already]
        if not new_files:
            continue
        body = (f"Hi {sub.get('name', 'there')},\n\n"
                f"{len(new_files)} new thumbnail(s) ready:\n\n"
                + "\n".join(f"  data/tf_outputs/{p.name}" for p in new_files[:20])
                + "\n\nDownload from the outputs directory; usage is unlimited.\n")
        r = mailer.send(AGENT_KEY, email,
                        f"Thumbnails ready — {len(new_files)} new",
                        body, purpose="fulfillment")
        if r.get("status") == "sent":
            log[email] = list(already | {p.name for p in new_files})
            sent += 1
    storage.save("tf_delivery_log.json", log)
    return {"fulfillment_sent": sent}


def acquire_cycle() -> dict:
    leads = storage.load("tf_leads.json", [])
    sample = ""
    samples = sorted(OUTPUTS_DIR.glob("*.png"))
    if samples:
        sample = f"\nSample available — reply and I'll send: {samples[0].name}\n"
    sent = 0
    for lead in leads:
        if lead.get("trial_sent"):
            continue
        email = lead.get("email")
        if not email:
            continue
        body = (
            f"Hi {lead.get('name', 'there')},\n\n"
            f"I run an automated thumbnail design service for YouTubers and short-form creators.\n"
            f"Send me your next video title and you'll get back a CTR-tuned thumbnail within 24 hours — free first one.\n\n"
            f"Pricing after the trial:\n"
            f"  $9 per thumbnail (one-off)\n"
            f"  $49/mo for 10 thumbnails\n"
            f"  $199 for a 30-pack bulk\n"
            f"{sample}\n"
            f"Reply with a title + niche (motivational, tech, wellness, comedy, finance) and I'll send the thumbnail back.\n"
        )
        r = mailer.send(AGENT_KEY, email,
                        "Free CTR-tuned thumbnail for your next video",
                        body, purpose="outreach")
        if r.get("status") == "sent":
            lead["trial_sent"] = datetime.now().isoformat()
            sent += 1
    storage.save("tf_leads.json", leads)
    return {"outreach_sent": sent}


def run_full_cycle() -> dict:
    q = build_queue()
    a = acquire_cycle()
    f = fulfill_cycle()
    rev = billing.revenue_summary(AGENT_KEY)
    subs = storage.load("tf_subscribers.json", [])
    metrics.record(
        AGENT_KEY,
        products_produced=q["thumbnails_produced"],
        outreach_sent=a["outreach_sent"],
        fulfillment_sent=f["fulfillment_sent"],
        active_subs=sum(1 for s in subs if s.get("status") == "active"),
        mrr=rev["mrr"],
        total_revenue=rev["total_paid"],
    )
    return {**q, **a, **f, **rev}
