"""
Cash Buyer Recruitment Agent tools.
Finds active real estate investors and adds them to your buyers list.
No API keys required — web scraping + SMTP only.
"""
import json
import os
import re
import time
import smtplib
import datetime
import requests
from pathlib import Path
from bs4 import BeautifulSoup
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.image import MIMEImage

DATA_DIR   = Path(__file__).parent.parent / "data"
BUYERS_FILE  = DATA_DIR / "cash_buyers.json"
EMAIL_LOG    = DATA_DIR / "email_log.json"
BOUNCED_FILE = DATA_DIR / "bf_bounced.json"

COMPANY_NAME  = "Wholesale Omniverse LLC"
COMPANY_EMAIL = "info@wholesaleomniverse.com"
SENDER_PHONE  = "207-385-4041"
SENDER_NAME   = "Tyreese Lumiere"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

SKIP_EMAILS = {"noreply", "example", "webmaster", "admin@", "support@", "no-reply",
               "donotreply", "postmaster", "privacy@", "legal@", "press@",
               "hotfrog", "godaddy", "filler@", "placeholder", "info@2x"}

# Directory/platform domains — never email these directly
SKIP_DOMAINS = {"hotfrog.", "reia.", "godaddy.", "squarespace.", "wix.com",
                "weebly.", "wordpress.com", "blogspot.", "tumblr."}


def _is_bounced(email: str) -> bool:
    try:
        bounced = json.loads(BOUNCED_FILE.read_text()) if BOUNCED_FILE.exists() else []
        return email.lower() in [b.lower() for b in bounced]
    except Exception:
        return False


def _mark_bounced(email: str) -> None:
    try:
        bounced = json.loads(BOUNCED_FILE.read_text()) if BOUNCED_FILE.exists() else []
        if email.lower() not in [b.lower() for b in bounced]:
            bounced.append(email.lower())
            DATA_DIR.mkdir(exist_ok=True)
            BOUNCED_FILE.write_text(json.dumps(bounced, indent=2))
    except Exception:
        pass


def _load(path, default):
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return default


