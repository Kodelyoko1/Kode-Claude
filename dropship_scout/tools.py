"""
DropshipScout — Weekly digest of viral TikTok-shop products + Amazon Movers & Shakers.
Revenue: $47/mo subscription. Free tier shows top 3; paid tier gets the full digest +
historical trend graphs + supplier links.

Sources (all public, no API keys required):
- TikTok Creative Center — trending hashtags + products
  https://ads.tiktok.com/business/creativecenter/inspiration/popular/hashtag/pc/en
- Amazon Movers & Shakers — real-time top-100 sales rank gainers per category
  https://www.amazon.com/gp/movers-and-shakers
"""
import re
import sys
import json
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).parent.parent))
from autonomous import storage, mailer, billing, metrics

AGENT_KEY = "dropship_scout"
DIGESTS_DIR = Path(__file__).parent.parent / "data" / "ds_digests"
WEBSITE_OUT = Path(__file__).parent.parent / "website" / "dropship_scout_trends.html"

# Categories Amazon Movers & Shakers exposes that map well to TikTok shop dropship niches.
AMAZON_MOVERS_CATEGORIES = [
    ("beauty",        "https://www.amazon.com/gp/movers-and-shakers/beauty"),
    ("home-kitchen",  "https://www.amazon.com/gp/movers-and-shakers/home-garden"),
    ("toys",          "https://www.amazon.com/gp/movers-and-shakers/toys-and-games"),
    ("fashion",       "https://www.amazon.com/gp/movers-and-shakers/fashion"),
    ("pet-supplies",  "https://www.amazon.com/gp/movers-and-shakers/pet-supplies"),
]

TIKTOK_TRENDS_URL = "https://ads.tiktok.com/business/creativecenter/inspiration/popular/hashtag/pc/en"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 DropshipScout/1.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def _get(url: str, timeout: int = 15) -> str:
    try:
        import requests
        resp = requests.get(url, headers=HEADERS, timeout=timeout)
        if resp.status_code == 200:
            return resp.text
    except Exception:
        pass
    return ""


def scrape_amazon_movers(category: str, url: str, limit: int = 10) -> list:
    """Pull top sales-rank gainers from a Movers & Shakers category page."""
    html = _get(url)
    if not html:
        return []
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return []
    soup = BeautifulSoup(html, "html.parser")
    products = []
    # Amazon uses a few card patterns; this targets the canonical M&S list.
    for item in soup.select("div.zg-item-immersion, li.zg-item-immersion, div[id^='gridItemRoot']")[:limit]:
        title_el = item.select_one("a div div, div.p13n-sc-truncate, span.zg-text-center-align div")
        link_el = item.select_one("a.a-link-normal[href*='/dp/']") or item.select_one("a[href*='/dp/']")
        rank_el = item.select_one("span.zg-rank, .zg-bdg-text")
        if not title_el or not link_el:
            continue
        href = link_el.get("href", "")
        if not href.startswith("http"):
            href = "https://www.amazon.com" + href
        # Strip Amazon tracking suffix
        href = href.split("/ref=")[0]
        products.append({
            "category":  category,
            "title":     title_el.get_text(strip=True)[:160],
            "rank":      rank_el.get_text(strip=True) if rank_el else "",
            "url":       href,
            "source":    "amazon_movers",
            "scraped_at": datetime.now().isoformat(),
        })
    return products


_HEX_COLOR_RE = re.compile(r'^[0-9a-fA-F]{3}$|^[0-9a-fA-F]{6}$|^[0-9a-fA-F]{8}$')


def _is_real_hashtag(name: str) -> bool:
    # Reject CSS hex color codes (e.g. fff, FE2C55) — they live in the HTML and used to
    # leak through the fallback regex as fake hashtags.
    if _HEX_COLOR_RE.match(name):
        return False
    # Must contain at least one letter — pure-numeric / pure-symbol strings aren't tags.
    if not re.search(r'[A-Za-z]', name):
        return False
    return True


def scrape_tiktok_trends(limit: int = 10) -> list:
    """Pull trending hashtags from TikTok Creative Center. JSON is embedded in the page.

    Returns [] when the structured JSON blob isn't present — callers/templates render a
    "refreshing" placeholder. We intentionally do NOT fall back to grepping `#word` from
    the raw HTML; the page is a JS-rendered SPA, so the only matches are CSS hex colors.
    """
    html = _get(TIKTOK_TRENDS_URL)
    if not html:
        return []
    trends = []
    for m in re.finditer(r'"hashtag_name":"([^"]{2,40})".{0,500}?"rank":(\d+)', html):
        name, rank = m.group(1), int(m.group(2))
        if not _is_real_hashtag(name):
            continue
        trends.append({"hashtag": "#" + name, "rank": rank})
        if len(trends) >= limit:
            break
    return [{**t, "source": "tiktok_creative_center",
             "url": f"https://www.tiktok.com/tag/{t['hashtag'][1:]}",
             "scraped_at": datetime.now().isoformat()} for t in trends]


