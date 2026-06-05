"""
BentoForge — link-in-bio landing page generator.
Revenue: $19 one-time, $9/mo hosting+updates, $49 white-label pack.

Owner-dropped manifest in data/bf_inputs/{slug}.json:
  {
    "creator_name": "Jane Doe",
    "handle": "@janedoe",
    "bio": "Designer building tools for indie hackers.",
    "niche": "design",
    "theme": "dark|light|sunset|forest|neon|paper",
    "avatar_url": "https://...",
    "links": [
      {"label": "Latest course", "url": "https://...", "emoji": "🎓"},
      {"label": "Newsletter",    "url": "https://...", "emoji": "📬"}
    ]
  }

Output (fully self-contained, no external deps at runtime):
  data/bf_outputs/{slug}/index.html
  data/bf_outputs/{slug}/meta.json

The HTML inlines all CSS, uses system fonts, scales mobile-first, and works
when dropped onto Netlify Drop / GitHub Pages / Cloudflare Pages / a USB stick.
"""
import json
import sys
from datetime import datetime
from html import escape
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from autonomous import storage, mailer, billing, metrics
from bentoforge import health

AGENT_KEY = "bentoforge"
INPUTS_DIR = Path(__file__).parent.parent / "data" / "bf_inputs"
OUTPUTS_DIR = Path(__file__).parent.parent / "data" / "bf_outputs"

THEMES = {
    "dark":    {"bg": "#0E0F13", "card": "#1A1D24", "fg": "#FFFFFF", "muted": "#9AA3B2", "accent": "#FFD24A", "hover": "#FFFFFF12"},
    "light":   {"bg": "#F6F4EE", "card": "#FFFFFF", "fg": "#0F1115", "muted": "#5C6470", "accent": "#FF4F2E", "hover": "#0000000A"},
    "sunset":  {"bg": "linear-gradient(180deg,#1A0F2E 0%,#3B1E3A 50%,#A24A4A 100%)", "card": "#22142E", "fg": "#FFE8D6", "muted": "#D7B7A8", "accent": "#FFB36B", "hover": "#FFFFFF14"},
    "forest":  {"bg": "linear-gradient(180deg,#0A1F1A 0%,#143A2D 100%)", "card": "#0F2A22", "fg": "#E8F5EC", "muted": "#9CC4B0", "accent": "#A5D6A7", "hover": "#FFFFFF12"},
    "neon":    {"bg": "#05060A", "card": "#0D0E18", "fg": "#E8F1FF", "muted": "#7F8AB0", "accent": "#00E5FF", "hover": "#00E5FF1A"},
    "paper":   {"bg": "#FCFAF2", "card": "#FFFFFF", "fg": "#1A1815", "muted": "#6B6555", "accent": "#1A1815", "hover": "#0000000C"},
}


