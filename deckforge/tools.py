"""
DeckForge — pitch deck generator (browser-native, prints to PDF cleanly).
Revenue: $49/deck, $149/mo (5), $497 founder fundraising package.

Output is a single self-contained `deck.html` (Reveal.js inlined from CDN
via a single <script src>) — no python-pptx dep required. Founders open
it in Chrome and either present from the browser or print → save as PDF
for emailing to investors.

Owner manifest in data/dk_inputs/{slug}.json:
  {
    "company": "Acme Coffee",
    "tagline": "The best espresso in the Midwest",
    "theme": "dark|light|minimal",
    "accent": "#FFB300",
    "slides": [
      {"type": "title",   "heading": "Acme Coffee", "body": "Series A · 2026"},
      {"type": "section", "heading": "The problem", "body": "..."},
      {"type": "bullets", "heading": "What we do",  "bullets": ["...","..."]},
      {"type": "stat",    "heading": "$2M ARR",     "body": "Up 4× YoY"},
      {"type": "team",    "heading": "Team",         "bullets": ["CEO — ex-Starbucks","CTO — ex-Square"]},
      {"type": "ask",     "heading": "Raising $3M", "body": "Seed extension. 18-month runway."}
    ]
  }

Output:
  data/dk_outputs/{slug}/deck.html
  data/dk_outputs/{slug}/spec.json
"""
import json
import sys
from datetime import datetime
from html import escape
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from autonomous import storage, mailer, billing, metrics
from deckforge import health

AGENT_KEY = "deckforge"
INPUTS_DIR = Path(__file__).parent.parent / "data" / "dk_inputs"
OUTPUTS_DIR = Path(__file__).parent.parent / "data" / "dk_outputs"

THEMES = {
    "dark":    {"bg": "#0F1115", "fg": "#FFFFFF", "muted": "#9AA3B2", "card": "#1A1D24"},
    "light":   {"bg": "#FCFAF2", "fg": "#0F1115", "muted": "#5C6470", "card": "#FFFFFF"},
    "minimal": {"bg": "#FFFFFF", "fg": "#000000", "muted": "#555555", "card": "#F2F2F2"},
}


def _hex(c: str, fallback: str = "#FF4F2E") -> str:
    c = (c or "").strip()
    return c if c.startswith("#") and len(c) in (4, 7) else fallback


def _slide_html(slide: dict, theme: dict, accent: str) -> str:
    t = slide.get("type", "section")
    heading = escape(slide.get("heading", ""))
    body = escape(slide.get("body", ""))
    bullets = slide.get("bullets", [])

    if t == "title":
        return f"""<section class="title-slide">
  <h1 style="color:{theme['fg']}">{heading}</h1>
  {f'<p class="muted">{body}</p>' if body else ''}
</section>"""

    if t == "stat":
        return f"""<section class="stat-slide">
  <div class="stat-num" style="color:{accent}">{heading}</div>
  {f'<p class="muted">{body}</p>' if body else ''}
</section>"""

    if t == "bullets" or t == "team":
        items = "".join(f'<li>{escape(b)}</li>' for b in bullets if b)
        return f"""<section>
  <h2>{heading}</h2>
  <ul class="bullets">{items}</ul>
</section>"""

    if t == "ask":
        return f"""<section class="ask-slide">
  <h2 style="color:{accent}">{heading}</h2>
  <p class="muted">{body}</p>
</section>"""

    # section / default
    return f"""<section>
  <h2>{heading}</h2>
  {f'<p>{body}</p>' if body else ''}
</section>"""