def _save(path, data):
    DATA_DIR.mkdir(exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def _now():
    return datetime.datetime.now().isoformat()


def _bing(query: str, n: int = 6) -> list:
    results = []
    try:
        resp = requests.get(
            f"https://www.bing.com/search?q={requests.utils.quote(query)}",
            headers=HEADERS, timeout=10,
        )
        soup = BeautifulSoup(resp.text, "lxml")
        for li in soup.select("li.b_algo")[:n]:
            title_el   = li.select_one("h2 a")
            snippet_el = li.select_one(".b_caption p") or li.select_one("p")
            if title_el:
                results.append({
                    "title":   title_el.get_text(strip=True),
                    "url":     title_el.get("href", ""),
                    "snippet": snippet_el.get_text(strip=True) if snippet_el else "",
                })
    except Exception:
        pass
    return results


def _extract_emails(text: str) -> list:
    raw = re.findall(r'[\w.+-]+@[\w-]+\.[a-zA-Z]{2,}', text)
    return [e for e in set(raw) if not any(s in e.lower() for s in SKIP_EMAILS)]


def _extract_phones(text: str) -> list:
    return list(set(re.findall(r'\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}', text)))


def _buyer_exists(email: str) -> bool:
    buyers = _load(BUYERS_FILE, {})
    return any(b.get("email", "").lower() == email.lower() for b in buyers.values())


def _save_buyer(name: str, email: str, phone: str, markets: str, source: str, notes: str = "") -> str:
    buyers = _load(BUYERS_FILE, {})
    buyer_id = f"BUYER-{len(buyers)+1:04d}"
    buyers[buyer_id] = {
        "buyer_id":   buyer_id,
        "name":       name,
        "email":      email,
        "phone":      phone,
        "markets":    markets,
        "buy_box":    "Single family, distressed/as-is, cash",
        "source":     source,
        "deals_closed": 0,
        "notes":      notes,
        "added_at":   _now(),
    }
    _save(BUYERS_FILE, buyers)
    return buyer_id


def _send_smtp(to_email: str, subject: str, body_text: str, body_html: str = "") -> dict:
    if _is_bounced(to_email):
        return {"status": "skipped_bounced"}

    smtp_host = os.environ.get("SMTP_HOST", "")
    smtp_user = os.environ.get("SMTP_USER", "")
    smtp_pass = os.environ.get("SMTP_PASS", "")
    smtp_port = int(os.environ.get("SMTP_PORT", 587))
    if not all([smtp_host, smtp_user, smtp_pass]):
        return {"status": "smtp_not_configured"}
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).parent.parent))
    from email_template import check_and_reserve_send, release_send
    quota = check_and_reserve_send()
    if not quota["ok"]:
        return {"status": "quota_exceeded", "count": quota["count"], "cap": quota["cap"]}
    try:
        if body_html and LOGO_PATH.exists():
            outer = MIMEMultipart("mixed")
            outer["Subject"] = subject
            outer["From"]    = f"{SENDER_NAME} <{smtp_user}>"
            outer["To"]      = to_email

            related = MIMEMultipart("related")
            alt = MIMEMultipart("alternative")
            alt.attach(MIMEText(body_text, "plain"))
            alt.attach(MIMEText(body_html, "html"))
            related.attach(alt)

            img = MIMEImage(LOGO_PATH.read_bytes(), _subtype="png")
            img.add_header("Content-ID", "<wo_logo>")
            img.add_header("Content-Disposition", "inline", filename="logo.png")
            related.attach(img)
            outer.attach(related)
            msg = outer
        else:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"]    = f"{SENDER_NAME} <{smtp_user}>"
            msg["To"]      = to_email
            msg.attach(MIMEText(body_text, "plain"))
            if body_html:
                msg.attach(MIMEText(body_html, "html"))

        import socket as _socket
        _addrs = _socket.getaddrinfo(smtp_host, smtp_port, _socket.AF_INET)
        _ip = _addrs[0][4][0] if _addrs else smtp_host
        with smtplib.SMTP(_ip, smtp_port) as s:
            s.ehlo(smtp_host); s.starttls(); s.login(smtp_user, smtp_pass)
            s.sendmail(smtp_user, to_email, msg.as_string())
        return {"status": "sent"}
    except smtplib.SMTPRecipientsRefused:
        release_send()
        _mark_bounced(to_email)
        return {"status": "bounced", "email": to_email}
    except Exception as e:
        release_send()
        err = str(e)
        # 550/551/553 = permanent failure — mark bounced so we never retry
        if any(code in err for code in ["550", "551", "553", "4.4.4", "5.1.1", "5.1.2"]):
            _mark_bounced(to_email)
        return {"status": "failed", "error": err}


BUYER_INTRO_TEMPLATE_TEXT = """Hi {name},

My name is Tyreese Lumiere with Wholesale Omniverse LLC. I'm a real estate wholesaler actively working deals in {markets} and I wanted to connect.

I regularly get properties under contract that match your profile:
  - Single family, distressed/as-is condition
  - Priced 20-40% below ARV
  - Ready to assign quickly

I'm building my priority buyers list - if you're actively buying in {markets}, I'd love to send you deal packets as they come in.

No obligation. If a deal fits your criteria, we move. If not, no problem.

Reply and let me know what your buy box looks like and I'll keep you top of mind.

Tyreese Lumiere
{sender_email}
{phone}
{company}"""


import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from email_template import send_branded_email as _send_branded

LOGO_PATH = Path(__file__).parent.parent / "data" / "logo.png"


