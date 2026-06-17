"""
Client Prospector — finds *paying clients* (wholesalers, investors, flippers)
for the Wholesale Deal Analyzer (SAAS) and Outreach-as-a-Service (OAS) products.

Reuses buyer_finder's Hotfrog scraping infrastructure but with different
search terms and a sales pitch email instead of the "I'm buying houses" intro.
"""
import sys
import json
import time
import datetime
import requests
from pathlib import Path
from bs4 import BeautifulSoup

PARENT = Path(__file__).parent.parent
sys.path.insert(0, str(PARENT))

from buyer_finder.tools import (
    HEADERS, _extract_emails, _extract_phones,
    _scrape_website_for_email, _guess_email_from_domain,
)
from email_template import send_branded_email

DATA_DIR        = PARENT / "data"
PROSPECTS_FILE  = DATA_DIR / "prospects.json"
PITCH_LOG_FILE  = DATA_DIR / "pitch_log.json"

# Domains that aren't real businesses — directory aggregators, registrars, social.
JUNK_DOMAINS = {
    "hotfrog", "yelp", "manta", "yellowpages", "bbb.org",
    "godaddy", "wix.com", "weebly", "facebook", "instagram",
    "linkedin", "twitter", "youtube", "google.com", "bing.com",
    "wholesaleomniverse",
}


def _is_junk_email(email: str) -> bool:
    e = email.lower()
    return any(d in e for d in JUNK_DOMAINS)


def _is_junk_website(url: str) -> bool:
    u = url.lower()
    return any(d in u for d in JUNK_DOMAINS)

HOTFROG_QUERIES = [
    "real-estate-investors",
    "real-estate-wholesalers",
]

PRODUCT_INFO = {
    "saas": {
        "name":   "Wholesale Deal Analyzer",
        "price":  "$197/month",
        "hook":   "AI-powered deal analysis — pulls comps, estimates ARV, calculates your max offer, and drafts LOIs in 30 seconds.",
        "cta":    "Reply 'YES' for a free 7-day trial and I'll set up your account today.\n\nOr see all our tools at: https://kodelyoko1.github.io/Kode-Claude/pricing.html",
    },
    "oas": {
        "name":   "Outreach-as-a-Service",
        "price":  "starting at $300/month",
        "hook":   "We run your motivated-seller outreach for you — scrape distressed leads from gov records, send weekly campaigns, and deliver hot-lead reports to your inbox.",
        "cta":    "Reply 'YES' and I'll send you a sample lead report from your market — no obligation.\n\nFull details: https://kodelyoko1.github.io/Kode-Claude/pricing.html",
    },
}


def _load(path: Path, default):
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return default