def _render_html(spec: dict) -> str:
    theme_key = spec.get("theme", "dark")
    t = THEMES.get(theme_key, THEMES["dark"])
    name = escape(spec.get("creator_name", "Untitled"))
    handle = escape(spec.get("handle", ""))
    bio = escape(spec.get("bio", ""))
    avatar = spec.get("avatar_url", "")
    links = spec.get("links", [])

    avatar_html = ""
    if avatar:
        avatar_html = f'<img class="avatar" src="{escape(avatar)}" alt="{name}" loading="lazy">'
    else:
        initials = "".join(p[0] for p in name.split()[:2]).upper() or "?"
        avatar_html = f'<div class="avatar avatar-fallback">{escape(initials)}</div>'

    link_items = []
    for link in links:
        label = escape(link.get("label", ""))
        url = escape(link.get("url", "#"))
        emoji = escape(link.get("emoji", "→"))
        if not label:
            continue
        link_items.append(
            f'<a class="link" href="{url}" target="_blank" rel="noopener noreferrer">'
            f'<span class="link-emoji" aria-hidden="true">{emoji}</span>'
            f'<span class="link-label">{label}</span>'
            f'</a>'
        )

    css = f"""
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    html, body {{
      min-height: 100vh;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", sans-serif;
      background: {t['bg']};
      color: {t['fg']};
      -webkit-font-smoothing: antialiased;
    }}
    .wrap {{
      max-width: 520px;
      margin: 0 auto;
      padding: 56px 20px 80px;
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 28px;
    }}
    .avatar {{
      width: 96px; height: 96px;
      border-radius: 50%;
      object-fit: cover;
      border: 3px solid {t['accent']};
    }}
    .avatar-fallback {{
      display: flex; align-items: center; justify-content: center;
      background: {t['card']};
      color: {t['accent']};
      font-size: 36px; font-weight: 700;
    }}
    h1 {{ font-size: 24px; font-weight: 700; letter-spacing: -0.01em; text-align: center; }}
    .handle {{ color: {t['muted']}; font-size: 15px; }}
    .bio {{ color: {t['muted']}; font-size: 15px; line-height: 1.5; text-align: center; max-width: 380px; }}
    .links {{ display: flex; flex-direction: column; gap: 12px; width: 100%; margin-top: 8px; }}
    .link {{
      display: flex; align-items: center; gap: 12px;
      padding: 18px 20px;
      background: {t['card']};
      color: {t['fg']};
      text-decoration: none;
      border-radius: 14px;
      font-weight: 600; font-size: 16px;
      transition: transform 100ms ease, background 100ms ease;
      border: 1px solid {t['hover']};
    }}
    .link:hover {{ transform: translateY(-1px); background: {t['hover']}; border-color: {t['accent']}55; }}
    .link-emoji {{ font-size: 20px; }}
    .link-label {{ flex: 1; }}
    footer {{ margin-top: 24px; color: {t['muted']}; font-size: 12px; text-align: center; }}
    footer a {{ color: {t['accent']}; text-decoration: none; }}
    """

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{name}{(' · ' + handle) if handle else ''}</title>
<meta name="description" content="{bio}">
<style>{css}</style>
</head>
<body>
<main class="wrap">
  {avatar_html}
  <div style="text-align:center; display:flex; flex-direction:column; gap:6px;">
    <h1>{name}</h1>
    {f'<div class="handle">{handle}</div>' if handle else ''}
  </div>
  {f'<p class="bio">{bio}</p>' if bio else ''}
  <nav class="links" aria-label="Links">
    {''.join(link_items) or '<p style="color:' + t['muted'] + ';text-align:center">No links yet.</p>'}
  </nav>
  <footer>Made with <a href="https://wholesaleomniverse.com">BentoForge</a></footer>