def _buyer_intro_html(name: str, markets: str) -> str:
    return f"""
<p style="margin:0 0 16px 0;color:#cccccc;">Hi {name},</p>
<p style="margin:0 0 16px 0;color:#cccccc;">
  My name is <strong>Tyreese Lumiere</strong> with Wholesale Omniverse LLC.
  I'm a real estate wholesaler actively working deals in <strong>{markets}</strong> and I wanted to connect.
</p>
<p style="margin:0 0 12px 0;color:#cccccc;">I regularly get properties under contract that match your profile:</p>
<table cellpadding="0" cellspacing="0" style="margin:0 0 20px 0;">
  <tr><td style="padding:4px 0;color:#cccccc;"><span style="color:#FDD023;font-weight:bold;">&#10003;</span>&nbsp; Single family, distressed/as-is condition</td></tr>
  <tr><td style="padding:4px 0;color:#cccccc;"><span style="color:#FDD023;font-weight:bold;">&#10003;</span>&nbsp; Priced 20&ndash;40% below ARV</td></tr>
  <tr><td style="padding:4px 0;color:#cccccc;"><span style="color:#FDD023;font-weight:bold;">&#10003;</span>&nbsp; Ready to assign quickly</td></tr>
</table>
<p style="margin:0 0 16px 0;color:#cccccc;">
  I'm building my <strong>priority buyers list</strong> &mdash; if you're actively buying in {markets},
  I'd love to send you deal packets as they come in.
</p>
<p style="margin:0 0 24px 0;color:#cccccc;">
  No obligation. If a deal fits your criteria, we move. If not, no problem.
  <strong>Reply and let me know what your buy box looks like</strong> and I'll keep you top of mind.
</p>
<p style="margin:0;color:#cccccc;">Talk soon,</p>"""


BUYER_INTRO_TEMPLATE = BUYER_INTRO_TEMPLATE_TEXT


REIA_SITES = {
    # ── Core 5 markets ──────────────────────────────────────────────────────
    ("memphis",        "tn"): [
        "https://www.memphisinvestorsgroup.com/",
        "https://www.memphisreia.com/",
        "https://midtennesseereia.com/",
    ],
    ("detroit",        "mi"): [
        "https://www.michiganreia.com/",
        "https://www.detroitreia.com/",
        "https://glreia.com/",
    ],
    ("cleveland",      "oh"): [
        "https://www.clevelandreia.com/",
        "https://ohreia.com/",
        "https://www.ncreia.com/",
    ],
    ("baltimore",      "md"): [
        "https://www.mdreia.com/",
        "https://www.bmorereia.com/",
        "https://ccreia.com/",
    ],
    ("chicago",        "il"): [
        "https://www.chicagoreia.com/",
        "https://illinoisreia.com/",
        "https://chicagolandreia.com/",
    ],
    # ── Southeast ────────────────────────────────────────────────────────────
    ("atlanta",        "ga"): [
        "https://www.atlantareia.com/",
        "https://gareia.org/",
        "https://www.atlantainvestorsalliance.com/",
    ],
    ("birmingham",     "al"): [
        "https://www.birminghamreia.com/",
        "https://www.alabamareia.com/",
        "https://www.centralalabamarealestateinvestors.com/",
    ],
    ("jacksonville",   "fl"): [
        "https://www.jaxreia.com/",
        "https://www.flreia.com/",
        "https://www.nefba.com/",
    ],
    ("tampa",          "fl"): [
        "https://www.tampareia.com/",
        "https://www.flreia.com/",
        "https://www.tbarea.reia.com/",
    ],
    ("charlotte",      "nc"): [
        "https://www.charlottereia.com/",
        "https://www.ncreia.com/",
        "https://www.piedmontreia.com/",
    ],
    ("nashville",      "tn"): [
        "https://www.nashvillereia.com/",
        "https://midtennesseereia.com/",
        "https://www.tnreia.com/",
    ],
    ("new orleans",    "la"): [
        "https://www.nolareia.com/",
        "https://www.louisianarei.com/",
        "https://www.gnoreia.org/",
    ],
    # ── Midwest ──────────────────────────────────────────────────────────────
    ("kansas city",    "mo"): [
        "https://www.kcreia.com/",
        "https://www.heartlandreia.com/",
        "https://www.kansascityreia.org/",
    ],
    ("indianapolis",   "in"): [
        "https://www.indyreia.com/",
        "https://www.indianareia.com/",
        "https://www.cireia.com/",
    ],
    ("st. louis",      "mo"): [
        "https://www.stlreia.com/",
        "https://www.missourireia.com/",
        "https://www.stlouisreia.org/",
    ],
    ("columbus",       "oh"): [
        "https://www.creia.net/",
        "https://ohreia.com/",
        "https://www.columbusreia.com/",
    ],
    ("milwaukee",      "wi"): [
        "https://www.milwaukeereia.com/",
        "https://www.wireia.com/",
        "https://www.southeasternwisconsinreia.com/",
    ],
    # ── Southwest / West ─────────────────────────────────────────────────────
    ("houston",        "tx"): [
        "https://www.houstonreia.com/",
        "https://texasreia.com/",
        "https://www.houstoncashflowconference.com/",
    ],
    ("dallas",         "tx"): [
        "https://www.dallasreia.com/",
        "https://dfwreia.com/",
        "https://www.dallasfortworth.reia.com/",
    ],
    ("san antonio",    "tx"): [
        "https://www.sareia.com/",
        "https://texasreia.com/",
        "https://www.sanantoniorealestateinvestors.com/",
    ],
    ("phoenix",        "az"): [
        "https://www.azreia.com/",
        "https://www.phoenixreia.com/",
        "https://www.arizonareia.com/",
    ],
    ("las vegas",      "nv"): [
        "https://www.lvreia.com/",
        "https://www.nvreia.com/",
        "https://www.southernnevadareia.com/",
    ],
    # ── Northeast ────────────────────────────────────────────────────────────
    ("philadelphia",   "pa"): [
        "https://www.pareia.com/",
        "https://phillyreia.com/",
        "https://www.delvalinvestors.com/",
    ],
    ("pittsburgh",     "pa"): [
        "https://www.pittsburghreia.com/",
        "https://pareia.com/",
        "https://www.pghreia.com/",
    ],
    ("new york",       "ny"): [
        "https://www.nyreia.com/",
        "https://www.njreia.com/",
        "https://www.nycreia.com/",
    ],
    ("buffalo",        "ny"): [
        "https://www.buffaloreia.com/",
        "https://nyreia.com/",
        "https://www.wnyrei.com/",
    ],
    ("boston",         "ma"): [
        "https://www.mareia.com/",
        "https://www.bostonreia.com/",
        "https://www.newenglandreia.com/",
    ],
}


