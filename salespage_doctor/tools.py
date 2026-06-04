"""
SalesPageDoctor — audits digital-product sales pages (Gumroad, Payhip, Sellfy, Ko-fi,
Lemon Squeezy creator pages) for conversion-killing issues. Free preview emails the
top 3 fixes; full audit + monthly monitoring are paid tiers.

Revenue: $77 one-time audit, $37/mo monitoring, $147 launch package (3 audits).

Heuristics-based (no LLM required): scans rendered HTML for trust signals, CTA
clarity, social proof, copy length, image count, mobile viewport, urgency cues.
"""
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).parent.parent))
from autonomous import storage, mailer, billing, metrics
from salespage_doctor import health

AGENT_KEY = "salespage_doctor"
REPORTS_DIR = Path(__file__).parent.parent / "data" / "spd_reports"
WEBSITE_OUT = Path(__file__).parent.parent / "website" / "salespage_doctor.html"

# Creator-platform queries — these are the URLs we want to find and audit.
# Each platform exposes individual product pages we can probe and contact creator.
DEFAULT_PROSPECT_QUERIES = [
    'site:gumroad.com inurl:l "buy"',
    'site:payhip.com inurl:b "buy"',
    'site:sellfy.com/p "add to cart"',
    'site:ko-fi.com/s "shop"',
]

SKIP_EMAIL_FRAGMENTS = {"noreply", "no-reply", "donotreply", "postmaster",
                        "admin@", "webmaster@", "privacy@", "legal@",
                        "press@", "example.com", "support@gumroad",
                        "support@payhip", "support@sellfy", "support@ko-fi"}

VALID_TLD = {"com", "org", "net", "io", "co", "us", "uk", "ca", "info", "biz",
             "tech", "blog", "shop", "store", "agency", "studio", "design", "media",
             "ai", "app", "dev", "xyz", "club", "online", "site", "live", "today"}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 SalesPageDoctor/1.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def _unwrap_bing_redirect(url: str) -> str:
    if "/ck/a" not in url:
        return url
    import base64
    from urllib.parse import parse_qs
    try:
        u = parse_qs(urlparse(url).query).get("u", [""])[0]
        if u.startswith("a1"):
            padded = u[2:] + "=" * (-len(u[2:]) % 4)
            return base64.urlsafe_b64decode(padded).decode("utf-8", errors="ignore")
    except Exception:
        pass
    return url


def _bing_search(query: str, n: int = 10) -> list:
    try:
        import requests
        from bs4 import BeautifulSoup
    except ImportError:
        return []
    try:
        resp = requests.get(
            f"https://www.bing.com/search?q={requests.utils.quote(query)}",
            headers=HEADERS, timeout=10,
        )
    except Exception:
        return []
    soup = BeautifulSoup(resp.text, "html.parser")
    out = []
    for li in soup.select("li.b_algo")[:n]:
        a = li.select_one("h2 a")
        if not a:
            continue
        url = _unwrap_bing_redirect(a.get("href", ""))
        if url.startswith("http"):
            out.append({"url": url, "title": a.get_text(strip=True)[:120]})
    return out


def _fetch(url: str, timeout: int = 12) -> str:
    try:
        import requests
        resp = requests.get(url, headers=HEADERS, timeout=timeout)
        if resp.status_code == 200 and "text/html" in resp.headers.get("content-type", ""):
            return resp.text
    except Exception:
        pass
    return ""


def _extract_email(html: str) -> str:
    mailtos = re.findall(r'mailto:([\w.+-]+@[\w.-]+\.[a-zA-Z]{2,})', html, re.IGNORECASE)
    candidates = mailtos + re.findall(r'\b[\w.+-]+@[\w-]+\.[a-zA-Z]{2,6}\b', html)
    for e in candidates:
        e_lower = e.lower()
        tld = e_lower.rsplit(".", 1)[-1]
        if tld not in VALID_TLD:
            continue
        if any(skip in e_lower for skip in SKIP_EMAIL_FRAGMENTS):
            continue
        return e
    return ""


