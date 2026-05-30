"""
LinkMender — SEO dead-link audit reports.
Revenue: $97 one-time audit, $47/mo monitoring, $197 agency lead list.
"""
import re
import sys
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).parent.parent))
from autonomous import storage, mailer, billing, metrics

AGENT_KEY = "link_mender"
SNAPSHOT_DIR = Path(__file__).parent.parent / "data" / "lm_snapshots"
REPORTS_DIR = Path(__file__).parent.parent / "data" / "lm_reports"

# Resource pages have the highest broken-link density per minute spent auditing,
# and the curator usually has a contact email on the page itself.
DEFAULT_PROSPECT_QUERIES = [
    '"useful links" "contact" -site:wikipedia.org -site:reddit.com',
    '"resource page" small business "contact us"',
    '"recommended tools" blog "email"',
    '"link roundup" inurl:blog "contact"',
]

SKIP_EMAIL_FRAGMENTS = {"noreply", "no-reply", "donotreply", "postmaster",
                        "admin@", "webmaster@", "privacy@", "legal@",
                        "press@", "example.com", "sentry.io", "wixpress"}

# Real TLDs we'll accept — keeps "logo-245x245@1x.png" out of the email pool.
VALID_TLD = {"com", "org", "net", "io", "co", "us", "uk", "ca", "info", "biz",
             "tech", "blog", "shop", "store", "agency", "studio", "design", "media",
             "ai", "app", "dev", "xyz", "club", "online", "site", "live", "today"}


def extract_links(html_path: Path) -> list:
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return []
    soup = BeautifulSoup(html_path.read_text(errors="ignore"), "html.parser")
    out = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith("http"):
            out.append({"url": href, "anchor": a.get_text(strip=True)[:120], "source_file": html_path.name})
    return out


