"""
PlannerForge — downloadable PDF planner generator.
Revenue: $14/planner, $29/mo (3), $99/mo unlimited.

Owner manifest in data/pl_inputs/{slug}.json:
  {
    "type": "daily|weekly|monthly|habit|goals",
    "title": "May 2026 Daily Planner",
    "start_date": "2026-05-01",   # ISO date
    "weeks": 4,                    # how many weeks to generate
    "brand_color": "#FF4F2E",
    "page_size": "letter|a4"      # default letter (612x792 @72dpi)
  }

Output: data/pl_outputs/{slug}.pdf (multi-page) + meta.json.

Uses Pillow's native multi-page PDF save — no reportlab dep required.
Pages are rendered as images at 150 DPI then saved as a PDF book.
"""
import json
import sys
from datetime import datetime, timedelta, date
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).parent.parent))
from autonomous import storage, mailer, billing, metrics

AGENT_KEY = "plannerforge"
INPUTS_DIR = Path(__file__).parent.parent / "data" / "pl_inputs"
OUTPUTS_DIR = Path(__file__).parent.parent / "data" / "pl_outputs"

PAGE_SIZES = {
    "letter": (1275, 1650),    # 8.5 x 11 in @ 150 DPI
    "a4":     (1240, 1754),    # 8.27 x 11.69 in @ 150 DPI
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
    for p in (FONT_BOLD if bold else FONT_REG):
        if Path(p).exists():
            return ImageFont.truetype(p, size=size)
    return ImageFont.load_default()


def _hex(c: str, fallback: str = "#1A1815") -> str:
    c = (c or "").strip()
    if c.startswith("#") and len(c) in (4, 7):
        return c
    return fallback


def _header(d: ImageDraw.ImageDraw, w: int, title: str, sub: str, accent: str):
    d.rectangle([0, 0, w, 16], fill=accent)
    f_title = _font(56, bold=True)
    d.text((80, 60), title, font=f_title, fill="#1A1815")
    if sub:
        f_sub = _font(32, bold=False)
        d.text((80, 130), sub, font=f_sub, fill="#5C6470")


def _footer(d: ImageDraw.ImageDraw, w: int, h: int, page_no: int, total: int):
    f = _font(22, bold=False)
    txt = f"{page_no} / {total}"
    tw = f.getlength(txt)
    d.text(((w - tw) / 2, h - 60), txt, font=f, fill="#9AA3B2")


def _grid_box(d: ImageDraw.ImageDraw, x: int, y: int, w: int, h: int,
              accent: str, label: str):
    d.rectangle([x, y, x + w, y + h], outline="#1A1815", width=2)
    d.rectangle([x, y, x + w, y + 36], fill=accent)
    d.text((x + 12, y + 6), label, font=_font(22, bold=True), fill="#FFFFFF")


def render_daily_page(W: int, H: int, day: date, accent: str, total: int, page_no: int) -> Image.Image:
    img = Image.new("RGB", (W, H), "#FCFAF2")
    d = ImageDraw.Draw(img)
    _header(d, W, day.strftime("%A, %B %d, %Y"), day.strftime("Week %W · Day %j"), accent)

    margin = 80
    col_w = (W - margin * 3) // 2
    top_y = 220

    # Left column: priorities + schedule
    _grid_box(d, margin, top_y, col_w, 240, accent, "TOP 3 PRIORITIES")
    for i in range(1, 4):
        d.text((margin + 24, top_y + 80 + (i - 1) * 50),
               f"{i}.  ____________________________", font=_font(26), fill="#1A1815")

    sched_y = top_y + 280
    _grid_box(d, margin, sched_y, col_w, H - sched_y - 120, accent, "SCHEDULE")
    hours = 14
    avail = H - sched_y - 120 - 80
    row_h = avail // hours
    for i in range(hours):
        hour = 6 + i
        ty = sched_y + 80 + i * row_h
        d.line([margin + 80, ty, margin + col_w - 20, ty], fill="#9AA3B2", width=1)
        d.text((margin + 20, ty - 16), f"{hour:02d}:00", font=_font(18), fill="#5C6470")

    # Right column: notes + gratitude
    col2_x = margin * 2 + col_w
    _grid_box(d, col2_x, top_y, col_w, H - top_y - 280, accent, "NOTES")

    grat_y = H - 280 - 120
    _grid_box(d, col2_x, grat_y, col_w, 200, accent, "GRATITUDE / WINS")

    _footer(d, W, H, page_no, total)
    return img


def render_weekly_page(W: int, H: int, week_start: date, accent: str, total: int, page_no: int) -> Image.Image:
    img = Image.new("RGB", (W, H), "#FCFAF2")
    d = ImageDraw.Draw(img)
    title = f"Week of {week_start.strftime('%b %d')}"
    _header(d, W, title, "Plan · Execute · Reflect", accent)

    margin = 80
    top_y = 220
    full_w = W - margin * 2

    _grid_box(d, margin, top_y, full_w, 200, accent, "WEEK INTENTIONS")
    d.text((margin + 24, top_y + 70),
           "1.  ____________________________________________________________",
           font=_font(24), fill="#1A1815")
    d.text((margin + 24, top_y + 120),
           "2.  ____________________________________________________________",
           font=_font(24), fill="#1A1815")

    day_y = top_y + 240
    avail = H - day_y - 120
    row_h = avail // 7
    days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    for i, name in enumerate(days):
        ty = day_y + i * row_h
        d.rectangle([margin, ty, margin + full_w, ty + row_h - 8], outline="#1A1815", width=2)
        d.text((margin + 24, ty + 20), name, font=_font(28, bold=True), fill=accent)
        the_day = week_start + timedelta(days=i)
        d.text((margin + 24, ty + 60), the_day.strftime("%b %d"), font=_font(20), fill="#5C6470")
        d.line([margin + 220, ty + 40, margin + full_w - 20, ty + 40], fill="#9AA3B2", width=1)
        d.line([margin + 220, ty + 80, margin + full_w - 20, ty + 80], fill="#9AA3B2", width=1)
    _footer(d, W, H, page_no, total)
    return img


def render_habit_page(W: int, H: int, month: date, accent: str, total: int, page_no: int) -> Image.Image:
    img = Image.new("RGB", (W, H), "#FCFAF2")
    d = ImageDraw.Draw(img)
    _header(d, W, f"Habits — {month.strftime('%B %Y')}", "Tick boxes to build streaks.", accent)
    margin = 80
    top_y = 240
    label_w = 360
    days_in_month = 31
    cell_w = (W - margin * 2 - label_w) / days_in_month
    cell_h = 60
    rows = 8

    # Header row with day numbers
    for di in range(days_in_month):
        x = margin + label_w + di * cell_w
        d.text((x + 6, top_y - 30), str(di + 1), font=_font(16), fill="#5C6470")

    for r in range(rows):
        ry = top_y + r * cell_h
        d.rectangle([margin, ry, margin + label_w, ry + cell_h], outline="#1A1815", width=1)
        d.text((margin + 12, ry + 18), f"Habit {r+1}: __________", font=_font(22), fill="#1A1815")
        for di in range(days_in_month):
            x = margin + label_w + di * cell_w
            d.rectangle([x, ry, x + cell_w, ry + cell_h], outline="#9AA3B2", width=1)
    _footer(d, W, H, page_no, total)
    return img


def render_cover(W: int, H: int, title: str, accent: str, total_pages: int) -> Image.Image:
    img = Image.new("RGB", (W, H), accent)
    d = ImageDraw.Draw(img)
    f = _font(96, bold=True)
    lines = []
    cur = ""
    for word in title.split():
        if f.getlength(f"{cur} {word}") < W - 200:
            cur = f"{cur} {word}".strip()
        else:
            lines.append(cur)
            cur = word
    lines.append(cur)
    y = (H - len(lines) * int(f.size * 1.1)) / 2 - 100
    for line in lines:
        lw = f.getlength(line)
        d.text(((W - lw) / 2, y), line, font=f, fill="#FFFFFF")
        y += int(f.size * 1.1)
    sub = f"{total_pages} pages · Generated {datetime.now():%Y-%m-%d}"
    sf = _font(28, bold=False)
    sw = sf.getlength(sub)
    d.text(((W - sw) / 2, H - 200), sub, font=sf, fill="#FFFFFF")
    return img


def build_planner(spec: dict, slug: str) -> dict:
    ptype = spec.get("type", "daily")
    start = date.fromisoformat(spec.get("start_date", date.today().isoformat()))
    weeks = max(1, min(52, int(spec.get("weeks", 4))))
    accent = _hex(spec.get("brand_color"))
    title = spec.get("title") or f"{ptype.title()} Planner — {start.strftime('%b %Y')}"
    W, H = PAGE_SIZES.get(spec.get("page_size", "letter"), PAGE_SIZES["letter"])

    pages = []
    if ptype == "daily":
        total_days = weeks * 7
        # +1 for the cover page in numbering
        cover = render_cover(W, H, title, accent, total_days)
        pages.append(cover)
        for i in range(total_days):
            day = start + timedelta(days=i)
            pages.append(render_daily_page(W, H, day, accent, total_days + 1, i + 2))
    elif ptype == "weekly":
        cover = render_cover(W, H, title, accent, weeks)
        pages.append(cover)
        for i in range(weeks):
            ws = start + timedelta(weeks=i)
            pages.append(render_weekly_page(W, H, ws, accent, weeks + 1, i + 2))
    elif ptype == "habit":
        cover = render_cover(W, H, title, accent, weeks)
        pages.append(cover)
        # one habit page per month covered
        month = date(start.year, start.month, 1)
        months_needed = max(1, (weeks * 7) // 28)
        for i in range(months_needed):
            pages.append(render_habit_page(W, H, month, accent, months_needed + 1, i + 2))
            # advance one month
            if month.month == 12:
                month = date(month.year + 1, 1, 1)
            else:
                month = date(month.year, month.month + 1, 1)
    else:
        # goals / monthly fallback — single page weekly layout for now
        cover = render_cover(W, H, title, accent, 1)
        pages.append(cover)
        pages.append(render_weekly_page(W, H, start, accent, 2, 2))

    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    pdf_path = OUTPUTS_DIR / f"{slug}.pdf"
    pages[0].save(pdf_path, "PDF", resolution=150, save_all=True,
                  append_images=pages[1:])
    meta = {
        "slug": slug, "type": ptype, "title": title, "pages": len(pages),
        "page_size": spec.get("page_size", "letter"),
        "brand_color": accent, "built_at": datetime.now().isoformat(),
    }
    (OUTPUTS_DIR / f"{slug}.meta.json").write_text(json.dumps(meta, indent=2))
    return {"slug": slug, "pdf": str(pdf_path), "pages": len(pages)}


def build_queue() -> dict:
    INPUTS_DIR.mkdir(parents=True, exist_ok=True)
    produced = 0
    failed = 0
    for spec_path in sorted(INPUTS_DIR.glob("*.json")):
        slug = spec_path.stem
        if (OUTPUTS_DIR / f"{slug}.pdf").exists():
            continue
        try:
            spec = json.loads(spec_path.read_text())
        except Exception:
            failed += 1
            continue
        try:
            build_planner(spec, slug)
            produced += 1
        except Exception:
            failed += 1
    return {"planners_produced": produced, "failures": failed}


def fulfill_cycle() -> dict:
    subs = storage.load("pl_subscribers.json", [])
    log = storage.load("pl_delivery_log.json", {})
    sent = 0
    for sub in subs:
        if sub.get("status") != "active":
            continue
        email = sub.get("email")
        if not email:
            continue
        already = set(log.get(email, []))
        new = [p for p in OUTPUTS_DIR.glob("*.pdf") if p.name not in already]
        if not new:
            continue
        body_parts = [f"Hi {sub.get('name', 'there')},\n",
                      f"{len(new)} new planner(s) ready:\n"]
        for p in new[:10]:
            body_parts.append(f"  data/pl_outputs/{p.name}")
        body = "\n".join(body_parts) + "\n"
        r = mailer.send(AGENT_KEY, email,
                        f"Planners ready — {len(new)} new",
                        body, purpose="fulfillment")
        if r.get("status") == "sent":
            log[email] = list(already | {p.name for p in new})
            sent += 1
    storage.save("pl_delivery_log.json", log)
    return {"fulfillment_sent": sent}


def acquire_cycle() -> dict:
    leads = storage.load("pl_leads.json", [])
    sent = 0
    for lead in leads:
        if lead.get("trial_sent"):
            continue
        email = lead.get("email")
        if not email:
            continue
        body = (
            f"Hi {lead.get('name', 'there')},\n\n"
            f"PlannerForge generates printable + tablet-friendly PDF planners — daily, "
            f"weekly, monthly, habit, goals — with your brand color and date range.\n\n"
            f"Send me a type + a start date and you'll get a 4-week sample PDF back, free.\n\n"
            f"Pricing:\n"
            f"  $14 per planner\n"
            f"  $29/mo for 3 planners\n"
            f"  $99/mo unlimited (white-label rights)\n"
        )
        r = mailer.send(AGENT_KEY, email,
                        "Free 4-week planner PDF (printable + tablet)",
                        body, purpose="outreach")
        if r.get("status") == "sent":
            lead["trial_sent"] = datetime.now().isoformat()
            sent += 1
    storage.save("pl_leads.json", leads)
    return {"outreach_sent": sent}


def run_full_cycle() -> dict:
    q = build_queue()
    a = acquire_cycle()
    f = fulfill_cycle()
    rev = billing.revenue_summary(AGENT_KEY)
    subs = storage.load("pl_subscribers.json", [])
    metrics.record(
        AGENT_KEY,
        products_produced=q["planners_produced"],
        outreach_sent=a["outreach_sent"],
        fulfillment_sent=f["fulfillment_sent"],
        active_subs=sum(1 for s in subs if s.get("status") == "active"),
        mrr=rev["mrr"],
        total_revenue=rev["total_paid"],
    )
    return {**q, **a, **f, **rev}