def find_buyers_reia(city: str, state: str) -> dict:
    """Scrape REIA/investor association sites for the target market to find member emails."""
    key   = (city.lower().strip(), state.lower().strip())
    sites = REIA_SITES.get(key, [])

    # Fallback: try constructing a likely REIA URL
    if not sites:
        slug = city.lower().replace(" ", "")
        sites = [
            f"https://www.{slug}reia.com/",
            f"https://{slug}reia.com/",
            f"https://www.{slug}investorsgroup.com/",
        ]

    found      = []
    seen_emails = set()

    for url in sites:
        try:
            resp = requests.get(url, headers=HEADERS, timeout=12)
            if resp.status_code != 200:
                continue
            soup   = BeautifulSoup(resp.text, "lxml")
            text   = soup.get_text(" ", strip=True)
            emails = _extract_emails(text)
            phones = _extract_phones(text)

            # Also check /members, /about, /contact subpages
            for subpage in ["/members", "/about", "/contact", "/join", "/directory"]:
                try:
                    sub = requests.get(url.rstrip("/") + subpage, headers=HEADERS, timeout=8)
                    if sub.status_code == 200:
                        sub_text   = BeautifulSoup(sub.text, "lxml").get_text(" ", strip=True)
                        emails    += _extract_emails(sub_text)
                        phones    += _extract_phones(sub_text)
                except Exception:
                    pass
                time.sleep(0.3)

            for email in set(emails):
                if email in seen_emails or _buyer_exists(email):
                    continue
                seen_emails.add(email)
                name  = re.sub(r'https?://|www\.', '', url).split(".")[0].replace("-", " ").title()
                phone = phones[0] if phones else ""
                buyer_id = _save_buyer(
                    name=f"{name} — {city} Investor",
                    email=email, phone=phone,
                    markets=f"{city}, {state}",
                    source=f"REIA site: {url}",
                )
                found.append({"buyer_id": buyer_id, "name": name, "email": email})
        except Exception:
            pass
        time.sleep(0.5)

    return {
        "city":         city,
        "state":        state,
        "source":       "REIA sites",
        "sites_checked": len(sites),
        "buyers_found": len(found),
        "buyers":       found,
    }