def gather_trending() -> dict:
    """Full sweep: TikTok hashtags + Amazon Movers across all categories."""
    tiktok = scrape_tiktok_trends(limit=15)
    amazon_by_cat = {}
    for cat, url in AMAZON_MOVERS_CATEGORIES:
        prods = scrape_amazon_movers(cat, url, limit=8)
        amazon_by_cat[cat] = prods
    return {
        "tiktok_hashtags":  tiktok,
        "amazon_movers":    amazon_by_cat,
        "captured_at":      datetime.now().isoformat(),
    }


def build_digest(trends: dict, is_preview: bool = False) -> Path:
    DIGESTS_DIR.mkdir(parents=True, exist_ok=True)
    suffix = "preview" if is_preview else "full"
    path = DIGESTS_DIR / f"trends_{datetime.now():%Y%m%d}_{suffix}.md"
    lines = [
        f"# DropshipScout — Trending Products Digest",
        f"_{datetime.now():%B %d, %Y}_\n",
        "## TikTok Creative Center — trending hashtags this week",
    ]
    tiktok = trends.get("tiktok_hashtags", [])
    show_tags = tiktok[:5] if is_preview else tiktok
    if not tiktok:
        lines.append("\n_(TikTok feed unavailable this run)_")
    for t in show_tags:
        lines.append(f"- **{t['hashtag']}** (rank #{t.get('rank', '?')}) — {t['url']}")
    if is_preview and len(tiktok) > 5:
        lines.append(f"\n_Preview: {len(tiktok) - 5} more hashtags in the full digest._")

    lines.append("\n## Amazon Movers & Shakers — top sales-rank gainers")
    for cat, prods in trends.get("amazon_movers", {}).items():
        show_prods = prods[:3] if is_preview else prods
        if not show_prods:
            continue
        lines.append(f"\n### {cat.replace('-', ' ').title()}")
        for p in show_prods:
            lines.append(f"- **{p['title']}** — {p['url']}")
        if is_preview and len(prods) > 3:
            lines.append(f"  _{len(prods) - 3} more in {cat} (full digest)._")

    if is_preview:
        lines.append(
            f"\n---\n"
            f"_This is the free preview. Subscribers get the full list + supplier sourcing links "
            f"+ weekly delta tracking._\n\n"
            f"**$47/month** → paypal.me/wholesaleomniverse/47 (reply with your email after payment)"
        )
    path.write_text("\n".join(lines))
    return path


def update_public_page(trends: dict) -> Path:
    """Regenerate website/dropship_scout_trends.html — public lead magnet showing
    the top 3 trends in each section. Drives signups to the paid digest."""
    tiktok = trends.get("tiktok_hashtags", [])[:5]
    amazon = trends.get("amazon_movers", {})

    rows = []
    for t in tiktok:
        rows.append(
            f'<li><a href="{t["url"]}" target="_blank" rel="noopener">'
            f'<strong>{t["hashtag"]}</strong></a> '
            f'<span class="muted">rank #{t.get("rank", "?")}</span></li>'
        )
    tiktok_html = "\n".join(rows) or "<li class='muted'>Refreshing — check back in a few hours.</li>"

    amazon_html = []
    for cat, prods in amazon.items():
        if not prods:
            continue
        amazon_html.append(f'<h3>{cat.replace("-", " ").title()}</h3><ul class="trend-list">')
        for p in prods[:3]:
            amazon_html.append(
                f'<li><a href="{p["url"]}" target="_blank" rel="noopener">{p["title"][:90]}</a></li>'
            )
        amazon_html.append("</ul>")
    amazon_html = "\n".join(amazon_html) or "<p class='muted'>Refreshing.</p>"

    updated = datetime.now().strftime("%B %d, %Y at %H:%M")
    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="description" content="Free weekly digest of viral TikTok-shop products + Amazon Movers & Shakers. New winning products to dropship every Monday.">