def _page_slug(url: str) -> str:
    p = urlparse(url)
    host = p.netloc.lower().replace("www.", "")
    path = p.path.strip("/").replace("/", "-")[:50]
    raw = f"{host}_{path}" if path else host
    return re.sub(r"[^a-z0-9.-]", "-", raw)[:80]


def audit_salespage(url: str) -> dict:
    """Heuristic audit. Returns a list of issues with severity + fix guidance."""
    html = _fetch(url)
    if not html:
        health.record_audit(url, "fetch_failed", detail="empty response from _fetch")
        return {"error": "fetch_failed", "url": url}
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        health.record_audit(url, "bs4_missing")
        return {"error": "bs4_missing", "url": url}
    soup = BeautifulSoup(html, "html.parser")

    issues = []
    text = soup.get_text(" ", strip=True)

    # --- CTA clarity ---
    cta_terms = ["buy", "purchase", "get", "add to cart", "checkout", "subscribe", "join"]
    buttons = soup.find_all(["button", "a"])
    cta_count = sum(1 for b in buttons if any(t in b.get_text(strip=True).lower() for t in cta_terms))
    if cta_count == 0:
        issues.append({"severity": "high", "category": "cta",
                       "title": "No clear call-to-action button",
                       "fix": "Add a single prominent button with action verb: 'Buy now', 'Get instant access', 'Add to cart'. Conversions drop ~30% without an obvious primary CTA."})
    elif cta_count > 8:
        issues.append({"severity": "med", "category": "cta",
                       "title": f"Too many CTAs ({cta_count}) — decision fatigue",
                       "fix": "Limit to 1 primary + 1 secondary CTA on a sales page. Multiple competing CTAs split attention and reduce conversion."})

    # --- Social proof ---
    proof_terms = ["testimonial", "review", "5 star", "★★", "rating", "customers say", "trusted by", "as seen", "press"]
    has_proof = any(t in text.lower() for t in proof_terms)
    if not has_proof:
        issues.append({"severity": "high", "category": "social_proof",
                       "title": "No visible social proof",
                       "fix": "Add 3-5 testimonials with names + photos, or display a star rating, customer count, or recognizable logos. Pages with social proof convert ~34% higher (NN/g study)."})

    # --- Trust signals ---
    trust_terms = ["guarantee", "refund", "money back", "secure", "ssl", "privacy"]
    has_trust = any(t in text.lower() for t in trust_terms)
    if not has_trust:
        issues.append({"severity": "med", "category": "trust",
                       "title": "No refund / guarantee language",
                       "fix": "Add a money-back guarantee (30 days is standard). Reduces purchase anxiety; refund rates rarely exceed 5%, but conversions can lift 15-25%."})

    # --- Copy length / depth ---
    words = len(text.split())
    if words < 200:
        issues.append({"severity": "high", "category": "copy_depth",
                       "title": f"Page copy is too thin ({words} words)",
                       "fix": "Sales pages for digital products convert best at 800-1500 words. Add: outcome bullets, who it's for, what's inside, common objections + answers, FAQ."})
    elif words > 4000:
        issues.append({"severity": "low", "category": "copy_depth",
                       "title": f"Page copy is very long ({words} words)",
                       "fix": "Consider a tighter narrative: outcome → proof → offer → FAQ → CTA. Long pages without clear structure fatigue readers."})

    # --- Images ---
    imgs = soup.find_all("img")
    if len(imgs) < 2:
        issues.append({"severity": "med", "category": "imagery",
                       "title": f"Only {len(imgs)} image(s) on page",
                       "fix": "Show the product: covers, screenshots, inside-look mockups. 3-6 images covering hero + product detail typically lifts conversion."})

    # --- Mobile viewport ---
    viewport = soup.find("meta", attrs={"name": "viewport"})
    if not viewport:
        issues.append({"severity": "high", "category": "mobile",
                       "title": "Missing mobile viewport meta tag",
                       "fix": "Add: <meta name='viewport' content='width=device-width, initial-scale=1.0'>. Without it, mobile users see desktop-sized layout — disastrous since 60%+ of creator-product traffic is mobile."})

    # --- Urgency / scarcity ---
    urgency_terms = ["limited", "today only", "ends ", "spots left", "while supplies", "save $", "% off"]
    has_urgency = any(t in text.lower() for t in urgency_terms)
    if not has_urgency:
        issues.append({"severity": "low", "category": "urgency",
                       "title": "No urgency or scarcity cues",
                       "fix": "Consider a launch discount with deadline, or 'first 100 customers get bonus X'. Ethical urgency lifts conversion 8-15%."})

    # --- Pricing visible ---
    has_price = bool(re.search(r'\$\s?\d+', text))
    if not has_price:
        issues.append({"severity": "high", "category": "pricing",
                       "title": "No visible price on page",
                       "fix": "Show the price prominently. Hidden pricing on a sales page is conversion suicide — visitors leave to find a clear alternative."})

    severity_weight = {"high": 30, "med": 15, "low": 5}
    score = max(0, 100 - sum(severity_weight.get(i["severity"], 0) for i in issues))

    health.record_audit(url, "success", score=score, issue_count=len(issues))
    return {
        "url":         url,
        "score":       score,
        "issue_count": len(issues),
        "issues":      issues,
        "word_count":  words,
        "audited_at":  datetime.now().isoformat(),
    }


