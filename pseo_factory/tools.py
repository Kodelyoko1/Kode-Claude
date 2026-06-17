"""
pSEO Factory — programmatic SEO landing-page generator.

Generates city-by-city "We Buy Houses" landing pages for wholesale real estate.
Each page targets a long-tail keyword like "we buy houses [city] [state]" and
includes locally-tuned copy, FAQs, and an HTML file ready to drop into the
website/ directory or upload to any static host.

Config via data/pseo_config.json:
  {
    "markets": [{"city": "Portland", "state": "ME", "county": "Cumberland"}, ...],
    "business_name": "Wholesale Omniverse",
    "phone": "207-385-4041",
    "email": "WholesaleOmniverse@gmail.com"
  }

Outputs:
  data/pseo_pages/{slug}.html    — standalone landing page
  data/pseo_pages/{slug}.md      — markdown version (for blog/CMS)
  data/pseo_index.json           — registry of all generated pages

Entry point: run_full_cycle()
"""
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from autonomous import storage, mailer, metrics, billing

AGENT_KEY  = "pseo_factory"
PAGES_DIR  = Path(__file__).parent.parent / "data" / "pseo_pages"
PAGES_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_CONFIG = {
    "markets": [
        {"city": "Portland",    "state": "ME", "county": "Cumberland"},
        {"city": "Bangor",      "state": "ME", "county": "Penobscot"},
        {"city": "Lewiston",    "state": "ME", "county": "Androscoggin"},
        {"city": "Auburn",      "state": "ME", "county": "Androscoggin"},
        {"city": "Augusta",     "state": "ME", "county": "Kennebec"},
        {"city": "Biddeford",   "state": "ME", "county": "York"},
        {"city": "South Portland", "state": "ME", "county": "Cumberland"},
        {"city": "Sanford",     "state": "ME", "county": "York"},
        {"city": "Brunswick",   "state": "ME", "county": "Cumberland"},
        {"city": "Saco",        "state": "ME", "county": "York"},
    ],
    "business_name": "Wholesale Omniverse",
    "phone": "207-385-4041",
    "email": "WholesaleOmniverse@gmail.com",
    "paypal_me": "paypal.me/wholesaleomniverse",
}

# Motivation phrases rotated by city index to avoid duplicate content penalties
MOTIVATION_HOOKS = [
    "behind on payments",
    "going through a divorce",
    "inherited an unwanted property",
    "facing foreclosure",
    "relocating out of state",
    "tired of dealing with tenants",
    "property needs major repairs",
    "going through probate",
    "underwater on your mortgage",
    "dealing with tax liens",
]

FAQS = [
    ("How fast can you close?",
     "We can close in as little as 7–14 days — or on your timeline if you need more time."),
    ("Do I need to make repairs?",
     "No. We buy houses as-is. You don't need to fix a single thing."),
    ("Are there any fees or commissions?",
     "Zero. We're not agents. There are no commissions, no closing costs on your end."),
    ("How do you determine the offer price?",
     "We look at recent sales in your neighborhood, the condition of the property, "
     "and what repairs are needed. We then make you a fair, no-obligation cash offer."),
    ("What if I owe more than the house is worth?",
     "We can still help. We work with homeowners in all situations, including short sales."),
    ("Is my information kept private?",
     "Absolutely. Your information is never shared with third parties."),
]