<title>DropshipScout — Free Weekly Trending-Product Digest</title>
<link rel="stylesheet" href="styles.css">
<link rel="icon" type="image/png" href="assets/logo.png">
<style>
.trend-list {{ list-style:none; padding:0; }}
.trend-list li {{ padding:8px 0; border-bottom:1px solid #eee; }}
.muted {{ color:#888; font-size:13px; }}
.cta-box {{ background:linear-gradient(135deg,#0f172a 0%,#1e293b 100%); color:#fff; padding:32px; border-radius:12px; margin:32px 0; text-align:center; }}
.cta-box a.btn {{ background:#f59e0b; color:#0f172a; padding:14px 28px; border-radius:8px; font-weight:700; text-decoration:none; display:inline-block; margin-top:12px; }}
</style>
</head>
<body>

<header class="site">
  <div class="container">
    <a href="/" class="brand">
      <img src="assets/logo.png" alt="Wholesale Omniverse">
      <span class="brand-text">WHOLESALE <span class="accent">OMNIVERSE</span></span>
    </a>
  </div>
</header>

<section class="hero">
  <div class="container">
    <p class="eyebrow">Updated {updated}</p>
    <h1>What's <span class="accent">selling on TikTok</span> right now</h1>
    <p class="lede">
      Live snapshot of trending TikTok-shop hashtags + Amazon Movers & Shakers — the same data feed
      paid subscribers get every Monday. Free preview below; the full digest unlocks every category,
      supplier sourcing links, and week-over-week delta tracking.
    </p>
  </div>
</section>

<section>
  <div class="container">
    <h2>TikTok — trending hashtags</h2>
    <ul class="trend-list">
      {tiktok_html}
    </ul>

    <h2 style="margin-top:48px;">Amazon Movers & Shakers</h2>
    {amazon_html}

    <div class="cta-box">
      <h2 style="color:#fff; margin-top:0;">Get the full digest every Monday</h2>
      <p>Every category. Sourcing links. Weekly trend deltas. Direct to your inbox.</p>
      <p><strong>$47/month</strong> · cancel anytime</p>
      <a class="btn" href="mailto:WholesaleOmniverse@gmail.com?subject=DropshipScout%20Subscribe&body=Hi%2C%20I%27d%20like%20to%20subscribe%20to%20DropshipScout.">Subscribe</a>
    </div>
  </div>
</section>

<footer class="site">
  <div class="container">
    <p>&copy; 2026 Wholesale Omniverse LLC. Data refreshed automatically.</p>
  </div>
</footer>
</body>
</html>"""
    WEBSITE_OUT.parent.mkdir(parents=True, exist_ok=True)
    WEBSITE_OUT.write_text(page)
    return WEBSITE_OUT


def deliver_subscribers(trends: dict) -> dict:
    """Send the full digest to active paying subscribers."""
    subs = storage.load("ds_subscribers.json", [])
    sent = 0
    digest_path = build_digest(trends, is_preview=False)
    for s in subs:
        if s.get("status") != "active":
            continue
        body = (
            f"Hi {s.get('name', 'there')},\n\n"
            f"Your weekly DropshipScout digest is attached. "
            f"{len(trends.get('tiktok_hashtags', []))} trending hashtags and "
            f"{sum(len(v) for v in trends.get('amazon_movers', {}).values())} mover products this week.\n\n"
            f"— DropshipScout, Wholesale Omniverse LLC"
        )
        result = mailer.send(AGENT_KEY, s["email"],
                             f"Weekly trending-products digest — {datetime.now():%b %d}",
                             body, purpose="fulfillment", attachments=[str(digest_path)])
        if result.get("status") == "sent":
            sent += 1
    return {"fulfillment_sent": sent}


def run_full_cycle() -> dict:
    trends = gather_trending()
    storage.save("ds_latest_trends.json", trends)

    update_public_page(trends)

    # Weekly delivery — only send digest on Mondays to avoid daily spam to subscribers.
    delivered = {"fulfillment_sent": 0}
    if datetime.now().weekday() == 0:  # 0 = Monday
        delivered = deliver_subscribers(trends)

    rev = billing.revenue_summary(AGENT_KEY)
    subs = storage.load("ds_subscribers.json", [])
    metrics.record(
        AGENT_KEY,
        prospects_added=0,  # this agent uses a public lead-magnet page, not cold outreach
        outreach_sent=0,
        fulfillment_sent=delivered["fulfillment_sent"],
        active_subs=sum(1 for s in subs if s.get("status") == "active"),
        mrr=rev["mrr"],
        total_revenue=rev["total_paid"],
    )
    return {
        "tiktok_hashtags":   len(trends.get("tiktok_hashtags", [])),
        "amazon_products":   sum(len(v) for v in trends.get("amazon_movers", {}).values()),
        "fulfillment_sent":  delivered["fulfillment_sent"],
        "public_page":       str(WEBSITE_OUT),
        **rev,
    }