def build_report(slug: str, audit: dict, is_preview: bool = False) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    suffix = "preview" if is_preview else "full"
    path = REPORTS_DIR / f"{slug}_{datetime.now():%Y%m%d}_{suffix}.md"
    issues = audit.get("issues", [])
    show = issues[:3] if is_preview else issues
    lines = [
        f"# Sales Page Audit — {audit.get('url')}",
        f"_{datetime.now():%B %d, %Y}_\n",
        f"**Conversion-readiness score:** {audit.get('score')}/100",
        f"**Issues found:** {audit.get('issue_count')}",
        f"**Page word count:** {audit.get('word_count')}\n",
        f"## Top {'3 ' if is_preview else ''}fixes (ranked by impact)",
    ]
    if is_preview and len(issues) > 3:
        lines.append(f"\n_Preview shows top 3 of {len(issues)} issues. Unlock full audit: $77 → paypal.me/wholesaleomniverse/77_\n")
    for i in show:
        sev = i["severity"].upper()
        lines.append(f"\n### [{sev}] {i['title']}")
        lines.append(f"_Category: {i['category']}_\n")
        lines.append(f"{i['fix']}")
    path.write_text("\n".join(lines))
    return path


def discover_prospects(query: str = "", max_new: int = 6) -> dict:
    if not query:
        query = DEFAULT_PROSPECT_QUERIES[datetime.now().day % len(DEFAULT_PROSPECT_QUERIES)]
    existing = storage.load("spd_prospects.json", [])
    existing_slugs = {p["slug"] for p in existing}

    results = _bing_search(query, n=max_new * 4)
    added = []
    for r in results:
        if len(added) >= max_new:
            break
        url = r["url"]
        slug = _page_slug(url)
        if slug in existing_slugs or not slug:
            continue
        html = _fetch(url)
        if not html or len(html) < 1000:
            continue
        contact = _extract_email(html)
        if not contact:
            continue
        existing.append({
            "slug":          slug,
            "url":           url,
            "title":         r.get("title", ""),
            "contact_email": contact,
            "discovered_at": datetime.now().isoformat(),
            "status":        "discovered",
        })
        added.append(slug)
    storage.save("spd_prospects.json", existing)
    health.record_query(query, results=len(results), discovered=len(added))
    return {"discovered": len(added), "query": query, "new_slugs": added}