def _slug(city: str, state: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", f"we-buy-houses-{city}-{state}".lower()).strip("-")


def _title(city: str, state: str) -> str:
    return f"We Buy Houses {city}, {state} — Fast Cash Offers, No Fees"


def _meta_desc(city: str, state: str, biz: str) -> str:
    return (
        f"Sell your house fast in {city}, {state}. {biz} buys homes as-is for cash. "
        f"No repairs, no commissions, no hassle. Get a free offer in 24 hours."
    )


def _page_html(market: dict, config: dict, hook: str) -> str:
    city    = market["city"]
    state   = market["state"]
    county  = market.get("county", "")
    biz     = config["business_name"]
    phone   = config["phone"]
    email   = config["email"]
    title   = _title(city, state)
    meta    = _meta_desc(city, state, biz)
    slug    = _slug(city, state)
    keyword = f"we buy houses {city} {state}"

    faq_html = "\n".join(
        f'  <details><summary>{q}</summary><p>{a}</p></details>'
        for q, a in FAQS
    )

    county_line = f", {county} County" if county else ""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title}</title>
  <meta name="description" content="{meta}">
  <meta name="keywords" content="{keyword}, sell my house fast {city}, cash home buyers {city} {state}, {county} home buyers">
  <link rel="canonical" href="/{slug}.html">
  <style>
    *{{box-sizing:border-box;margin:0;padding:0}}
    body{{font-family:'Segoe UI',sans-serif;color:#1a1a1a;background:#fff}}
    header{{background:#1a3c6e;color:#fff;padding:2rem 1rem;text-align:center}}
    header h1{{font-size:2rem;margin-bottom:.5rem}}
    header p{{font-size:1.1rem;opacity:.9}}
    .cta-bar{{background:#e8a020;padding:1.2rem;text-align:center}}
    .cta-bar a{{color:#fff;font-size:1.3rem;font-weight:700;text-decoration:none}}
    .section{{max-width:860px;margin:2.5rem auto;padding:0 1rem}}
    h2{{color:#1a3c6e;margin-bottom:1rem;font-size:1.5rem}}
    .steps{{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:1.5rem;margin:1.5rem 0}}
    .step{{background:#f4f7fb;border-radius:8px;padding:1.5rem;text-align:center}}
    .step .num{{font-size:2rem;font-weight:700;color:#e8a020}}
    .benefits li{{margin:.6rem 0;padding-left:1.5rem;position:relative}}
    .benefits li::before{{content:"✓";position:absolute;left:0;color:#27ae60;font-weight:700}}
    details{{border:1px solid #ddd;border-radius:6px;padding:.8rem 1rem;margin:.6rem 0}}
    summary{{cursor:pointer;font-weight:600;color:#1a3c6e}}
    details p{{margin-top:.6rem;color:#444}}
    footer{{background:#1a3c6e;color:#aac;text-align:center;padding:1.5rem;margin-top:3rem;font-size:.85rem}}
  </style>
</head>
<body>

<header>
  <h1>We Buy Houses in {city}, {state}</h1>
  <p>Fast cash offers — close in 7–14 days — zero fees, zero repairs needed</p>
</header>

<div class="cta-bar">
  <a href="tel:{phone}">📞 Call or Text Now: {phone}</a>
</div>

<div class="section">
  <h2>Get a Fair Cash Offer for Your {city}{county_line} Home</h2>
  <p>
    Are you {hook}? {biz} buys houses in {city} and throughout {state} — in any
    condition, any situation. We pay cash, cover closing costs, and close on
    <em>your</em> timeline — sometimes in as little as 7 days.
  </p>
</div>

<div class="section">
  <h2>How It Works</h2>
  <div class="steps">
    <div class="step"><div class="num">1</div><strong>Contact Us</strong><br>Call, text, or email us about your property.</div>
    <div class="step"><div class="num">2</div><strong>Get an Offer</strong><br>We evaluate and send a no-obligation cash offer within 24 hrs.</div>
    <div class="step"><div class="num">3</div><strong>Choose Your Date</strong><br>Pick any closing date. We handle the paperwork.</div>
    <div class="step"><div class="num">4</div><strong>Get Paid</strong><br>Cash in hand at closing. Simple as that.</div>
  </div>
</div>

<div class="section">
  <h2>Why Homeowners in {city} Choose {biz}</h2>
  <ul class="benefits">
    <li>No repairs, no cleaning — sell it exactly as-is</li>
    <li>No agent commissions (save 5–6%)</li>
    <li>No lender delays — we pay cash</li>
    <li>Close in 7 days or whenever you're ready</li>
    <li>We handle all paperwork and closing costs</li>
    <li>Local {state} investors — not a national chain</li>
  </ul>
</div>

<div class="section">
  <h2>We Buy All Types of {city} Properties</h2>
  <ul class="benefits">
    <li>Single-family homes</li>
    <li>Multi-family / duplexes</li>
    <li>Inherited or estate properties</li>
    <li>Rental properties (even with tenants)</li>
    <li>Distressed or fire-damaged homes</li>
    <li>Properties with code violations or liens</li>
  </ul>
</div>

<div class="section">
  <h2>Frequently Asked Questions</h2>
  <div class="faq">
{faq_html}
  </div>
</div>

<div class="section" style="text-align:center;background:#f4f7fb;padding:2rem;border-radius:10px">
  <h2>Ready to Get Your Cash Offer?</h2>
  <p style="margin:.8rem 0 1.5rem">No obligation. No pressure. Just a fair offer for your {city} property.</p>
  <p style="font-size:1.3rem;font-weight:700"><a href="tel:{phone}">{phone}</a></p>
  <p style="margin:.5rem 0"><a href="mailto:{email}">{email}</a></p>
</div>

<footer>
  &copy; {datetime.now().year} {biz} — Cash Home Buyers in {city}, {state} and throughout {state}.
  | <a href="mailto:{email}" style="color:#aac">{email}</a>
  | <a href="tel:{phone}" style="color:#aac">{phone}</a>
</footer>

</body>
</html>"""


def _page_md(market: dict, config: dict, hook: str) -> str:
    city  = market["city"]
    state = market["state"]
    biz   = config["business_name"]
    phone = config["phone"]
    email = config["email"]

    faq_md = "\n".join(f"**{q}**\n{a}\n" for q, a in FAQS)

    return f"""# We Buy Houses {city}, {state} — Fast Cash Offers, No Fees

*{biz} | {phone} | {email}*

---

Are you {hook}? {biz} buys houses in {city}, {state} — any condition, any situation.
We pay cash, cover closing costs, and close on your timeline.

## How It Works

1. **Contact Us** — Call, text, or email about your property.
2. **Get an Offer** — No-obligation cash offer within 24 hours.
3. **Choose Your Closing Date** — 7 days or whenever you're ready.
4. **Get Paid** — Cash at closing, all paperwork handled.

## Why Choose {biz}?

- No repairs, no cleaning — sell as-is
- Zero agent commissions
- No lender delays — cash purchase
- Local {state} investors

## FAQ

{faq_md}

---

📞 **{phone}** | ✉ **{email}**
"""


def run_full_cycle() -> dict:
    config = storage.load("pseo_config.json", DEFAULT_CONFIG)
    markets = config.get("markets", DEFAULT_CONFIG["markets"])

    index = storage.load("pseo_index.json", {})
    pages_built = 0
    pages_skipped = 0

    for i, market in enumerate(markets):
        city  = market.get("city", "")
        state = market.get("state", "")
        if not city or not state:
            continue

        slug = _slug(city, state)
        hook = MOTIVATION_HOOKS[i % len(MOTIVATION_HOOKS)]

        html_path = PAGES_DIR / f"{slug}.html"
        md_path   = PAGES_DIR / f"{slug}.md"

        html_content = _page_html(market, config, hook)
        md_content   = _page_md(market, config, hook)

        html_path.write_text(html_content, encoding="utf-8")
        md_path.write_text(md_content,   encoding="utf-8")

        index[slug] = {
            "slug":       slug,
            "city":       city,
            "state":      state,
            "county":     market.get("county", ""),
            "html_path":  str(html_path),
            "md_path":    str(md_path),
            "keyword":    f"we buy houses {city} {state}",
            "built_at":   datetime.now(timezone.utc).isoformat(),
        }
        pages_built += 1

    storage.save("pseo_index.json", index)

    rev  = billing.revenue_summary(AGENT_KEY)
    subs = storage.load("pseo_subscribers.json", [])
    metrics.record(
        AGENT_KEY,
        pages_built=pages_built,
        total_pages=len(index),
        active_subs=sum(1 for s in subs if s.get("status") == "active"),
        mrr=rev["mrr"],
    )

    return {
        "pages_built":   pages_built,
        "pages_skipped": pages_skipped,
        "total_pages":   len(index),
        "output_dir":    str(PAGES_DIR),
        "mrr":           rev["mrr"],
    }