def _scrape_website_for_email(website_url: str) -> str:
    """
    Visit a business website and its /contact page to find an email address.
    Returns the first valid email found, or empty string.
    """
    if not website_url or not website_url.startswith("http"):
        return ""
    for path in ["", "/contact", "/contact-us", "/about", "/about-us"]:
        try:
            resp = requests.get(website_url.rstrip("/") + path, headers=HEADERS, timeout=8)
            if resp.status_code == 200:
                emails = _extract_emails(resp.text)
                if emails:
                    return emails[0]
        except Exception:
            pass
        time.sleep(0.3)
    return ""


def _guess_email_from_domain(website_url: str) -> str:
    """Try common email prefixes for a domain when none is found on the page."""
    try:
        domain = re.sub(r'https?://(www\.)?', '', website_url).split("/")[0].strip()
        if not domain or "." not in domain:
            return ""
        bad = {"gmail", "yahoo", "hotmail", "outlook"} | SKIP_DOMAINS
        if any(s in domain for s in bad):
            return ""
        for prefix in ["info", "contact", "hello", "invest"]:
            return f"{prefix}@{domain}"
    except Exception:
        pass
    return ""


def find_buyers_hotfrog(city: str, state: str) -> dict:
    """
    Scrape Hotfrog for real estate investor businesses.
    For each listing: tries to find email on their own website before saving.
    """
    state_lower = state.lower().strip()
    city_slug   = city.lower().replace(" ", "-")
    url = f"https://www.hotfrog.com/search/{city_slug}-{state_lower}/real-estate-investors"

    found = []
    try:
        resp = requests.get(url, headers=HEADERS, timeout=12)
        if resp.status_code != 200:
            return {"city": city, "state": state, "source": "Hotfrog", "buyers_found": 0}

        soup  = BeautifulSoup(resp.text, "lxml")
        links = [(a.get_text(strip=True), a["href"])
                 for a in soup.select('a[href*="/company/"]')
                 if a.get_text(strip=True)][:10]

        for biz_name, href in links:
            full_url = f"https://www.hotfrog.com{href}" if href.startswith("/") else href
            try:
                detail = requests.get(full_url, headers=HEADERS, timeout=10)
                d_soup = BeautifulSoup(detail.text, "lxml")
                d_text = d_soup.get_text(" ", strip=True)

                phones  = _extract_phones(d_text)
                emails  = _extract_emails(d_text)
                phone   = phones[0] if phones else ""

                # Extract business website link from the Hotfrog page
                website_link = ""
                for a in d_soup.select("a[href]"):
                    href_val = a.get("href", "")
                    if href_val.startswith("http") and not any(s in href_val for s in SKIP_DOMAINS):
                        website_link = href_val
                        break

                # If no email on Hotfrog page, visit their website
                if not emails and website_link:
                    email_from_site = _scrape_website_for_email(website_link)
                    if email_from_site:
                        emails = [email_from_site]

                # Last resort: guess info@domain from their website
                if not emails and website_link:
                    guessed = _guess_email_from_domain(website_link)
                    if guessed:
                        emails = [guessed]

                if not phone and not emails:
                    continue

                email = emails[0] if emails else ""
                if email and _buyer_exists(email):
                    continue

                buyer_id = _save_buyer(
                    name=biz_name[:60], email=email, phone=phone,
                    markets=f"{city}, {state}",
                    source=f"Hotfrog: {full_url}",
                    notes=f"Website: {website_link}" if website_link else "",
                )
                found.append({"buyer_id": buyer_id, "name": biz_name, "email": email, "phone": phone})
                time.sleep(0.5)
            except Exception:
                continue

    except Exception as e:
        return {"city": city, "state": state, "source": "Hotfrog", "buyers_found": 0, "error": str(e)}

    return {
        "city":         city,
        "state":        state,
        "source":       "Hotfrog",
        "buyers_found": len(found),
        "buyers":       found,
    }