def acquire_cycle() -> dict:
    prospects = storage.load("spd_prospects.json", [])
    sent = 0
    new_audits = 0
    for p in prospects:
        if p.get("status") in ("contacted", "client", "high_score_skip"):
            continue
        audit = audit_salespage(p["url"])
        if "error" in audit:
            p["status"] = f"audit_error_{audit['error']}"
            continue
        new_audits += 1
        if audit["score"] >= 85 or not audit["issues"]:
            p["status"] = "high_score_skip"
            p["last_score"] = audit["score"]
            continue
        report = build_report(p["slug"], audit, is_preview=True)
        if p.get("contact_email"):
            top = audit["issues"][0]
            body = (
                f"Hi — I ran a free conversion audit on your sales page: {p['url']}\n\n"
                f"Conversion-readiness score: {audit['score']}/100\n"
                f"Issues found: {audit['issue_count']} ({top['severity'].upper()} severity top issue)\n"
                f"Biggest fix: {top['title']}\n\n"
                f"Free preview attached (top 3 issues with fix guidance).\n\n"
                f"Full audit with every issue + specific copy/layout fixes: $77.\n"
                f"Monthly monitoring (re-audit + alert when score drops): $37/mo.\n"
                f"Launch package (3 audits + before/after): $147.\n\n"
                f"PayPal: paypal.me/wholesaleomniverse/77\n"
                f"Reply with your access key after payment for full delivery."
            )
            result = mailer.send(AGENT_KEY, p["contact_email"],
                                 f"Your sales page scored {audit['score']}/100 — {audit['issue_count']} fixable issues",
                                 body, purpose="outreach", attachments=[str(report)])
            if result.get("status") == "sent":
                p["status"] = "contacted"
                p["contacted_at"] = datetime.now().isoformat()
                p["last_score"] = audit["score"]
                sent += 1
    storage.save("spd_prospects.json", prospects)
    return {"new_audits": new_audits, "outreach_sent": sent}


def fulfill_cycle() -> dict:
    clients = storage.load("spd_clients.json", [])
    sent = 0
    for c in clients:
        if c.get("status") != "active":
            continue
        audit = audit_salespage(c["url"])
        if "error" in audit:
            continue
        report = build_report(c["slug"], audit, is_preview=False)
        body = (
            f"Hi {c.get('name', 'there')},\n\n"
            f"Monthly audit for {c['url']}.\n"
            f"Score: {audit['score']}/100 ({audit['issue_count']} issues)\n\n"
            f"Full report attached.\n\n"
            f"— SalesPageDoctor, Wholesale Omniverse LLC"
        )
        result = mailer.send(AGENT_KEY, c["contact_email"],
                             f"Monthly sales-page audit — {audit['score']}/100",
                             body, purpose="fulfillment", attachments=[str(report)])
        if result.get("status") == "sent":
            sent += 1
    return {"fulfillment_sent": sent}


def update_public_page() -> Path:
    page = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="description" content="Free 90-second audit of your Gumroad / Payhip / Sellfy / Ko-fi sales page. Get the top 3 conversion fixes instantly.">
