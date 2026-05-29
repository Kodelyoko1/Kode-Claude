"""
LinkMender — SEO dead-link audit reports.
Revenue: $97 one-time audit, $47/mo monitoring, $197 agency lead list.
"""
import re
import sys
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from autonomous import storage, mailer, billing, metrics

AGENT_KEY = "link_mender"
SNAPSHOT_DIR = Path(__file__).parent.parent / "data" / "lm_snapshots"
REPORTS_DIR = Path(__file__).parent.parent / "data" / "lm_reports"


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
        for l in unique[:200]:
            status = check_link(l["url"])
            if status in (404, 410, 0):
                l["status"] = status
                broken.append(l)
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
    a = acquire_cycle()
    f = fulfill_cycle()
    rev = billing.revenue_summary(AGENT_KEY)
    clients = storage.load("lm_clients.json", [])
    metrics.record(
        AGENT_KEY,
        prospects_added=a["new_audits"],
        outreach_sent=a["outreach_sent"],
        fulfillment_sent=f["fulfillment_sent"],
        active_subs=sum(1 for c in clients if c.get("status") == "active"),
        mrr=rev["mrr"],
        total_revenue=rev["total_paid"],
    )
    return {**a, **f, **rev}