def find_buyers_bing(city: str, state: str, max_results: int = 10) -> dict:
    """Wrapper kept for API compatibility — now delegates to REIA + Hotfrog."""
    reia   = find_buyers_reia(city, state)
    hotfrog = find_buyers_hotfrog(city, state)
    all_buyers = reia.get("buyers", []) + hotfrog.get("buyers", [])
    return {
        "city":         city,
        "state":        state,
        "source":       "REIA sites + Hotfrog",
        "buyers_found": len(all_buyers),
        "buyers":       all_buyers,
    }


def find_buyers_craigslist(city: str, state: str) -> dict:
    """Craigslist now JS-renders its results and blocks scrapers — delegates to Hotfrog instead."""
    return find_buyers_hotfrog(city, state)


def find_buyers_redfin_cash_sales(city: str, state: str) -> dict:
    """
    Find buyers who recently paid cash for properties on Redfin.
    Cash buyers show up as recent buyers with no mortgage — they're the best leads.
    Searches for their contact info via Bing after finding their names.
    """
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from tools import _scrape_redfin_city, _bing_search, _free_skip_trace

    listings = _scrape_redfin_city(city, state, max_results=20)
    found = []

    for listing in listings[:10]:
        address = listing.get("address", "")
        if not address:
            continue

        # Search for who recently bought properties in this area (cash buyers)
        query = f'"{city}" "{state}" real estate investor cash buyer "{address[:20]}" contact'
        results = _bing_search(query, max_results=3)
        for r in results:
            emails = _extract_emails(r.get("snippet", ""))
            phones = _extract_phones(r.get("snippet", ""))
            for email in emails:
                if _buyer_exists(email):
                    continue
                name = r.get("title", "Cash Buyer")[:50]
                buyer_id = _save_buyer(
                    name=name, email=email,
                    phone=phones[0] if phones else "",
                    markets=f"{city}, {state}",
                    source=f"Redfin cash sales search: {address}",
                )
                found.append({"buyer_id": buyer_id, "name": name, "email": email})
        time.sleep(0.5)

    return {
        "city":         city,
        "state":        state,
        "source":       "Redfin + Bing",
        "buyers_found": len(found),
        "buyers":       found,
    }


def enrich_buyers_with_email(limit: int = 50) -> dict:
    """
    Go through existing phone-only buyers and try to find their email.
    For Hotfrog-sourced buyers, re-visits the Hotfrog page to get their website link,
    then scrapes that site for an email.
    """
    buyers = _load(BUYERS_FILE, {})
    no_email = [b for b in buyers.values() if not b.get("email") and b.get("phone")][:limit]

    enriched = 0
    for b in no_email:
        buyer_id = b["buyer_id"]
        name     = b.get("name", "")
        markets  = b.get("markets", "")
        city     = markets.split(",")[0].strip() if markets else ""

        email   = ""
        website = ""

        # Strategy 1: Bing search for business name + city → extract email from results
        if name and city:
            query = f'"{name}" "{city}" real estate investor contact email'
            results = _bing(query, n=4)
            for r in results:
                # Check snippet for email
                found_emails = _extract_emails(r.get("snippet", "") + " " + r.get("title", ""))
                if found_emails:
                    email = found_emails[0]
                    break
                # Visit result URL for email
                result_url = r.get("url", "")
                if result_url and not any(s in result_url for s in SKIP_DOMAINS):
                    website = result_url
                    email = _scrape_website_for_email(result_url)
                    if email:
                        break
            time.sleep(0.5)

        # Strategy 2: guess info@domain from any website found
        if not email and website:
            email = _guess_email_from_domain(website)

        if email and not _buyer_exists(email):
            buyers[buyer_id]["email"] = email
            buyers[buyer_id]["email_source"] = "enriched_via_bing"
            if website:
                buyers[buyer_id]["website"] = website
            enriched += 1

        time.sleep(0.6)

    if enriched:
        _save(BUYERS_FILE, buyers)

    return {
        "checked":  len(no_email),
        "enriched": enriched,
        "message":  f"Added emails to {enriched} of {len(no_email)} phone-only buyers.",
    }