def _save(path: Path, data):
    DATA_DIR.mkdir(exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def _now():
    return datetime.datetime.now().isoformat()


def _prospect_exists(email: str) -> bool:
    if not email:
        return False
    prospects = _load(PROSPECTS_FILE, {})
    return any(p.get("email", "").lower() == email.lower() for p in prospects.values())


def _save_prospect(name, email, phone, website, market, source, product) -> str:
    prospects = _load(PROSPECTS_FILE, {})
    pid = f"PRO-{len(prospects)+1:05d}"
    while pid in prospects:
        pid = f"PRO-{len(prospects)+2:05d}"
    prospects[pid] = {
        "prospect_id": pid,
        "name": name[:80],
        "email": email,
        "phone": phone,
        "website": website,
        "market": market,
        "source": source,
        "product_pitched": product,
        "status": "new",
        "pitched_at": "",
        "replied": False,
        "converted_client_id": "",
        "created_at": _now(),
    }
    _save(PROSPECTS_FILE, prospects)
    return pid


def find_prospects_hotfrog(city: str, state: str, max_per_query: int = 5) -> dict:
    """
    Scrape Hotfrog for real estate wholesalers/investors/flippers in a market.
    Saves new prospects to data/prospects.json.
    """
    state_lower = state.lower().strip()
    city_slug   = city.lower().replace(" ", "-")
    found = []

    for query in HOTFROG_QUERIES:
        url = f"https://www.hotfrog.com/search/{city_slug}-{state_lower}/{query}"
        try:
            resp = requests.get(url, headers=HEADERS, timeout=12)
            if resp.status_code != 200:
                continue
            soup = BeautifulSoup(resp.text, "lxml")
            links = [(a.get_text(strip=True), a["href"])
                     for a in soup.select('a[href*="/company/"]')
                     if a.get_text(strip=True)][:max_per_query]

            for biz_name, href in links:
                full_url = f"https://www.hotfrog.com{href}" if href.startswith("/") else href
                try:
                    detail = requests.get(full_url, headers=HEADERS, timeout=10)
                    d_soup = BeautifulSoup(detail.text, "lxml")
                    d_text = d_soup.get_text(" ", strip=True)

                    phones  = _extract_phones(d_text)
                    emails  = _extract_emails(d_text)
                    phone   = phones[0] if phones else ""

                    website_link = ""
                    for a in d_soup.select("a[href]"):
                        h = a.get("href", "")
                        if (h.startswith("http") and "hotfrog.com" not in h
                                and not _is_junk_website(h)):
                            website_link = h
                            break

                    if not emails and website_link:
                        email_from_site = _scrape_website_for_email(website_link)
                        if email_from_site:
                            emails = [email_from_site]
                    if not emails and website_link:
                        guessed = _guess_email_from_domain(website_link)
                        if guessed:
                            emails = [guessed]

                    # Filter out aggregator/junk emails
                    emails = [e for e in emails if not _is_junk_email(e)]

                    email = emails[0] if emails else ""
                    if not email or _prospect_exists(email):
                        continue

                    pid = _save_prospect(
                        name=biz_name,
                        email=email,
                        phone=phone,
                        website=website_link,
                        market=f"{city}, {state}",
                        source=f"Hotfrog/{query}",
                        product="saas",   # default — runner can override
                    )
                    found.append({"prospect_id": pid, "name": biz_name, "email": email})
                    time.sleep(0.5)
                except Exception:
                    continue
        except Exception:
            continue
        time.sleep(0.4)

    return {
        "city": city,
        "state": state,
        "prospects_found": len(found),
        "prospects": found,
    }


def _pitch_html(name: str, market: str, product: str) -> str:
    info = PRODUCT_INFO.get(product, PRODUCT_INFO["saas"])
    first_name = name.split()[0] if name else "there"
    return (
        f"Hi <strong>{first_name}</strong>,<br><br>"
        f"I'm Tyreese with Wholesale Omniverse. I saw you're active in <strong>{market}</strong> "
        f"and wanted to reach out about <strong>{info['name']}</strong>.<br><br>"
        f"{info['hook']}<br><br>"
        f"Wholesalers using it close 2–3 extra deals per month because they can analyze and respond to "
        f"motivated sellers in minutes instead of hours.<br><br>"
        f"<strong>{info['cta']}</strong><br><br>"
        f"If it's not a fit, no problem — just hit reply with 'NO' and I won't follow up."
    )


def _pitch_text(name: str, market: str, product: str) -> str:
    info = PRODUCT_INFO.get(product, PRODUCT_INFO["saas"])
    first_name = name.split()[0] if name else "there"
    return (
        f"Hi {first_name},\n\n"
        f"I'm Tyreese with Wholesale Omniverse. I saw you're active in {market} "
        f"and wanted to reach out about {info['name']} ({info['price']}).\n\n"
        f"{info['hook']}\n\n"
        f"Wholesalers using it close 2-3 extra deals per month because they can analyze and respond to "
        f"motivated sellers in minutes instead of hours.\n\n"
        f"{info['cta']}\n\n"
        f"If it's not a fit, no problem — just hit reply with 'NO' and I won't follow up.\n\n"
        f"— Tyreese Lumiere, Wholesale Omniverse LLC"
    )


def pitch_prospect(prospect_id: str, product: str = "saas") -> dict:
    """Send the SAAS or OAS pitch email to one prospect."""
    prospects = _load(PROSPECTS_FILE, {})
    p = prospects.get(prospect_id)
    if not p:
        return {"error": f"Prospect {prospect_id} not found"}
    if p.get("status") == "pitched":
        return {"status": "already_pitched", "prospect_id": prospect_id}
    if not p.get("email"):
        return {"error": "No email on file"}

    info = PRODUCT_INFO.get(product, PRODUCT_INFO["saas"])
    subject = f"Quick question about your deals in {p['market']}"
    body_text = _pitch_text(p["name"], p["market"], product)
    body_html = _pitch_html(p["name"], p["market"], product)

    result = send_branded_email(
        to_email=p["email"],
        subject=subject,
        body_text=body_text,
        body_html_inner=body_html,
    )

    if result.get("status") == "sent":
        prospects[prospect_id]["status"] = "pitched"
        prospects[prospect_id]["pitched_at"] = _now()
        prospects[prospect_id]["product_pitched"] = product
        _save(PROSPECTS_FILE, prospects)

        log = _load(PITCH_LOG_FILE, [])
        log.append({
            "prospect_id": prospect_id,
            "email": p["email"],
            "product": product,
            "sent_at": _now(),
        })
        _save(PITCH_LOG_FILE, log)

    return {"prospect_id": prospect_id, "email": p["email"], "result": result}


def pitch_all_new(product: str = "saas", limit: int = 25) -> dict:
    """Send pitch emails to every prospect with status='new'."""
    prospects = _load(PROSPECTS_FILE, {})
    new_ones = [pid for pid, p in prospects.items() if p.get("status") == "new"][:limit]
    sent = 0
    failures = []
    for pid in new_ones:
        out = pitch_prospect(pid, product=product)
        if out.get("result", {}).get("status") == "sent":
            sent += 1
        else:
            failures.append({"prospect_id": pid, "error": out.get("result", {}).get("error", out.get("error"))})
        time.sleep(1.5)
    return {"product": product, "sent": sent, "attempted": len(new_ones), "failures": failures}


def list_prospects(status: str = "") -> dict:
    prospects = _load(PROSPECTS_FILE, {})
    items = list(prospects.values())
    if status:
        items = [p for p in items if p.get("status") == status]
    return {
        "total": len(prospects),
        "filtered": len(items),
        "new":      sum(1 for p in prospects.values() if p.get("status") == "new"),
        "pitched":  sum(1 for p in prospects.values() if p.get("status") == "pitched"),
        "replied":  sum(1 for p in prospects.values() if p.get("replied")),
        "converted": sum(1 for p in prospects.values() if p.get("converted_client_id")),
        "prospects": items,
    }


def mark_replied(prospect_id: str, notes: str = "") -> dict:
    prospects = _load(PROSPECTS_FILE, {})
    if prospect_id not in prospects:
        return {"error": f"Prospect {prospect_id} not found"}
    prospects[prospect_id]["replied"] = True
    prospects[prospect_id]["status"] = "replied"
    prospects[prospect_id]["reply_notes"] = notes
    prospects[prospect_id]["replied_at"] = _now()
    _save(PROSPECTS_FILE, prospects)
    return {"status": "marked_replied", "prospect_id": prospect_id}


def mark_converted(prospect_id: str, client_id: str) -> dict:
    """Link a prospect to a paying client record (SAAS-xxxx or OAS-xxxx)."""
    prospects = _load(PROSPECTS_FILE, {})
    if prospect_id not in prospects:
        return {"error": f"Prospect {prospect_id} not found"}
    prospects[prospect_id]["converted_client_id"] = client_id
    prospects[prospect_id]["status"] = "converted"
    _save(PROSPECTS_FILE, prospects)
    return {"status": "converted", "prospect_id": prospect_id, "client_id": client_id}
