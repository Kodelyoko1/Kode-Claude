#!/usr/bin/env python3
"""
Email Enrichment Pass — find emails for phone-only cash buyers.

Strategy (no API keys, no spend):
  1. Guess a domain from the business name (e.g., "ABC Investments" → abcinvestments.com)
  2. Visit guessed domains + scrape any /contact pages for emails
  3. Bing-search "{business name} {city} contact" → extract URLs → scrape those
  4. Fall back to info@<guessed domain>

Writes any found emails back to data/cash_buyers.json.
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

sys.path.insert(0, str(Path(__file__).parent))
from buyer_finder.tools import (
    _scrape_website_for_email, _guess_email_from_domain,
    _extract_emails, _bing, HEADERS,
)
import requests
from bs4 import BeautifulSoup
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

console = Console()
DATA_DIR = Path(__file__).parent / "data"
BUYERS_FILE = DATA_DIR / "cash_buyers.json"
ENRICH_LOG  = DATA_DIR / "enrichment_log.json"

# Domain endings to try when guessing
DOMAIN_TLDS = [".com", ".net", ".co"]

# Filter — don't save these as enrichment "wins"
JUNK_DOMAINS = {
    "hotfrog", "yelp", "manta", "yellowpages", "bbb.org",
    "godaddy", "facebook", "instagram", "linkedin", "twitter",
    "wholesaleomniverse", "share.here.com",
}

# Image file extensions that the email regex sometimes misparses (e.g. logo@2x.png)
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico", ".bmp")


def _is_image_artifact(email: str) -> bool:
    return email.lower().endswith(IMAGE_EXT)


def _domain_matches_name(email: str, business_name: str) -> bool:
    """Check that the email's domain relates to the business — kills random scraped emails."""
    if "@" not in email:
        return False
    domain = email.split("@", 1)[1].lower().split(".")[0]
    slug = _slug_from_name(business_name)
    if not slug or len(slug) < 4:
        return False
    # Either the slug contains the domain root or vice versa, with a meaningful overlap
    return (domain in slug or slug in domain or
            (len(domain) >= 5 and any(domain[i:i+5] in slug for i in range(len(domain) - 4))))


def _domain_resolves(url: str) -> bool:
    """Cheap check that a domain actually responds before we save a guess."""
    try:
        r = requests.head(url, headers=HEADERS, timeout=4, allow_redirects=True)
        return r.status_code < 500
    except Exception:
        return False


def _slug_from_name(name: str) -> str:
    """Turn 'ABC Investments LLC' into 'abcinvestments'."""
    s = re.sub(r"\b(llc|inc|corp|co|llp|ltd|group|properties|investments|"
               r"realty|realestate|capital|partners|holdings|company)\b", "", name.lower())
    s = re.sub(r"[^a-z0-9]", "", s)
    return s


def _is_junk(url_or_email: str) -> bool:
    s = url_or_email.lower()
    return any(d in s for d in JUNK_DOMAINS)


def _try_guess_domains(business_name: str) -> list:
    """Generate likely domain candidates for a business name."""
    slug = _slug_from_name(business_name)
    if not slug or len(slug) < 4:
        return []
    return [f"https://www.{slug}{tld}" for tld in DOMAIN_TLDS] + \
           [f"https://{slug}{tld}" for tld in DOMAIN_TLDS]