def email_buyer_intro(buyer_id: str) -> dict:
    """Send a deal-packet intro email to a cash buyer inviting them onto the buyers list."""
    buyers = _load(BUYERS_FILE, {})
    if buyer_id not in buyers:
        return {"error": f"Buyer {buyer_id} not found."}

    b     = buyers[buyer_id]
    email = b.get("email", "")
    if not email:
        return {"status": "skipped", "reason": "No email on file."}

    smtp_user = os.environ.get("SMTP_USER", COMPANY_EMAIL)
    markets   = b.get("markets", "your target market")
    name      = b.get("name", "Investor")
    subject   = f"Cash Buyer List — Deals in {b.get('markets', 'your market')}"

    body_text = BUYER_INTRO_TEMPLATE_TEXT.format(
        name=name, markets=markets,
        sender_email=smtp_user, phone=SENDER_PHONE, company=COMPANY_NAME,
    )
    body_html = _buyer_intro_html(name=name, markets=markets)

    result = _send_branded(
        to_email=email, subject=subject,
        body_text=body_text, body_html_inner=body_html,
    )

    # Log
    log = _load(EMAIL_LOG, [])
    log.append({"to": email, "buyer_id": buyer_id, "subject": subject,
                "status": result["status"], "sent_at": _now(), "type": "buyer_intro"})
    _save(EMAIL_LOG, log)

    if result["status"] == "sent":
        buyers[buyer_id]["intro_email_sent"] = True
        buyers[buyer_id]["updated_at"] = _now()
        _save(BUYERS_FILE, buyers)

    return {"buyer_id": buyer_id, "name": b.get("name"), "email": email, "status": result["status"]}


def recruit_buyers_full_cycle(city: str, state: str, auto_email: bool = True) -> dict:
    """
    Run full buyer recruitment for one market: Bing search + Craigslist + Redfin,
    then email every new buyer found. The core autonomous action.
    """
    all_found = []

    # Source 1: Bing
    bing_result = find_buyers_bing(city, state)
    all_found.extend(bing_result.get("buyers", []))
    time.sleep(1)

    # Source 2: Craigslist
    cl_result = find_buyers_craigslist(city, state)
    all_found.extend(cl_result.get("buyers", []))
    time.sleep(1)

    # Source 3: Redfin cash sales
    rf_result = find_buyers_redfin_cash_sales(city, state)
    all_found.extend(rf_result.get("buyers", []))

    # De-dupe by buyer_id
    seen_ids = set()
    unique = []
    for b in all_found:
        if b.get("buyer_id") not in seen_ids:
            seen_ids.add(b.get("buyer_id"))
            unique.append(b)

    emailed = []
    if auto_email:
        for b in unique:
            result = email_buyer_intro(b["buyer_id"])
            if result.get("status") == "sent":
                emailed.append(b)
            time.sleep(1)

    return {
        "city":          city,
        "state":         state,
        "total_new_buyers": len(unique),
        "from_bing":     bing_result.get("buyers_found", 0),
        "from_craigslist": cl_result.get("buyers_found", 0),
        "from_redfin":   rf_result.get("buyers_found", 0),
        "emails_sent":   len(emailed),
        "buyers":        unique,
    }


def run_all_markets(auto_email: bool = True) -> dict:
    """Run buyer recruitment for all your existing pipeline markets at once."""
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from tools import _load as parent_load, LEADS_FILE as pf

    leads = parent_load(Path(str(pf)), {})
    # Get top markets by lead count
    city_counts: dict = {}
    for lead in leads.values():
        city  = lead.get("city", "")
        state = lead.get("state", "")
        if city and state:
            key = (city, state)
            city_counts[key] = city_counts.get(key, 0) + 1

    top_markets = sorted(city_counts.items(), key=lambda x: x[1], reverse=True)[:5]

    all_results = []
    total_buyers = 0
    total_emailed = 0

    for (city, state), lead_count in top_markets:
        result = recruit_buyers_full_cycle(city, state, auto_email=auto_email)
        total_buyers  += result.get("total_new_buyers", 0)
        total_emailed += result.get("emails_sent", 0)
        all_results.append({
            "city":       city,
            "state":      state,
            "lead_count": lead_count,
            **result,
        })
        time.sleep(2)

    return {
        "markets_hit":     len(all_results),
        "total_new_buyers": total_buyers,
        "total_emails_sent": total_emailed,
        "per_market":      all_results,
    }