def render_deck(spec: dict) -> str:
    theme = THEMES.get(spec.get("theme", "dark"), THEMES["dark"])
    accent = _hex(spec.get("accent"))
    company = escape(spec.get("company", "Untitled"))
    tagline = escape(spec.get("tagline", ""))
    slides = spec.get("slides", [])
    slides_html = "\n".join(_slide_html(s, theme, accent) for s in slides) or \
        f'<section><h2>{company}</h2><p class="muted">{tagline}</p></section>'

    css = f"""
    html, body {{ background: {theme['bg']}; color: {theme['fg']}; }}
    .reveal {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }}
    .reveal section {{ text-align: left; }}
    .reveal h1 {{ font-size: 4.2em; letter-spacing: -0.02em; margin-bottom: 0.3em; color: {theme['fg']}; }}
    .reveal h2 {{ font-size: 2.4em; letter-spacing: -0.01em; margin-bottom: 0.5em; color: {accent}; }}
    .reveal p {{ font-size: 1.4em; line-height: 1.5; color: {theme['fg']}; }}
    .reveal .muted {{ color: {theme['muted']}; }}
    .reveal ul.bullets {{ list-style: none; padding: 0; }}
    .reveal ul.bullets li {{ font-size: 1.4em; line-height: 1.6; padding: 12px 0; padding-left: 36px; position: relative; }}
    .reveal ul.bullets li::before {{ content: ""; position: absolute; left: 0; top: 26px; width: 18px; height: 4px; background: {accent}; }}
    .reveal .title-slide, .reveal .stat-slide, .reveal .ask-slide {{ text-align: center; }}
    .reveal .stat-num {{ font-size: 6em; font-weight: 800; margin-bottom: 0.2em; }}
    .reveal .progress {{ color: {accent}; }}
    """

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{company}{(' — ' + tagline) if tagline else ''}</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/reveal.js@5/dist/reveal.css">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/reveal.js@5/dist/theme/black.css">
<style>{css}</style>
</head>
<body>
<div class="reveal"><div class="slides">
{slides_html}
</div></div>
<script src="https://cdn.jsdelivr.net/npm/reveal.js@5/dist/reveal.js"></script>
<script>
Reveal.initialize({{
  hash: true, controls: true, progress: true, center: true,
  transition: 'fade',
  width: 1280, height: 720, margin: 0.08,
}});
</script>
</body>
</html>"""


def build_deck(spec: dict, slug: str) -> dict:
    out_dir = OUTPUTS_DIR / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "deck.html").write_text(render_deck(spec))
    (out_dir / "spec.json").write_text(json.dumps({
        "slug": slug,
        "company": spec.get("company"),
        "slide_count": len(spec.get("slides", [])),
        "theme": spec.get("theme", "dark"),
        "built_at": datetime.now().isoformat(),
    }, indent=2))
    return {"slug": slug, "out_dir": str(out_dir), "slides": len(spec.get("slides", []))}


def build_queue() -> dict:
    INPUTS_DIR.mkdir(parents=True, exist_ok=True)
    produced = 0
    failed = 0
    for spec_path in sorted(INPUTS_DIR.glob("*.json")):
        slug = spec_path.stem
        if (OUTPUTS_DIR / slug / "deck.html").exists():
            continue
        try:
            spec = json.loads(spec_path.read_text())
        except Exception:
            failed += 1
            continue
        build_deck(spec, slug)
        produced += 1
    return {"decks_produced": produced, "failures": failed}


def fulfill_cycle() -> dict:
    subs = storage.load("dk_subscribers.json", [])
    log = storage.load("dk_delivery_log.json", {})
    sent = 0
    for sub in subs:
        if sub.get("status") != "active":
            continue
        email = sub.get("email")
        if not email:
            continue
        already = set(log.get(email, []))
        new = [d for d in OUTPUTS_DIR.iterdir()
               if d.is_dir() and d.name not in already and (d / "deck.html").exists()]
        if not new:
            continue
        body_parts = [f"Hi {sub.get('name', 'there')},\n",
                      f"{len(new)} new deck(s) ready:\n"]
        for d in new[:5]:
            body_parts.append(f"\n--- {d.name} ---")
            body_parts.append(f"  data/dk_outputs/{d.name}/deck.html")
            body_parts.append(f"  Open in Chrome → present or print → save as PDF.")
        body = "\n".join(body_parts) + "\n"
        r = mailer.send(AGENT_KEY, email,
                        f"Decks ready — {len(new)} new",
                        body, purpose="fulfillment")
        if r.get("status") == "sent":
            log[email] = list(already | {d.name for d in new})
            sent += 1
    storage.save("dk_delivery_log.json", log)
    return {"fulfillment_sent": sent}


def acquire_cycle() -> dict:
    leads = storage.load("dk_leads.json", [])
    sent = 0
    for lead in leads:
        if lead.get("trial_sent"):
            continue
        email = lead.get("email")
        if not email:
            continue
        body = (
            f"Hi {lead.get('name', 'there')},\n\n"
            f"DeckForge takes a pitch outline and ships a polished slide deck (browser-native, "
            f"prints to PDF for investor emails) — no Keynote, no PowerPoint, no Figma needed.\n\n"
            f"Send me your company, the problem you solve, traction numbers, and the ask. "
            f"You'll get a 10-slide deck back within 24 hours — first one free.\n\n"
            f"Pricing:\n"
            f"  $49 per deck\n"
            f"  $149/mo for 5 decks\n"
            f"  $497 founder fundraising package (deck + revisions + 30-min strategy call)\n"
        )
        r = mailer.send(AGENT_KEY, email,
                        "Free pitch deck for your raise",
                        body, purpose="outreach")
        if r.get("status") == "sent":
            lead["trial_sent"] = datetime.now().isoformat()
            sent += 1
    storage.save("dk_leads.json", leads)
    return {"outreach_sent": sent}


def run_full_cycle() -> dict:
    q = build_queue()
    a = acquire_cycle()
    f = fulfill_cycle()
    rev = billing.revenue_summary(AGENT_KEY)
    subs = storage.load("dk_subscribers.json", [])
    metrics.record(
        AGENT_KEY,
        products_produced=q["decks_produced"],
        outreach_sent=a["outreach_sent"],
        fulfillment_sent=f["fulfillment_sent"],
        active_subs=sum(1 for s in subs if s.get("status") == "active"),
        mrr=rev["mrr"],
        total_revenue=rev["total_paid"],
    )
    return {**q, **a, **f, **rev}