def _try_domain(url: str, timeout: int = 8) -> str:
    """Visit a URL; if it resolves, scrape it for an email."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
        if r.status_code != 200:
            return ""
        emails = _extract_emails(r.text)
        for e in emails:
            if not _is_junk(e):
                return e
        # Also try /contact /about pages on the same domain
        return _scrape_website_for_email(url)
    except Exception:
        return ""


def _bing_search_for_email(business_name: str, city: str) -> str:
    """Last-ditch: Bing search for the business, visit results, scrape emails."""
    query = f"{business_name} {city} contact email"
    try:
        results = _bing(query, n=4)
    except Exception:
        return ""
    for r in results:
        url = r.get("url", "") if isinstance(r, dict) else r
        if not url or _is_junk(url):
            continue
        email = _try_domain(url, timeout=6)
        if email:
            return email
        time.sleep(0.3)
    return ""


def _acceptable_email(email: str, business_name: str) -> bool:
    if not email or _is_junk(email) or _is_image_artifact(email):
        return False
    return _domain_matches_name(email, business_name)


def enrich_one(buyer: dict) -> dict:
    """Try every strategy to find an email for one buyer. Returns updates to apply."""
    name = buyer.get("name", "").strip()
    city = (buyer.get("markets", "").split(",")[0] or "").strip()
    if not name:
        return {}

    # Strategy 1: guess + try domain (require domain match + actual page response)
    for url in _try_guess_domains(name):
        email = _try_domain(url, timeout=6)
        if email and _acceptable_email(email, name):
            return {"email": email, "enrichment_source": f"domain_guess:{url}"}
        time.sleep(0.2)

    # Strategy 2: Bing search — only keep emails whose domain matches the business
    email = _bing_search_for_email(name, city)
    if email and _acceptable_email(email, name):
        return {"email": email, "enrichment_source": f"bing:{name}"}

    # Strategy 3: info@<resolved domain> as last resort — verify the domain actually responds
    slug = _slug_from_name(name)
    if slug and len(slug) >= 4:
        guessed = f"https://www.{slug}.com"
        if _domain_resolves(guessed):
            fallback = _guess_email_from_domain(guessed)
            if fallback and not _is_junk(fallback) and not _is_image_artifact(fallback):
                return {"email": fallback, "enrichment_source": "guess_pattern",
                        "enrichment_note": "Unverified — info@ pattern from resolved domain"}

    return {}


def run(limit: int = 0):
    buyers = json.loads(BUYERS_FILE.read_text())
    no_email = [(bid, b) for bid, b in buyers.items() if not b.get("email")]

    if limit > 0:
        no_email = no_email[:limit]

    console.print(Panel(
        Text.from_markup(
            f"[bold]Email Enrichment Pass[/bold]\n"
            f"  Target buyers (phone-only): [white]{len(no_email)}[/white]\n"
            f"  Strategies: domain guess → Bing search → info@ fallback\n"
            f"  Expected runtime: ~{len(no_email) * 8 / 60:.0f} min"
        ),
        title="[bold blue]Wholesale Omniverse — Enrichment[/bold blue]",
        border_style="blue",
    ))

    found, skipped, log = 0, 0, []
    for i, (bid, b) in enumerate(no_email, 1):
        update = enrich_one(b)
        if update.get("email"):
            buyers[bid].update(update)
            buyers[bid]["enriched_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            found += 1
            console.print(f"  [green]✓[/green] {i:>3}/{len(no_email)}  "
                          f"{b.get('name', '')[:35]:35}  →  {update['email']}")
            log.append({"buyer_id": bid, "name": b.get("name"),
                        "email": update["email"],
                        "source": update.get("enrichment_source", "")})
            # Persist after every win — don't lose work if interrupted
            BUYERS_FILE.write_text(json.dumps(buyers, indent=2))
        else:
            skipped += 1
            console.print(f"  [dim]·[/dim] {i:>3}/{len(no_email)}  "
                          f"{b.get('name', '')[:35]:35}  no email found")
        time.sleep(0.4)

    if log:
        existing = json.loads(ENRICH_LOG.read_text()) if ENRICH_LOG.exists() else []
        existing.extend(log)
        ENRICH_LOG.write_text(json.dumps(existing, indent=2))

    console.print(Panel(
        Text.from_markup(
            f"[bold green]Enrichment complete[/bold green]\n\n"
            f"  Found:    [green]{found}[/green] new email(s)\n"
            f"  Skipped:  [yellow]{skipped}[/yellow] (no email findable)\n\n"
            f"  Addressable buyer list is now: "
            f"[bold]{found + 8}[/bold] (was 8)\n\n"
            f"  Next: [bold]python3 run_cash_blast.py --mode buyer-pitch --send[/bold]\n"
            f"        will now reach all newly-enriched buyers."
        ),
        border_style="green",
    ))


def main():
    parser = argparse.ArgumentParser(description="Email enrichment for phone-only buyers")
    parser.add_argument("--limit", type=int, default=0,
                        help="Only enrich first N (0 = all 186)")
    args = parser.parse_args()
    run(args.limit)


if __name__ == "__main__":
    main()