def get_buyers_summary() -> dict:
    """Full buyers list overview: total, by market, who has been emailed, etc."""
    buyers = _load(BUYERS_FILE, {})
    items  = list(buyers.values())

    by_market: dict = {}
    for b in items:
        mkt = b.get("markets", "unknown")
        by_market[mkt] = by_market.get(mkt, 0) + 1

    emailed     = [b for b in items if b.get("intro_email_sent")]
    not_emailed = [b for b in items if not b.get("intro_email_sent")]
    top_buyers  = sorted(items, key=lambda x: x.get("deals_closed", 0), reverse=True)[:5]

    return {
        "total_buyers":     len(items),
        "emailed":          len(emailed),
        "not_yet_emailed":  len(not_emailed),
        "deals_closed":     sum(b.get("deals_closed", 0) for b in items),
        "by_market":        dict(sorted(by_market.items(), key=lambda x: x[1], reverse=True)[:10]),
        "top_buyers":       [{"id": b["buyer_id"], "name": b.get("name"), "market": b.get("markets"), "deals": b.get("deals_closed", 0)} for b in top_buyers],
        "unemailed_sample": [{"id": b["buyer_id"], "name": b.get("name"), "email": b.get("email")} for b in not_emailed[:5]],
    }


TOOLS = [
    {
        "name": "recruit_buyers_full_cycle",
        "description": "Run full buyer recruitment for one market — searches Bing + Craigslist + Redfin, saves all buyers, auto-emails them an intro.",
        "input_schema": {
            "type": "object",
            "properties": {
                "city":       {"type": "string"},
                "state":      {"type": "string"},
                "auto_email": {"type": "boolean", "default": True},
            },
            "required": ["city", "state"],
        },
    },
    {
        "name": "run_all_markets",
        "description": "Run buyer recruitment for all top markets in your pipeline at once. Core autonomous action.",
        "input_schema": {
            "type": "object",
            "properties": {
                "auto_email": {"type": "boolean", "default": True},
            },
        },
    },
    {
        "name": "find_buyers_bing",
        "description": "Search Bing for cash buyers and real estate investors in a specific market.",
        "input_schema": {
            "type": "object",
            "properties": {
                "city":        {"type": "string"},
                "state":       {"type": "string"},
                "max_results": {"type": "integer", "default": 10},
            },
            "required": ["city", "state"],
        },
    },
    {
        "name": "find_buyers_craigslist",
        "description": "Scrape Craigslist real-estate-wanted for cash buyers posting 'I buy houses' in a city.",
        "input_schema": {
            "type": "object",
            "properties": {
                "city":  {"type": "string"},
                "state": {"type": "string"},
            },
            "required": ["city", "state"],
        },
    },
    {
        "name": "email_buyer_intro",
        "description": "Send a deal-packet intro email to a specific cash buyer inviting them onto the buyers list.",
        "input_schema": {
            "type": "object",
            "properties": {
                "buyer_id": {"type": "string"},
            },
            "required": ["buyer_id"],
        },
    },
    {
        "name": "get_buyers_summary",
        "description": "Full buyers list overview: total count, by market, emailed vs not, top buyers by deals closed.",
        "input_schema": {"type": "object", "properties": {}},
    },
]

TOOL_FUNCTIONS = {
    "recruit_buyers_full_cycle": recruit_buyers_full_cycle,
    "run_all_markets":           run_all_markets,
    "find_buyers_bing":          find_buyers_bing,
    "find_buyers_craigslist":    find_buyers_craigslist,
    "email_buyer_intro":         email_buyer_intro,
    "get_buyers_summary":        get_buyers_summary,
}