</main>
</body>
</html>"""


def build_page(spec: dict, slug: str) -> dict:
    out_dir = OUTPUTS_DIR / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "index.html").write_text(_render_html(spec))
    meta = {
        "slug": slug,
        "creator_name": spec.get("creator_name"),
        "handle": spec.get("handle"),
        "theme": spec.get("theme", "dark"),
        "link_count": len(spec.get("links", [])),
        "built_at": datetime.now().isoformat(),
    }
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=2))
    return {"slug": slug, "out_dir": str(out_dir), "link_count": meta["link_count"]}


def build_queue() -> dict:
    INPUTS_DIR.mkdir(parents=True, exist_ok=True)
    produced = 0
    failed = 0
    for spec_path in sorted(INPUTS_DIR.glob("*.json")):
        slug = spec_path.stem
        if (OUTPUTS_DIR / slug / "index.html").exists():
            continue
        try:
            spec = json.loads(spec_path.read_text())
        except Exception as e:
            failed += 1
            health.record_page(slug, "spec_invalid",
                               detail=f"{type(e).__name__}: {str(e)[:60]}")
            continue
        if not spec.get("links"):
            health.record_page(slug, "no_links", theme=spec.get("theme", ""))
            failed += 1
            continue
        try:
            r = build_page(spec, slug)
            produced += 1
            health.record_page(slug, "success", theme=spec.get("theme", "dark"),
                               link_count=r.get("link_count", 0))
        except Exception as e:
            failed += 1
            health.record_page(slug, "build_failed", theme=spec.get("theme", ""),
                               detail=f"{type(e).__name__}: {str(e)[:80]}")
    return {"pages_produced": produced, "failures": failed}


def fulfill_cycle() -> dict:
    subs = storage.load("bf_subscribers.json", [])
    log = storage.load("bf_delivery_log.json", {})
    sent = 0
    for sub in subs:
        if sub.get("status") != "active":
            continue
        email = sub.get("email")
        if not email:
            continue
        already = set(log.get(email, []))
        new = [d for d in OUTPUTS_DIR.iterdir()
               if d.is_dir() and d.name not in already and (d / "index.html").exists()]
        if not new:
            continue
        body_parts = [f"Hi {sub.get('name', 'there')},\n",
                      f"{len(new)} new link-in-bio page(s) ready:\n"]
        for d in new[:5]:
            body_parts.append(f"\n--- {d.name} ---")
            body_parts.append(f"  data/bf_outputs/{d.name}/index.html")
            body_parts.append("  Upload to: Netlify Drop, GitHub Pages, or Cloudflare Pages.")
        body = "\n".join(body_parts) + "\n"
        r = mailer.send(AGENT_KEY, email,
                        f"BentoForge pages ready — {len(new)} new",
                        body, purpose="fulfillment")
        if r.get("status") != "sent":
            health.record_delivery(email, "mail_failed", slugs=len(new),
                                   detail=f"mailer={r.get('status','?')}: "
                                          f"{(r.get('reason') or r.get('error',''))[:80]}")
        else:
            health.record_delivery(email, "success", slugs=len(new))
        if r.get("status") == "sent":
            log[email] = list(already | {d.name for d in new})
            sent += 1
    storage.save("bf_delivery_log.json", log)
    return {"fulfillment_sent": sent}


def acquire_cycle() -> dict:
    leads = storage.load("bf_leads.json", [])
    sent = 0
    for lead in leads:
        if lead.get("trial_sent"):
            continue
        email = lead.get("email")
        if not email:
            continue
        body = (
            f"Hi {lead.get('name', 'there')},\n\n"
            f"I run an automated link-in-bio service. Send me your name, bio, 4–8 links, "
            f"and a preferred theme (dark/light/sunset/forest/neon/paper) and you'll get back "
            f"a single self-contained HTML file ready to drop onto any free host.\n\n"
            f"First page free. After that:\n"
            f"  $19 one-time per page\n"
            f"  $9/mo for unlimited updates + hosting on our subdomain\n"
            f"  $49 white-label pack (5 pages for an agency client)\n\n"
            f"Reply with your name + 4 links and I'll send the page within 24 hours.\n"
        )
        r = mailer.send(AGENT_KEY, email,
                        "Free link-in-bio page (sleek, mobile-first)",
                        body, purpose="outreach")
        if r.get("status") == "sent":
            lead["trial_sent"] = datetime.now().isoformat()
            sent += 1
    storage.save("bf_leads.json", leads)
    return {"outreach_sent": sent}


def run_full_cycle() -> dict:
    q = build_queue()
    a = acquire_cycle()
    f = fulfill_cycle()
    rev = billing.revenue_summary(AGENT_KEY)
    subs = storage.load("bf_subscribers.json", [])
    metrics.record(
        AGENT_KEY,
        products_produced=q["pages_produced"],
        outreach_sent=a["outreach_sent"],
        fulfillment_sent=f["fulfillment_sent"],
        active_subs=sum(1 for s in subs if s.get("status") == "active"),
        mrr=rev["mrr"],
        total_revenue=rev["total_paid"],
    )
    return {**q, **a, **f, **rev}