def check_link(url: str, timeout: int = 8) -> int:
    """Return HTTP status code (or 0 on error)."""
    try:
        req = urllib.request.Request(url, method="HEAD",
                                     headers={"User-Agent": "Mozilla/5.0 LinkMender/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return 0


def audit_site(site_slug: str, deep_check: bool = True) -> dict:
    """Pull links from snapshots for this site, check each, return broken ones."""
    site_dir = SNAPSHOT_DIR / site_slug
    if not site_dir.exists():
        return {"error": "no_snapshots", "site": site_slug}
    links = []
    for html in site_dir.glob("*.html"):
        links.extend(extract_links(html))
    # dedupe by URL
    seen = set()
    unique = []
    for l in links:
        if l["url"] in seen:
            continue
        seen.add(l["url"])
        unique.append(l)

    broken = []
    if deep_check:
        # Sequential HEADs against 200 links could take 27min worst-case
        # (8s timeout × 200). 10 workers keeps a polite RPS while bringing
        # typical runs under 30s.
        candidates = unique[:200]
        with ThreadPoolExecutor(max_workers=10) as ex:
            futures = {ex.submit(check_link, l["url"]): l for l in candidates}
            for fut in as_completed(futures):
                status = fut.result()
                if status in (404, 410, 0):
                    link = futures[fut]
                    link["status"] = status
                    broken.append(link)
    return {"site": site_slug, "total_links": len(unique), "broken_links": broken,
            "broken_count": len(broken)}


def build_report(site_slug: str, audit: dict, is_preview: bool = False) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    suffix = "preview" if is_preview else "full"
    path = REPORTS_DIR / f"{site_slug}_{datetime.now():%Y%m%d}_{suffix}.md"
    broken = audit.get("broken_links", [])
    show = broken[:5] if is_preview else broken
    lines = [
        f"# Dead Link Audit — {site_slug}",
        f"_{datetime.now():%B %d, %Y}_\n",
        f"- Total links scanned: {audit.get('total_links', 0)}",
        f"- Broken (404/410/unreachable): {audit.get('broken_count', 0)}",
    ]
    if is_preview and len(broken) > 5:
        lines.append(f"\n_Preview shows 5 of {len(broken)}. Unlock full report: $97 → paypal.me/wholesaleomniverse/97_\n")
    for l in show:
        lines.append(f"\n## {l['url']}")
        lines.append(f"- **Status:** {l.get('status', '?')}")
        lines.append(f"- **Anchor text:** {l['anchor']}")
        lines.append(f"- **Found in:** {l['source_file']}")
        lines.append(f"- **Action:** replace with current resource or remove")
    path.write_text("\n".join(lines))
    return path


def _unwrap_bing_redirect(url: str) -> str:
    """Bing wraps result URLs in /ck/a?u=a1<base64>. Decode to the actual destination."""
    if "/ck/a" not in url:
        return url
    import base64
    from urllib.parse import urlparse, parse_qs
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
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 LinkMender/1.0"}
    try:
        resp = requests.get(
            f"https://www.bing.com/search?q={requests.utils.quote(query)}",
            headers=headers, timeout=10,
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


def _url_to_slug(url: str) -> str:
    host = urlparse(url).netloc.lower().replace("www.", "")
    path = urlparse(url).path.strip("/").replace("/", "-")[:60]
    raw = f"{host}_{path}" if path else host
    return re.sub(r"[^a-z0-9._-]", "-", raw)[:80]


def _fetch_html(url: str, timeout: int = 12) -> str:
    try:
        import requests
    except ImportError:
        return ""
    try:
        resp = requests.get(url, timeout=timeout,
                            headers={"User-Agent": "Mozilla/5.0 LinkMender/1.0"})
        if resp.status_code == 200 and "text/html" in resp.headers.get("content-type", ""):
            return resp.text
    except Exception:
        pass
    return ""


def _extract_contact_email(html: str) -> str:
    # First, mailto: links — most reliable signal
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


def discover_prospects(query: str = "", max_new: int = 8) -> dict:
    """Find candidate sites, snapshot HTML, save as prospects with contact info."""
    if not query:
        # Rotate through default queries so we don't hit the same Bing results every run.
        query = DEFAULT_PROSPECT_QUERIES[datetime.now().day % len(DEFAULT_PROSPECT_QUERIES)]

    existing = storage.load("lm_prospects.json", [])
    existing_slugs = {p["site_slug"] for p in existing}

    results = _bing_search(query, n=max_new * 2)
    added = []
    for r in results:
        if len(added) >= max_new:
            break
        url = r["url"]
        slug = _url_to_slug(url)
        if slug in existing_slugs or not slug:
            continue
        html = _fetch_html(url)
        if not html or len(html) < 1000:
            continue
        contact = _extract_contact_email(html)
        if not contact:
            continue
        snap_dir = SNAPSHOT_DIR / slug
        snap_dir.mkdir(parents=True, exist_ok=True)
        (snap_dir / "index.html").write_text(html, errors="ignore")
        existing.append({
            "site_slug":     slug,
            "url":           url,
            "title":         r.get("title", ""),
            "contact_email": contact,
            "discovered_at": datetime.now().isoformat(),
            "status":        "discovered",
        })
        added.append(slug)

    storage.save("lm_prospects.json", existing)
    return {"discovered": len(added), "query": query, "new_slugs": added}


def acquire_cycle() -> dict:
    """Run preview audits on prospect sites and send to owners."""
    prospects = storage.load("lm_prospects.json", [])
    sent = 0
    new_audits = 0
    for p in prospects:
        if p.get("status") in ("contacted", "client"):
            continue
        audit = audit_site(p["site_slug"], deep_check=True)
        if audit.get("broken_count", 0) < 3:
            p["status"] = "insufficient_signal"
            continue
        report = build_report(p["site_slug"], audit, is_preview=True)
        new_audits += 1
        if p.get("contact_email"):
            body = (
                f"Hi — I run an SEO audit service. I scanned {p['site_slug']} and found "
                f"{audit['broken_count']} dead outbound links that are bleeding link equity.\n\n"
                f"Free preview attached (5 of {audit['broken_count']}).\n\n"
                f"Full report with every broken link + suggested replacements: $97.\n"
                f"Monthly monitoring: $47/mo.\n\n"
                f"PayPal: paypal.me/wholesaleomniverse/97\n"
                f"Reply with your access key after payment for full delivery."
            )
            result = mailer.send(AGENT_KEY, p["contact_email"],
                                 f"{audit['broken_count']} broken links found on {p['site_slug']}",
                                 body, purpose="outreach", attachments=[str(report)])
            if result.get("status") == "sent":
                p["status"] = "contacted"
                p["contacted_at"] = datetime.now().isoformat()
                sent += 1
    storage.save("lm_prospects.json", prospects)
    return {"new_audits": new_audits, "outreach_sent": sent}


def fulfill_cycle() -> dict:
    clients = storage.load("lm_clients.json", [])
    sent = 0
    for c in clients:
        if c.get("status") != "active":
            continue
        audit = audit_site(c["site_slug"], deep_check=True)
        report = build_report(c["site_slug"], audit, is_preview=False)
        body = (
            f"Hi {c.get('contact_name', 'there')},\n\n"
            f"Monthly audit for {c['site_slug']} — {audit['broken_count']} broken links found.\n\n"
            f"Full report attached.\n\n"
            f"— LinkMender, Wholesale Omniverse LLC"
        )
        result = mailer.send(AGENT_KEY, c["contact_email"],
                             f"Monthly dead-link audit — {c['site_slug']}",
                             body, purpose="fulfillment", attachments=[str(report)])
        if result.get("status") == "sent":
            sent += 1
    return {"fulfillment_sent": sent}


def run_full_cycle() -> dict:
    d = discover_prospects()
    a = acquire_cycle()
    f = fulfill_cycle()
    rev = billing.revenue_summary(AGENT_KEY)
    clients = storage.load("lm_clients.json", [])
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