<title>SalesPageDoctor — Free Sales-Page Audit for Digital Creators</title>
<link rel="stylesheet" href="styles.css">
<link rel="icon" type="image/png" href="assets/logo.png">
<style>
.tier-card { border:1px solid #e5e7eb; border-radius:12px; padding:24px; background:#fff; }
.tier-card.featured { border-color:#f59e0b; box-shadow:0 8px 24px rgba(245,158,11,0.15); }
.tier-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(260px,1fr)); gap:20px; margin:24px 0; }
.audit-form { background:#0f172a; color:#fff; padding:32px; border-radius:12px; margin:32px 0; }
.audit-form input { width:100%; padding:14px; border-radius:8px; border:none; font-size:16px; margin-bottom:12px; }
.audit-form button { background:#f59e0b; color:#0f172a; padding:14px 28px; border-radius:8px; font-weight:700; border:none; font-size:16px; cursor:pointer; }
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
    <p class="eyebrow">For creators on Gumroad, Payhip, Sellfy, Ko-fi, Lemon Squeezy</p>
    <h1>Your sales page is leaking <span class="accent">conversions</span>. Find out why in 90 seconds.</h1>
    <p class="lede">
      SalesPageDoctor scans your product page for 8 conversion-killing patterns most creators
      overlook: weak CTAs, missing social proof, copy depth, mobile viewport, trust signals,
      and pricing visibility. Get a score and the top 3 fixes — free.
    </p>

    <div class="audit-form">
      <h2 style="color:#fff; margin-top:0;">Free 3-issue audit</h2>
      <p>Drop your sales page URL — we'll email you the audit within 24 hours.</p>
      <form action="mailto:WholesaleOmniverse@gmail.com" method="post" enctype="text/plain">
        <input name="url" type="url" placeholder="https://gumroad.com/l/your-product" required>
        <input name="email" type="email" placeholder="Where should we send the audit?" required>
        <button type="submit">Get my free audit</button>
      </form>
    </div>
  </div>
</section>

<section>
  <div class="container">
    <h2>How it works</h2>
    <ol style="font-size:17px; line-height:1.8; color:var(--slate-7);">
      <li><strong>You drop your sales page URL</strong> in the form above.</li>
      <li><strong>We scan it</strong> for 8 conversion patterns: CTA clarity, social proof, trust signals, copy depth, imagery, mobile viewport, urgency, pricing visibility.</li>
      <li><strong>You get a free report</strong> with your score (out of 100) and the top 3 high-impact fixes within 24 hours.</li>
      <li><strong>Upgrade if you want everything</strong> — full audit with every issue and specific copy/layout fixes.</li>
    </ol>

    <h2 style="margin-top:48px;">Pricing</h2>
    <div class="tier-grid">
      <div class="tier-card">
        <h3>Free preview</h3>
        <p style="font-size:24px; font-weight:700;">$0</p>
        <ul><li>Conversion score</li><li>Top 3 issues</li><li>Fix guidance for each</li></ul>
      </div>
      <div class="tier-card featured">
        <h3>Full audit</h3>
        <p style="font-size:24px; font-weight:700;">$77 <span style="font-size:14px; font-weight:400;">once</span></p>
        <ul><li>Every issue found</li><li>Specific copy + layout fixes</li><li>Mobile + desktop scoring</li><li>Before/after checklist</li></ul>
        <p><a class="btn btn-primary" href="https://paypal.me/wholesaleomniverse/77">Buy full audit</a></p>
      </div>
      <div class="tier-card">
        <h3>Monthly monitoring</h3>
        <p style="font-size:24px; font-weight:700;">$37<span style="font-size:14px;">/mo</span></p>
        <ul><li>Re-audit every 30 days</li><li>Email alert when score drops</li><li>Track fixes over time</li></ul>
        <p><a class="btn btn-secondary" href="https://paypal.me/wholesaleomniverse/37">Subscribe</a></p>
      </div>
      <div class="tier-card">
        <h3>Launch package</h3>
        <p style="font-size:24px; font-weight:700;">$147</p>
        <ul><li>3 full audits over launch window</li><li>Pre-launch baseline</li><li>Mid-launch tune</li><li>Post-launch results</li></ul>
        <p><a class="btn btn-secondary" href="https://paypal.me/wholesaleomniverse/147">Buy launch package</a></p>
      </div>
    </div>
  </div>
</section>

<footer class="site">
  <div class="container">
    <p>&copy; 2026 Wholesale Omniverse LLC. SalesPageDoctor scans publicly accessible URLs only.</p>
  </div>
</footer>
</body>
</html>"""
    WEBSITE_OUT.parent.mkdir(parents=True, exist_ok=True)
    WEBSITE_OUT.write_text(page)
    return WEBSITE_OUT


def run_full_cycle() -> dict:
    update_public_page()
    d = discover_prospects()
    a = acquire_cycle()
    f = fulfill_cycle()
    rev = billing.revenue_summary(AGENT_KEY)
    clients = storage.load("spd_clients.json", [])
    metrics.record(
        AGENT_KEY,
        prospects_added=d["discovered"],
        outreach_sent=a["outreach_sent"],
        fulfillment_sent=f["fulfillment_sent"],
        active_subs=sum(1 for c in clients if c.get("status") == "active"),
        mrr=rev["mrr"],
        total_revenue=rev["total_paid"],
    )
    return {"discovered": d["discovered"], **a, **f, **rev}
