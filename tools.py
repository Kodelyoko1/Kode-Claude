import json
import csv
import datetime
import smtplib
import os
import re
import time
from urllib.parse import unquote, urlparse, parse_qs
import requests
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from bs4 import BeautifulSoup
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
IMPORT_DIR = DATA_DIR / "import"
IMPORT_PROCESSED_DIR = IMPORT_DIR / "processed"
LEADS_FILE = DATA_DIR / "leads.json"
BUYERS_FILE = DATA_DIR / "cash_buyers.json"
CONTRACTS_FILE = DATA_DIR / "contracts.json"
COMPS_FILE = DATA_DIR / "comps.json"
EMAIL_LOG_FILE = DATA_DIR / "email_log.json"
PROPERTIES_FILE = DATA_DIR / "properties.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

LEAD_STATUSES = {"new", "contacted", "negotiating", "under_contract", "assigned", "dead"}

# ─── Storage helpers ──────────────────────────────────────────────────────────

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

# ─── Tool: Analyze Deal ───────────────────────────────────────────────────────

def analyze_deal(
    address: str,
    arv: float,
    repair_cost: float,
    asking_price: float,
    assignment_fee: float = 10000.0,
    closing_costs: float = 2000.0,
    holding_costs: float = 0.0,
) -> dict:
    """
    Analyze a wholesale real estate deal.
    Calculates MAO, equity, profit, and whether the deal works.

    MAO formula: ARV × 0.70 - Repair Costs - Assignment Fee
    """
    mao = (arv * 0.70) - repair_cost - assignment_fee
    all_in_cost = asking_price + repair_cost + closing_costs + holding_costs
    arv_ratio = (asking_price / arv * 100) if arv else 0
    equity = arv - all_in_cost
    equity_pct = (equity / arv * 100) if arv else 0
    spread = mao - asking_price  # positive = room to negotiate, negative = deal too expensive

    if asking_price <= mao and equity_pct >= 30:
        verdict = "STRONG DEAL"
        action = "Move fast — lock it up immediately"
    elif asking_price <= mao:
        verdict = "GOOD DEAL"
        action = "Solid deal — get it under contract"
    elif asking_price <= mao * 1.1:
        verdict = "BORDERLINE"
        action = "Negotiate down or reduce assignment fee"
    else:
        verdict = "DEAL DOES NOT WORK"
        action = f"Seller needs to come down ${asking_price - mao:,.0f} for this to work"

    return {
        "address": address,
        "arv": arv,
        "repair_cost": repair_cost,
        "asking_price": asking_price,
        "assignment_fee": assignment_fee,
        "mao": round(mao, 2),
        "spread": round(spread, 2),
        "all_in_cost_to_buyer": round(all_in_cost, 2),
        "equity_after_repairs": round(equity, 2),
        "equity_pct": round(equity_pct, 2),
        "arv_purchase_ratio_pct": round(arv_ratio, 2),
        "verdict": verdict,
        "action": action,
        "your_profit": assignment_fee,
    }

# ─── Tool: Research Market / Comps ───────────────────────────────────────────

def research_market(address: str, city: str, state: str) -> dict:
    """Research a neighborhood — searches for recent sales, market trends, and property values."""
    queries = [
        f"recent home sales {city} {state} 2024 2025 average price per sqft",
        f"{city} {state} real estate market trends investors ARV",
        f"distressed properties foreclosure {city} {state} wholesale deals",
    ]
    insights = []
    for q in queries:
        url = f"https://html.duckduckgo.com/html/?q={requests.utils.quote(q)}"
        try:
            resp = requests.get(url, headers=HEADERS, timeout=10)
            soup = BeautifulSoup(resp.text, "lxml")
            for r in soup.select(".result")[:3]:
                title_el = r.select_one(".result__title")
                snippet_el = r.select_one(".result__snippet")
                if title_el and snippet_el:
                    insights.append({
                        "title": title_el.get_text(strip=True),
                        "insight": snippet_el.get_text(strip=True),
                    })
        except Exception:
            continue

    # Cache
    comps = _load(COMPS_FILE, [])
    comps.append({"address": address, "city": city, "state": state, "researched_at": _now(), "insights": insights[:6]})
    _save(COMPS_FILE, comps)

    return {"location": f"{city}, {state}", "near": address, "market_insights": insights[:9], "count": len(insights[:9])}

# ─── Tool: Find Motivated Sellers ────────────────────────────────────────────

def find_motivated_sellers(city: str, state: str, strategy: str = "all") -> dict:
    """
    Search for motivated seller leads in a market.
    Strategies: foreclosure, probate, tax_delinquent, vacant, divorce, all
    """
    strategy_queries = {
        "foreclosure": f"pre-foreclosure NOD lis pendens {city} {state} motivated seller",
        "probate": f"probate real estate {city} {state} estate sale motivated seller",
        "tax_delinquent": f"tax delinquent properties {city} {state} behind on taxes motivated seller",
        "vacant": f"vacant abandoned properties {city} {state} wholesale deal",
        "divorce": f"divorce sale real estate {city} {state} motivated seller",
        "all": f"motivated sellers distressed properties {city} {state} wholesale real estate leads",
    }

    query = strategy_queries.get(strategy, strategy_queries["all"])
    results = _bing_search(query, max_results=8)

    return {
        "market": f"{city}, {state}",
        "strategy": strategy,
        "leads_sources": [{"source": r["title"], "info": r["snippet"], "url": r["url"]} for r in results],
        "tip": "Use these sources to find lists. Then skip trace to get owner contact info.",
    }

# ─── Tool: Save Lead ─────────────────────────────────────────────────────────

def save_lead(
    address: str,
    city: str,
    state: str,
    seller_name: str = "",
    seller_phone: str = "",
    seller_email: str = "",
    asking_price: float = 0,
    estimated_arv: float = 0,
    estimated_repairs: float = 0,
    lead_source: str = "",
    motivation: str = "",
    notes: str = "",
) -> dict:
    """Save a motivated seller lead to the pipeline."""
    leads = _load(LEADS_FILE, {})
    lead_id = f"LEAD-{len(leads)+1:04d}"

    mao = 0.0
    if estimated_arv and estimated_repairs:
        mao = round((estimated_arv * 0.70) - estimated_repairs - 10000, 2)

    leads[lead_id] = {
        "lead_id": lead_id,
        "address": address,
        "city": city,
        "state": state,
        "seller_name": seller_name,
        "seller_phone": seller_phone,
        "seller_email": seller_email,
        "asking_price": asking_price,
        "estimated_arv": estimated_arv,
        "estimated_repairs": estimated_repairs,
        "estimated_mao": mao,
        "lead_source": lead_source,
        "motivation": motivation,
        "status": "new",
        "notes": notes,
        "created_at": _now(),
        "updated_at": _now(),
    }
    _save(LEADS_FILE, leads)
    return {"status": "saved", "lead_id": lead_id, "estimated_mao": mao, "pipeline_size": len(leads)}

# ─── Tool: Get Leads ──────────────────────────────────────────────────────────

def get_leads(status: str = "", city: str = "") -> dict:
    """Retrieve all seller leads, optionally filtered by status or city."""
    leads = _load(LEADS_FILE, {})
    items = list(leads.values())
    if status:
        items = [l for l in items if l.get("status", "").lower() == status.lower()]
    if city:
        items = [l for l in items if l.get("city", "").lower() == city.lower()]
    items.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return {"leads": items, "count": len(items), "filters": {"status": status, "city": city}}

# ─── Tool: Update Lead Status ─────────────────────────────────────────────────

def update_lead_status(lead_id: str, status: str, notes: str = "") -> dict:
    """Update a lead's status: new → contacted → negotiating → under_contract → assigned / dead"""
    if status not in LEAD_STATUSES:
        return {"error": f"Invalid status. Choose from: {', '.join(LEAD_STATUSES)}"}
    leads = _load(LEADS_FILE, {})
    if lead_id not in leads:
        return {"error": f"Lead {lead_id} not found."}
    leads[lead_id]["status"] = status
    leads[lead_id]["updated_at"] = _now()
    if notes:
        leads[lead_id]["notes"] = (leads[lead_id].get("notes", "") + f"\n[{_now()[:10]}] {notes}").strip()
    _save(LEADS_FILE, leads)
    return {"status": "updated", "lead_id": lead_id, "new_status": status}

# ─── Tool: Add Cash Buyer ─────────────────────────────────────────────────────

def add_cash_buyer(
    name: str,
    phone: str = "",
    email: str = "",
    buy_box: str = "",
    markets: str = "",
    max_price: float = 0,
    preferred_property_types: str = "",
    notes: str = "",
) -> dict:
    """Add an investor/cash buyer to your buyers list."""
    buyers = _load(BUYERS_FILE, {})
    buyer_id = f"BUYER-{len(buyers)+1:04d}"
    buyers[buyer_id] = {
        "buyer_id": buyer_id,
        "name": name,
        "phone": phone,
        "email": email,
        "buy_box": buy_box,
        "markets": markets,
        "max_price": max_price,
        "preferred_property_types": preferred_property_types,
        "notes": notes,
        "deals_closed": 0,
        "added_at": _now(),
    }
    _save(BUYERS_FILE, buyers)
    return {"status": "added", "buyer_id": buyer_id, "total_buyers": len(buyers)}

# ─── Tool: Get Cash Buyers ────────────────────────────────────────────────────

def get_cash_buyers(market: str = "") -> dict:
    """Retrieve your cash buyers list."""
    buyers = _load(BUYERS_FILE, {})
    items = list(buyers.values())
    if market:
        items = [b for b in items if market.lower() in b.get("markets", "").lower()]
    return {"buyers": items, "count": len(items)}

# ─── Tool: Create Contract ────────────────────────────────────────────────────

def create_contract(
    lead_id: str,
    contract_price: float,
    assignment_fee: float,
    close_date: str,
    earnest_money: float = 500.0,
    inspection_period_days: int = 14,
    notes: str = "",
) -> dict:
    """Record a property going under contract."""
    leads = _load(LEADS_FILE, {})
    contracts = _load(CONTRACTS_FILE, [])

    lead = leads.get(lead_id)
    if not lead:
        return {"error": f"Lead {lead_id} not found."}

    contract_id = f"CONTRACT-{len(contracts)+1:04d}"
    net_profit = assignment_fee
    contract = {
        "contract_id": contract_id,
        "lead_id": lead_id,
        "address": lead["address"],
        "city": lead["city"],
        "state": lead["state"],
        "seller_name": lead.get("seller_name", ""),
        "contract_price": contract_price,
        "assignment_fee": assignment_fee,
        "net_profit": net_profit,
        "earnest_money": earnest_money,
        "inspection_period_days": inspection_period_days,
        "close_date": close_date,
        "status": "active",
        "notes": notes,
        "created_at": _now(),
    }
    contracts.append(contract)
    _save(CONTRACTS_FILE, contracts)

    # Update lead status
    if lead_id in leads:
        leads[lead_id]["status"] = "under_contract"
        leads[lead_id]["updated_at"] = _now()
        _save(LEADS_FILE, leads)

    return {"status": "created", "contract": contract}

# ─── Tool: Get Contracts ──────────────────────────────────────────────────────

def get_contracts(status: str = "") -> dict:
    """Retrieve all contracts, optionally filtered by status."""
    contracts = _load(CONTRACTS_FILE, [])
    if status:
        contracts = [c for c in contracts if c.get("status", "").lower() == status.lower()]
    total_fees = sum(c.get("assignment_fee", 0) for c in contracts if c.get("status") != "cancelled")
    return {"contracts": contracts, "count": len(contracts), "total_assignment_fees": round(total_fees, 2)}

# ─── Tool: Assign Contract ────────────────────────────────────────────────────

def assign_contract(contract_id: str, buyer_id: str, final_assignment_fee: float, notes: str = "") -> dict:
    """Assign a contract to a cash buyer and close the wholesale deal."""
    contracts = _load(CONTRACTS_FILE, [])
    buyers = _load(BUYERS_FILE, {})
    leads = _load(LEADS_FILE, {})

    contract = next((c for c in contracts if c["contract_id"] == contract_id), None)
    if not contract:
        return {"error": f"Contract {contract_id} not found."}
    buyer = buyers.get(buyer_id)
    if not buyer:
        return {"error": f"Buyer {buyer_id} not found."}

    contract["status"] = "assigned"
    contract["assigned_to_buyer"] = buyer["name"]
    contract["buyer_id"] = buyer_id
    contract["final_assignment_fee"] = final_assignment_fee
    contract["assigned_at"] = _now()
    if notes:
        contract["notes"] = notes

    buyers[buyer_id]["deals_closed"] = buyers[buyer_id].get("deals_closed", 0) + 1

    lead_id = contract.get("lead_id")
    if lead_id and lead_id in leads:
        leads[lead_id]["status"] = "assigned"
        leads[lead_id]["updated_at"] = _now()
        _save(LEADS_FILE, leads)

    _save(CONTRACTS_FILE, contracts)
    _save(BUYERS_FILE, buyers)

    return {
        "status": "assigned",
        "contract_id": contract_id,
        "assigned_to": buyer["name"],
        "assignment_fee_collected": final_assignment_fee,
        "address": contract["address"],
    }

# ─── Tool: Business Summary ───────────────────────────────────────────────────

def get_business_summary() -> dict:
    """Full wholesale business pipeline overview."""
    leads = _load(LEADS_FILE, {})
    buyers = _load(BUYERS_FILE, {})
    contracts = _load(CONTRACTS_FILE, [])

    lead_list = list(leads.values())
    statuses = {}
    for l in lead_list:
        s = l.get("status", "unknown")
        statuses[s] = statuses.get(s, 0) + 1

    assigned = [c for c in contracts if c.get("status") == "assigned"]
    active = [c for c in contracts if c.get("status") == "active"]
    total_earned = sum(c.get("final_assignment_fee", 0) for c in assigned)
    pipeline_value = sum(c.get("assignment_fee", 0) for c in active)

    return {
        "leads": {
            "total": len(lead_list),
            "by_status": statuses,
            "hot_leads": [l for l in lead_list if l.get("status") in ("negotiating", "under_contract")],
        },
        "contracts": {
            "active": len(active),
            "assigned_closed": len(assigned),
            "pipeline_value": round(pipeline_value, 2),
        },
        "financials": {
            "total_earned": round(total_earned, 2),
            "deals_closed": len(assigned),
            "avg_assignment_fee": round(total_earned / len(assigned), 2) if assigned else 0,
        },
        "buyers_list": {
            "total_buyers": len(buyers),
            "top_buyers": sorted(buyers.values(), key=lambda x: x.get("deals_closed", 0), reverse=True)[:3],
        },
    }

# ─── Tool: Skip Trace Guidance ────────────────────────────────────────────────

def skip_trace_guidance(property_address: str, owner_name: str = "") -> dict:
    """
    Provide guidance on how to skip trace a property owner to get contact info.
    Returns recommended services and search strategies.
    """
    services = [
        {"name": "BatchSkipTracing", "url": "batchskiptracing.com", "cost": "~$0.13/record", "best_for": "Bulk lists"},
        {"name": "PropStream", "url": "propstream.com", "cost": "~$99/mo", "best_for": "Finding + tracing in one tool"},
        {"name": "TLO/TransUnion", "url": "tlo.com", "cost": "Pay per search", "best_for": "High accuracy"},
        {"name": "BeenVerified", "url": "beenverified.com", "cost": "~$26/mo", "best_for": "Quick individual lookups"},
        {"name": "Spokeo", "url": "spokeo.com", "cost": "~$20/mo", "best_for": "Finding phone numbers"},
    ]
    free_methods = [
        "County assessor website — search by address for owner name",
        "Google '[Owner Name] [City] [State] phone'",
        "LinkedIn search for owner name",
        "Facebook search for owner name + city",
        "Whitepages.com — free basic lookup",
        "USPS mail forwarding — send letter to property, may forward",
    ]
    return {
        "property": property_address,
        "owner": owner_name,
        "paid_services": services,
        "free_methods": free_methods,
        "pro_tip": "Best combo: BatchSkipTracing for bulk + PropStream for finding leads",
    }

# ─── Tool: Lookup Property Records ──────────────────────────────────────────

def lookup_property_records(address: str, city: str, state: str) -> dict:
    """
    Look up public property records for an address — finds owner name,
    mailing address, assessed value, and tax status via web search.
    """
    queries = [
        f'"{address}" {city} {state} property owner assessor records',
        f'site:qpublic.net OR site:assessor.com "{address}" {city} {state} owner',
        f'{address} {city} {state} property tax owner name public record',
    ]
    findings = []
    for q in queries:
        url = f"https://html.duckduckgo.com/html/?q={requests.utils.quote(q)}"
        try:
            resp = requests.get(url, headers=HEADERS, timeout=10)
            soup = BeautifulSoup(resp.text, "lxml")
            for r in soup.select(".result")[:3]:
                title_el = r.select_one(".result__title")
                snippet_el = r.select_one(".result__snippet")
                link_el = r.select_one(".result__url")
                if title_el and snippet_el:
                    findings.append({
                        "title": title_el.get_text(strip=True),
                        "info": snippet_el.get_text(strip=True),
                        "url": link_el.get_text(strip=True) if link_el else "",
                    })
            time.sleep(0.5)
        except Exception:
            continue

    # Save to properties store
    props = _load(PROPERTIES_FILE, {})
    prop_key = f"{address}_{city}_{state}".lower().replace(" ", "_")
    props[prop_key] = {
        "address": address, "city": city, "state": state,
        "looked_up_at": _now(), "findings": findings[:5],
    }
    _save(PROPERTIES_FILE, props)

    assessor_urls = {
        "LA": "https://www.qpublicnet.com/county/east-baton-rouge OR laassessor.org",
        "GA": "https://qpublic.net/ga",
        "TX": "https://tax.texas.gov/property",
        "FL": "https://www.floridarevenue.com/property",
        "TN": "https://comptroller.tn.gov/boards/state-board-of-equalization/property-tax",
    }
    assessor = assessor_urls.get(state.upper(), f"Search '{state} county assessor property lookup'")

    return {
        "address": address, "city": city, "state": state,
        "public_record_findings": findings[:6],
        "assessor_direct_link": assessor,
        "tip": f"For exact owner info, visit your county assessor and search: {address}",
    }


# ─── Tool: Find Owner Email ───────────────────────────────────────────────────

def find_owner_contact(owner_name: str, city: str, state: str, address: str = "") -> dict:
    """
    Attempt to find a property owner's email and phone via web search.
    Searches LinkedIn, Facebook, Whitepages, and general web.
    """
    queries = [
        f'"{owner_name}" {city} {state} email contact phone',
        f'"{owner_name}" {city} {state} property owner landlord',
        f'"{owner_name}" {address} {state} contact' if address else f'"{owner_name}" {city} {state} whitepages',
    ]
    contacts = []
    emails_found = []
    phones_found = []

    for q in queries:
        for sr in _bing_search(q, max_results=4):
            snippet_text = sr.get("snippet", "")
            emails_found += [e for e in re.findall(r'[\w.+-]+@[\w-]+\.[a-zA-Z]{2,}', snippet_text)
                             if not any(s in e.lower() for s in SKIP_EMAILS)]
            phones_found += re.findall(r'\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}', snippet_text)
            contacts.append({"source": sr.get("title",""), "info": snippet_text[:200], "url": sr.get("url","")})
        time.sleep(0.4)

    unique_emails = list(dict.fromkeys(emails_found))
    unique_phones = list(dict.fromkeys(phones_found))

    return {
        "owner_name": owner_name,
        "city": city, "state": state,
        "emails_found": unique_emails,
        "phones_found": unique_phones[:5],
        "sources_checked": contacts[:6],
        "confidence": "high" if unique_emails else "low — use skip trace service for email",
        "skip_trace_fallback": "batchskiptracing.com (~$0.13/record) for guaranteed contact info",
    }


# ─── Tool: Prospect from Government Records ──────────────────────────────────

# Verified working Socrata open data endpoints (tested 2026-05)
# Each entry maps record_type → (endpoint_url, owner_field, address_field)
SOCRATA_DATASETS = {
    "chicago": {
        "code_violations": (
            "https://data.cityofchicago.org/resource/22u3-xenr.json",
            "respondent_name", "violation_address",
        ),
        "vacant": (
            "https://data.cityofchicago.org/resource/kc9i-wq85.json",
            None, "property_address",
        ),
    },
    "kansas city": {
        "code_violations": (
            "https://data.kcmo.org/resource/nhtf-e75a.json",
            None, "mapped_location",
        ),
    },
    "norfolk": {
        "tax_delinquent": (
            "https://data.norfolk.gov/resource/7qie-z5gv.json",
            "owner_name", "address",
        ),
    },
}

# Known county assessor/tax sites with parseable delinquent list pages
COUNTY_DELINQUENT_PAGES = {
    ("wayne", "MI"): "https://www.waynecounty.com/elected/treasurer/delinquent-taxes.aspx",
    ("cook", "IL"): "https://www.cookcountytreasurer.com/scavenger.aspx",
    ("harris", "TX"): "https://www.hctax.net/Property/DelinquentTaxList",
    ("maricopa", "AZ"): "https://treasurer.maricopa.gov/property/delinquent",
    ("fulton", "GA"): "https://www.fultoncountytaxes.org/property-tax/delinquent-tax-list.aspx",
    ("shelby", "TN"): "https://www.shelbycountytrustee.com/delinquent-tax-list",
    ("orleans", "LA"): "https://www.nolaassessor.com/delinquent-tax-list",
    ("pinellas", "FL"): "https://www.pinellasclerk.org/aspInclude/property.asp",
    ("multnomah", "OR"): "https://multco.us/assessment-taxation/delinquent-property-taxes",
}

# Bing search queries per record type (Bing is more reliable than DDG for this)
BING_QUERIES = {
    "tax_delinquent": [
        '"{city} {state}" delinquent property tax list owner name filetype:pdf OR filetype:csv',
        '"{county} county" {state} delinquent taxpayer list property address',
        '"{city}" {state} unpaid property taxes owner list site:.gov',
        '"{county} county" {state} tax delinquent roll annual list download',
    ],
    "code_violations": [
        '"{city} {state}" code violations property list owner contact',
        '"{city}" {state} nuisance properties owner list site:.gov',
        '"{city}" {state} blighted property owners contact email',
    ],
    "foreclosure": [
        '"{county} county" {state} sheriff sale list property address owner',
        '"{city} {state}" lis pendens foreclosure filing owner name',
        '"{county} county" clerk {state} foreclosure notices public record',
    ],
    "probate": [
        '"{county} county" {state} probate court estate property filings',
        '"{city}" {state} probate real estate owner heir contact',
        '"{county} county" {state} estate sale property list',
    ],
    "vacant": [
        '"{city} {state}" vacant property registration list owner',
        '"{city}" {state} abandoned property owner contact registry site:.gov',
        '"{city}" {state} vacant land owner list public record',
    ],
}

SKIP_EMAILS = {"noreply", "example", "webmaster", "admin@", "support@", "no-reply",
               "donotreply", "postmaster", "privacy@", "legal@", "press@"}


def _bing_search(query: str, max_results: int = 5) -> list:
    """Search Bing and return list of (title, url, snippet) tuples."""
    results = []
    bing_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        url = f"https://www.bing.com/search?q={requests.utils.quote(query)}"
        resp = requests.get(url, headers=bing_headers, timeout=10)
        if resp.status_code != 200:
            return results
        soup = BeautifulSoup(resp.text, "lxml")
        for li in soup.select("li.b_algo")[:max_results]:
            title_el = li.select_one("h2 a")
            snippet_el = li.select_one(".b_caption p") or li.select_one("p")
            title = title_el.get_text(strip=True) if title_el else ""
            href = title_el.get("href", "") if title_el else ""
            snippet = snippet_el.get_text(strip=True) if snippet_el else ""
            if title and href:
                results.append({"title": title, "url": href, "snippet": snippet})
    except Exception:
        pass
    return results


def _scrape_page(url: str) -> dict:
    """Fetch a page and extract owner names, addresses, and emails from it."""
    result = {"url": url, "names": [], "addresses": [], "emails": [], "phones": [], "text_preview": ""}
    gov_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml",
    }
    try:
        resp = requests.get(url, headers=gov_headers, timeout=12, verify=False)
        if resp.status_code != 200:
            return result
        soup = BeautifulSoup(resp.text, "lxml")
        text = soup.get_text(separator=" ", strip=True)
        result["text_preview"] = text[:500]

        # Extract emails (filter out generic/system emails)
        raw_emails = re.findall(r'[\w.+-]+@[\w-]+\.[a-zA-Z]{2,}', text)
        result["emails"] = [e for e in set(raw_emails)
                            if not any(s in e.lower() for s in SKIP_EMAILS)][:8]

        # Extract addresses
        result["addresses"] = list(set(re.findall(
            r'\d{2,5}\s+[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){0,3}\s+'
            r'(?:St|Ave|Rd|Blvd|Dr|Ln|Ct|Way|Pl|Ter|Pkwy|Hwy|Circle|Loop)\b',
            text
        )))[:10]

        # Extract owner/taxpayer names from common label patterns
        name_hits = re.findall(
            r'(?:Owner|Taxpayer|Grantor|Debtor|Name|Registered\s+Agent)[:\s]+([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){1,3})',
            text
        )
        # Also look in table cells
        for row in soup.select("tr"):
            cells = [td.get_text(strip=True) for td in row.find_all(["td", "th"])]
            for i, cell in enumerate(cells):
                if cell.lower() in ("owner", "taxpayer", "name", "owner name") and i + 1 < len(cells):
                    candidate = cells[i + 1]
                    if re.match(r'^[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)+$', candidate):
                        name_hits.append(candidate)
        result["names"] = list(set(name_hits))[:10]
        result["phones"] = list(set(re.findall(r'\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}', text)))[:5]
    except Exception:
        pass
    return result


def _try_socrata(city: str, state: str, record_type: str, max_records: int = 10) -> list:
    """Query verified working Socrata endpoints. Returns list of prospect dicts."""
    prospects = []
    city_key = city.lower()
    datasets = SOCRATA_DATASETS.get(city_key, {})
    entry = datasets.get(record_type) or (list(datasets.values())[0] if datasets else None)
    if not entry:
        return prospects
    endpoint, owner_field, address_field = entry
    try:
        resp = requests.get(
            endpoint,
            params={"$limit": max_records},
            headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0"},
            timeout=12,
            verify=False,
        )
        if resp.status_code != 200:
            return prospects
        rows = resp.json()
        if not isinstance(rows, list) or not rows:
            return prospects
        for row in rows:
            name = (row.get(owner_field) if owner_field else None) or ""
            address = (row.get(address_field) if address_field else None) or ""
            if not address:
                continue
            prospects.append({
                "owner_name": str(name).strip(),
                "address": str(address).strip(),
                "email": None,
                "phone": None,
                "source": f"Open data portal ({city})",
                "source_url": endpoint,
            })
    except Exception:
        pass
    return prospects


def lookup_owner_by_address(address: str, city: str, state: str) -> dict:
    """
    Look up property owner from free county assessor open data APIs.
    Confirmed working: Chicago IL (Cook County), Baltimore MD, Norfolk VA.
    Returns {"owner_name": str, "mailing_address": str} or {}.
    """
    h        = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    state_up = state.upper().strip()
    city_low = city.lower().strip()
    addr_up  = address.upper().strip()
    parts    = addr_up.split()
    street_num  = parts[0] if parts and parts[0].isdigit() else ""
    street_name = " ".join(parts[1:3]) if len(parts) > 1 else addr_up

    # Cook County (Chicago, IL) — Socrata open data
    if state_up == "IL":
        try:
            r = requests.get(
                "https://datacatalog.cookcountyil.gov/resource/tx2p-k2g9.json",
                params={
                    "$where": f"prop_address_full LIKE '{street_num} {street_name}%'",
                    "$limit": 1,
                    "$select": "prop_address_full,mail_address_name,mail_address_full",
                },
                headers=h, timeout=10,
            )
            if r.status_code == 200:
                data = r.json()
                if data and data[0].get("mail_address_name"):
                    return {
                        "owner_name": data[0]["mail_address_name"].title(),
                        "mailing_address": data[0].get("mail_address_full", ""),
                    }
        except Exception:
            pass

    # Baltimore City, MD — ArcGIS real property layer
    if state_up == "MD":
        try:
            # Try exact number + street, then fallback to street name only filtered by number
            for where_clause in [
                f"FULLADDR LIKE '{street_num} {street_name}%'",
                f"FULLADDR LIKE '%{street_name}%' AND FULLADDR LIKE '{street_num} %'",
            ]:
                r = requests.get(
                    "https://services1.arcgis.com/UWYHeuuJISiGmgXx/arcgis/rest/services/"
                    "realprop_lulc_footprint/FeatureServer/0/query",
                    params={
                        "where": where_clause,
                        "outFields": "OWNER_1,FULLADDR,MAILTOADD",
                        "f": "json",
                        "resultRecordCount": 1,
                    },
                    headers=h, timeout=12,
                )
                if r.status_code == 200:
                    feats = r.json().get("features", [])
                    if feats:
                        a = feats[0]["attributes"]
                        owner = (a.get("OWNER_1") or "").strip()
                        if owner:
                            return {
                                "owner_name": owner.title(),
                                "mailing_address": (a.get("MAILTOADD") or "").strip(),
                            }
        except Exception:
            pass

    # Norfolk, VA — Socrata tax records open data
    if state_up == "VA" and "norfolk" in city_low:
        try:
            r = requests.get(
                "https://data.norfolk.gov/resource/7qie-z5gv.json",
                params={
                    "$where": f"upper(address) LIKE '%{street_name}%'",
                    "$limit": 1,
                    "$select": "owner_name,address",
                },
                headers=h, timeout=10,
            )
            if r.status_code == 200:
                data = r.json()
                if data and data[0].get("owner_name"):
                    return {"owner_name": data[0]["owner_name"].title()}
        except Exception:
            pass

    return {}


def _free_skip_trace(name: str, city: str, state: str, address: str = "") -> dict:
    """
    Free skip trace using FastPeopleSearch, TruePeopleSearch, ZabaSearch, Radaris.
    Searches by both name and address for higher hit rates. No API key required.
    """
    phones, emails = [], []
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.google.com/",
    }

    def extract(text):
        ph = re.findall(r'\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}', text)
        em = [e for e in re.findall(r'[\w.+-]+@[\w-]+\.[a-zA-Z]{2,}', text)
              if not any(s in e.lower() for s in SKIP_EMAILS)]
        return ph, em

    slug_name = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')
    slug_loc  = re.sub(r'[^a-z0-9]+', '-', f"{city} {state}".lower()).strip('-')

    # 1. FastPeopleSearch — by name
    try:
        url = f"https://www.fastpeoplesearch.com/name/{slug_name}_{slug_loc}"
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, "lxml")
            for el in soup.select(".card-block, .search-result"):
                ph, em = extract(el.get_text(" ", strip=True))
                phones += ph; emails += em
    except Exception:
        pass

    # 2. FastPeopleSearch — by address (often more accurate for property owners)
    if address and (not phones and not emails):
        try:
            slug_addr = re.sub(r'[^a-z0-9]+', '-', address.lower()).strip('-')
            slug_city = re.sub(r'[^a-z0-9]+', '-', f"{city}-{state}".lower()).strip('-')
            url = f"https://www.fastpeoplesearch.com/address/{slug_addr}_{slug_city}"
            r = requests.get(url, headers=headers, timeout=10)
            if r.status_code == 200:
                soup = BeautifulSoup(r.text, "lxml")
                for el in soup.select(".card-block, .search-result, .result-item"):
                    ph, em = extract(el.get_text(" ", strip=True))
                    phones += ph; emails += em
        except Exception:
            pass

    # 3. TruePeopleSearch — by name
    if not phones and not emails:
        try:
            r = requests.get(
                "https://www.truepeoplesearch.com/results",
                params={"name": name, "citystatezip": f"{city} {state}"},
                headers=headers, timeout=10,
            )
            if r.status_code == 200:
                ph, em = extract(BeautifulSoup(r.text, "lxml").get_text(" ", strip=True))
                phones += ph; emails += em
        except Exception:
            pass

    # 4. TruePeopleSearch — by address
    if address and (not phones and not emails):
        try:
            r = requests.get(
                "https://www.truepeoplesearch.com/resultaddress",
                params={"streetaddress": address, "citystatezip": f"{city} {state}"},
                headers=headers, timeout=10,
            )
            if r.status_code == 200:
                ph, em = extract(BeautifulSoup(r.text, "lxml").get_text(" ", strip=True))
                phones += ph; emails += em
        except Exception:
            pass

    # 5. ZabaSearch — by name + state
    if not phones and not emails:
        try:
            first, *rest = name.split()
            last = rest[-1] if rest else ""
            r = requests.get(
                f"https://www.zabasearch.com/people/{first}+{last}/{state}/",
                headers=headers, timeout=10,
            )
            if r.status_code == 200:
                ph, em = extract(BeautifulSoup(r.text, "lxml").get_text(" ", strip=True))
                phones += ph; emails += em
        except Exception:
            pass

    # 6. Radaris — by name + city
    if not phones and not emails:
        try:
            first, *rest = name.split()
            last = rest[-1] if rest else ""
            r = requests.get(
                f"https://radaris.com/p/{first}/{last}/",
                params={"from": f"{city}, {state}"},
                headers=headers, timeout=10,
            )
            if r.status_code == 200:
                ph, em = extract(BeautifulSoup(r.text, "lxml").get_text(" ", strip=True))
                phones += ph; emails += em
        except Exception:
            pass

    return {
        "phones": list(dict.fromkeys(phones))[:3],
        "emails": list(dict.fromkeys(emails))[:2],
    }


def _batchskiptracing_lookup(name: str, address: str, city: str, state: str) -> dict:
    """
    Look up owner contact info via BatchSkipTracing API.
    Requires BATCHSKIPTRACING_API_KEY in env.
    Returns dict with phones and emails.
    """
    api_key = os.environ.get("BATCHSKIPTRACING_API_KEY", "")
    if not api_key:
        return {}
    try:
        resp = requests.post(
            "https://api.batchskiptracing.com/api/person/search",
            headers={"X-API-Key": api_key, "Content-Type": "application/json"},
            json={
                "firstName": name.split()[0] if name else "",
                "lastName": " ".join(name.split()[1:]) if len(name.split()) > 1 else "",
                "address": address,
                "city": city,
                "state": state,
            },
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json()
            phones = [p.get("phoneNumber") for p in data.get("phones", []) if p.get("phoneNumber")]
            emails = [e.get("email") for e in data.get("emails", []) if e.get("email")]
            return {"phones": phones[:3], "emails": emails[:2]}
    except Exception:
        pass
    return {}


# ─── CSV column aliases for PropStream, BatchLeads, and generic exports ───────
_CSV_COL_MAP = {
    "owner_name":    ["owner name", "owner_name", "seller name", "contact name", "name", "taxpayer name"],
    "address":       ["property address", "property_address", "address", "street address", "mailing address"],
    "city":          ["city", "property city", "mailing city"],
    "state":         ["state", "property state", "mailing state", "st"],
    "zip":           ["zip", "zipcode", "zip code", "postal code", "property zip"],
    "email":         ["email", "email address", "owner email", "contact email"],
    "phone":         ["phone", "phone number", "owner phone", "cell", "mobile"],
    "asking_price":  ["asking price", "list price", "price", "amount owed"],
    "estimated_arv": ["arv", "after repair value", "estimated value", "zestimate", "market value"],
    "motivation":    ["motivation", "lead type", "list type", "distress type", "category"],
}


def _detect_csv_columns(header_row: list) -> dict:
    """Map CSV header columns to our standard field names."""
    normalized = [h.lower().strip() for h in header_row]
    mapping = {}
    for field, aliases in _CSV_COL_MAP.items():
        for alias in aliases:
            if alias in normalized:
                mapping[field] = normalized.index(alias)
                break
    return mapping


def scan_and_import_csv_leads(
    auto_email: bool = False,
    record_type: str = "motivated_seller",
    default_city: str = "",
    default_state: str = "",
) -> dict:
    """
    Scan data/import/ for CSV files from PropStream, BatchLeads, or any export.
    Auto-detects columns, imports all rows as leads, optionally emails them.
    Moves processed files to data/import/processed/.
    """
    IMPORT_DIR.mkdir(parents=True, exist_ok=True)
    IMPORT_PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    csv_files = list(IMPORT_DIR.glob("*.csv")) + list(IMPORT_DIR.glob("*.CSV"))
    if not csv_files:
        return {
            "status": "no_files",
            "message": f"No CSV files found in {IMPORT_DIR}. "
                       "Drop a PropStream, BatchLeads, or any export CSV there to import.",
            "import_path": str(IMPORT_DIR),
        }

    total_imported = 0
    total_emailed = 0
    files_processed = []

    for csv_path in csv_files:
        try:
            with open(csv_path, newline="", encoding="utf-8-sig") as f:
                reader = csv.reader(f)
                rows = list(reader)
            if len(rows) < 2:
                continue

            col_map = _detect_csv_columns(rows[0])
            if "address" not in col_map and "owner_name" not in col_map:
                files_processed.append({"file": csv_path.name, "status": "skipped — no address or owner column detected"})
                continue

            leads_batch = []
            for row in rows[1:]:
                def get(field):
                    idx = col_map.get(field)
                    return row[idx].strip() if idx is not None and idx < len(row) else ""

                name = get("owner_name")
                address = get("address")
                city = get("city") or default_city
                state = get("state") or default_state
                if not address and not name:
                    continue

                leads_batch.append({
                    "owner_name": name,
                    "address": address,
                    "city": city,
                    "state": state,
                    "email": get("email") or None,
                    "phone": get("phone") or None,
                    "asking_price": float(get("asking_price").replace("$","").replace(",","") or 0) if get("asking_price") else 0,
                    "estimated_arv": float(get("estimated_arv").replace("$","").replace(",","") or 0) if get("estimated_arv") else 0,
                    "motivation": get("motivation") or record_type,
                })

            # Use import_and_email_leads for enrichment + saving
            if leads_batch and city:
                result = import_and_email_leads(
                    leads=leads_batch,
                    city=city or default_city,
                    state=state or default_state,
                    record_type=record_type,
                    auto_email=auto_email,
                )
                total_imported += result.get("total_imported", 0)
                total_emailed += result.get("emailed", 0)

            # Move to processed
            dest = IMPORT_PROCESSED_DIR / csv_path.name
            csv_path.rename(dest)
            files_processed.append({
                "file": csv_path.name,
                "rows_imported": len(leads_batch),
                "status": "processed",
            })
        except Exception as e:
            files_processed.append({"file": csv_path.name, "status": f"error: {e}"})

    return {
        "files_scanned": len(csv_files),
        "files_processed": files_processed,
        "total_imported": total_imported,
        "total_emailed": total_emailed,
        "import_path": str(IMPORT_DIR),
    }


def prospect_from_government_records(
    city: str,
    state: str,
    county: str = "",
    record_type: str = "tax_delinquent",
    max_prospects: int = 10,
    auto_email: bool = False,
) -> dict:
    """
    Search local government websites for motivated seller prospects.
    Uses Socrata open data APIs, direct county portal scraping, and Bing web search
    to find property owners from tax delinquent lists, code violations,
    foreclosure filings, probate records, or vacant property registries.
    Looks up emails for every owner found, saves all as pipeline leads,
    and optionally auto-emails them.

    record_type options: tax_delinquent, code_violations, foreclosure, probate, vacant
    """
    county = county or city
    prospects = []
    seen_names = set()
    gov_pages_visited = []

    # ── Phase 1: Socrata open data (richest structured data) ─────────────────
    socrata_prospects = _try_socrata(city, state, record_type, max_records=max_prospects)
    for p in socrata_prospects:
        name = p.get("owner_name", "")
        if name and name not in seen_names and len(name) > 3:
            seen_names.add(name)
            prospects.append({**p, "record_type": record_type, "city": city, "state": state})
        if len(prospects) >= max_prospects:
            break

    # ── Phase 2: Direct county delinquent list pages ──────────────────────────
    if len(prospects) < max_prospects:
        county_key = (county.lower(), state.upper())
        county_url = COUNTY_DELINQUENT_PAGES.get(county_key)
        if county_url:
            gov_pages_visited.append(county_url)
            page_data = _scrape_page(county_url)
            for i, name in enumerate(page_data.get("names", [])):
                if name in seen_names:
                    continue
                seen_names.add(name)
                prospects.append({
                    "owner_name": name,
                    "city": city, "state": state,
                    "address": page_data["addresses"][i] if i < len(page_data["addresses"]) else "",
                    "email": page_data["emails"][i] if i < len(page_data["emails"]) else None,
                    "phone": page_data["phones"][i] if i < len(page_data["phones"]) else None,
                    "source": "County delinquent tax list",
                    "source_url": county_url,
                    "record_type": record_type,
                })
                if len(prospects) >= max_prospects:
                    break

    # ── Phase 3: Bing search → scrape top government pages ───────────────────
    if len(prospects) < max_prospects:
        queries = BING_QUERIES.get(record_type, BING_QUERIES["tax_delinquent"])
        for raw_q in queries[:3]:
            query = raw_q.format(city=city, state=state, county=county)
            search_results = _bing_search(query, max_results=4)
            time.sleep(0.8)
            for sr in search_results:
                url = sr.get("url", "")
                snippet = sr.get("snippet", "")
                if not url:
                    continue

                # Pull names/emails from snippet text first (fast, no HTTP request)
                snippet_emails = [e for e in re.findall(r'[\w.+-]+@[\w-]+\.[a-zA-Z]{2,}', snippet)
                                   if not any(s in e.lower() for s in SKIP_EMAILS)]
                snippet_names = re.findall(
                    r'(?:Owner|Taxpayer|Name)[:\s]+([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){1,2})',
                    snippet
                )
                for name in snippet_names:
                    if name not in seen_names and len(name) > 4:
                        seen_names.add(name)
                        prospects.append({
                            "owner_name": name,
                            "city": city, "state": state,
                            "address": "",
                            "email": snippet_emails[0] if snippet_emails else None,
                            "source": sr.get("title", ""),
                            "source_url": url,
                            "record_type": record_type,
                        })

                # Scrape .gov and county/assessor pages directly
                is_gov_page = any(s in url.lower() for s in
                                  [".gov", "assessor", "treasurer", "tax", "county", "clerk", "delinquent"])
                if is_gov_page and url not in gov_pages_visited:
                    gov_pages_visited.append(url)
                    page_data = _scrape_page(url)
                    time.sleep(0.5)
                    for i, name in enumerate(page_data.get("names", [])):
                        if name in seen_names or len(name) < 5:
                            continue
                        seen_names.add(name)
                        prospects.append({
                            "owner_name": name,
                            "city": city, "state": state,
                            "address": page_data["addresses"][i] if i < len(page_data["addresses"]) else "",
                            "email": (page_data["emails"][i] if i < len(page_data["emails"])
                                      else page_data["emails"][0] if page_data["emails"] else None),
                            "source": sr.get("title", ""),
                            "source_url": url,
                            "record_type": record_type,
                        })
                        if len(prospects) >= max_prospects:
                            break
                if len(prospects) >= max_prospects:
                    break
            if len(prospects) >= max_prospects:
                break

    # ── Phase 4: Contact enrichment — free skip trace → BST API → web search ──
    bst_key = os.environ.get("BATCHSKIPTRACING_API_KEY", "")
    for p in prospects:
        name = p.get("owner_name", "")
        address = p.get("address", "")

        if name and not (p.get("email") and p.get("phone")):
            # 1. Free skip trace (FastPeopleSearch + TruePeopleSearch + ZabaSearch + Radaris)
            free = _free_skip_trace(name, city, state, address)
            if free.get("phones") and not p.get("phone"):
                p["phone"] = free["phones"][0]
            if free.get("emails") and not p.get("email"):
                p["email"] = free["emails"][0]
            time.sleep(0.5)

            # 2. BatchSkipTracing API if still missing contact and key is set
            if bst_key and not (p.get("email") and p.get("phone")):
                result = _batchskiptracing_lookup(name, address, city, state)
                if result.get("emails") and not p.get("email"):
                    p["email"] = result["emails"][0]
                if result.get("phones") and not p.get("phone"):
                    p["phone"] = result["phones"][0]
                time.sleep(0.3)

            if p.get("email") or p.get("phone"):
                continue  # enrichment succeeded, skip web search

        # 3. Web search fallback (free, less reliable)
        if not p.get("owner_name") and address:
            q = f'"{address}" {city} {state} property owner name email contact'
            results = _bing_search(q, max_results=3)
            for sr in results:
                snippet = sr.get("snippet", "")
                name_hit = re.findall(
                    r'(?:Owner|Name|Taxpayer)[:\s]+([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){1,2})',
                    snippet
                )
                email_hit = [e for e in re.findall(r'[\w.+-]+@[\w-]+\.[a-zA-Z]{2,}', snippet)
                             if not any(s in e.lower() for s in SKIP_EMAILS)]
                if name_hit:
                    p["owner_name"] = name_hit[0]
                if email_hit and not p.get("email"):
                    p["email"] = email_hit[0]
                if p.get("owner_name"):
                    break
            time.sleep(0.4)

        if not p.get("email") and p.get("owner_name"):
            contact = find_owner_contact(p["owner_name"], city, state, p.get("address", ""))
            if contact.get("emails_found"):
                p["email"] = contact["emails_found"][0]
            if contact.get("phones_found") and not p.get("phone"):
                p["phone"] = contact["phones_found"][0]
            time.sleep(0.3)

    # ── Phase 5: Save all prospects as pipeline leads ─────────────────────────
    for p in prospects:
        save_lead(
            address=p.get("address", "Unknown"),
            city=city, state=state,
            seller_name=p.get("owner_name", "Unknown"),
            seller_email=p.get("email", ""),
            seller_phone=p.get("phone", ""),
            notes=f"Found via {record_type} government records. Source: {p.get('source_url', '')}",
            motivation=record_type,
        )

    # ── Phase 6: Auto-email if requested ─────────────────────────────────────
    emailed = []
    if auto_email:
        template_map = {
            "tax_delinquent": "pre_foreclosure",
            "code_violations": "vacant",
            "foreclosure": "pre_foreclosure",
            "probate": "probate",
            "vacant": "vacant",
        }
        template = template_map.get(record_type, "motivated_seller")
        for p in prospects:
            if p.get("email"):
                result = send_outreach_email(
                    to_email=p["email"],
                    owner_name=p.get("owner_name", "Property Owner"),
                    property_address=p.get("address", "your property"),
                    city=city,
                    template_type=template,
                )
                emailed.append({"to": p["email"], "owner": p.get("owner_name", ""), "status": result.get("status", "failed")})
                time.sleep(1)

    with_email = [p for p in prospects if p.get("email")]
    without_email = [p for p in prospects if not p.get("email")]

    return {
        "record_type": record_type,
        "city": city, "state": state,
        "government_pages_visited": gov_pages_visited,
        "total_prospects_found": len(prospects),
        "prospects_with_email": len(with_email),
        "prospects_without_email": len(without_email),
        "prospects": prospects,
        "auto_emailed": emailed,
        "next_steps": (
            f"Found {len(with_email)} prospects with emails ready to contact."
            + (" Run with auto_email=true to send outreach automatically." if not auto_email and with_email else "")
            if prospects
            else f"No structured data found online for {city} {state} {record_type} records. "
                 "Try: (1) visit your county assessor site manually, (2) request the list from the tax office, "
                 "or (3) use BatchSkipTracing.com to skip trace a purchased list."
        ),
    }


# ─── Tool: Import Leads and Email ────────────────────────────────────────────

def import_and_email_leads(
    leads: list,
    city: str,
    state: str,
    record_type: str = "motivated_seller",
    auto_email: bool = False,
) -> dict:
    """
    Import a list of owner names/addresses (e.g. from a manually downloaded
    government delinquent list), look up their emails via web search,
    save them as pipeline leads, and optionally send outreach emails.

    Each item in leads should be a dict with at least one of:
      - owner_name, address, email, phone

    record_type controls which email template is used if auto_email=True:
      motivated_seller, probate, pre_foreclosure, vacant
    """
    results = []
    for entry in leads:
        name = str(entry.get("owner_name") or entry.get("name") or "").strip()
        address = str(entry.get("address") or entry.get("property_address") or "").strip()
        email = str(entry.get("email") or "").strip() or None
        phone = str(entry.get("phone") or "").strip() or None

        if not name and not address:
            continue

        # Look up contact if missing — free skip trace first, then web search
        if name and not (email and phone):
            free = _free_skip_trace(name, city, state, address)
            if free.get("phones") and not phone:
                phone = free["phones"][0]
            if free.get("emails") and not email:
                email = free["emails"][0]
            time.sleep(0.5)
        if not email and name:
            contact = find_owner_contact(name, city, state, address)
            if contact.get("emails_found"):
                email = contact["emails_found"][0]
            if contact.get("phones_found") and not phone:
                phone = contact["phones_found"][0]
            time.sleep(0.4)

        # Save as lead
        save_lead(
            address=address or "Unknown",
            city=city, state=state,
            seller_name=name or "Unknown",
            seller_email=email or "",
            seller_phone=phone or "",
            notes=f"Imported from {record_type} records list.",
            motivation=record_type,
        )

        result = {
            "owner_name": name,
            "address": address,
            "email": email,
            "phone": phone,
            "saved_as_lead": True,
            "emailed": False,
        }

        # Send email if requested and email was found
        if auto_email and email:
            template_map = {
                "tax_delinquent": "pre_foreclosure",
                "code_violations": "vacant",
                "foreclosure": "pre_foreclosure",
                "probate": "probate",
                "vacant": "vacant",
                "motivated_seller": "motivated_seller",
            }
            template = template_map.get(record_type, "motivated_seller")
            send_result = send_outreach_email(
                to_email=email,
                owner_name=name or "Property Owner",
                property_address=address or "your property",
                city=city,
                template_type=template,
            )
            result["emailed"] = send_result.get("status") == "sent"
            result["email_status"] = send_result.get("status", "failed")
            time.sleep(1)

        results.append(result)

    with_email = [r for r in results if r.get("email")]
    emailed = [r for r in results if r.get("emailed")]

    return {
        "total_imported": len(results),
        "saved_as_leads": len(results),
        "with_email": len(with_email),
        "emailed": len(emailed),
        "results": results,
        "summary": f"Imported {len(results)} leads. {len(with_email)} have emails. {len(emailed)} emails sent.",
    }


# ─── Tool: Craigslist Motivated Seller Scraper ───────────────────────────────

# ─── Redfin motivated seller scraper ────────────────────────────────────────

# Bounding box per city: polygon string for Redfin poly= param (lon+lat pairs, closed)
# Detroit shifted north (42.45+) to avoid Windsor, Ontario border overlap
REDFIN_CITY_CONFIG = {
    ("detroit", "mi"):       {"poly": "-83.40+42.45,-82.90+42.45,-82.90+42.75,-83.40+42.75,-83.40+42.45", "state": "MI", "max_price": 120000},
    ("baltimore", "md"):     {"poly": "-76.72+39.20,-76.52+39.20,-76.52+39.38,-76.72+39.38,-76.72+39.20", "state": "MD", "max_price": 200000},
    ("memphis", "tn"):       {"poly": "-90.12+35.02,-89.83+35.02,-89.83+35.23,-90.12+35.23,-90.12+35.02", "state": "TN", "max_price": 150000},
    ("cleveland", "oh"):     {"poly": "-81.88+41.38,-81.53+41.38,-81.53+41.55,-81.88+41.55,-81.88+41.38", "state": "OH", "max_price": 120000},
    ("chicago", "il"):       {"poly": "-87.94+41.64,-87.52+41.64,-87.52+42.03,-87.94+42.03,-87.94+41.64", "state": "IL", "max_price": 300000},
    ("kansas city", "mo"):   {"poly": "-94.72+38.92,-94.40+38.92,-94.40+39.15,-94.72+39.15,-94.72+38.92", "state": "MO", "max_price": 150000},
    ("norfolk", "va"):       {"poly": "-76.36+36.81,-76.02+36.81,-76.02+36.98,-76.36+36.98,-76.36+36.81", "state": "VA", "max_price": 250000},
    ("atlanta", "ga"):       {"poly": "-84.56+33.64,-84.29+33.64,-84.29+33.89,-84.56+33.89,-84.56+33.64", "state": "GA", "max_price": 200000},
    ("houston", "tx"):       {"poly": "-95.67+29.52,-95.10+29.52,-95.10+29.92,-95.67+29.92,-95.67+29.52", "state": "TX", "max_price": 200000},
    ("dallas", "tx"):        {"poly": "-97.00+32.62,-96.63+32.62,-96.63+32.90,-97.00+32.90,-97.00+32.62", "state": "TX", "max_price": 200000},
    ("philadelphia", "pa"):  {"poly": "-75.28+39.86,-74.96+39.86,-74.96+40.14,-75.28+40.14,-75.28+39.86", "state": "PA", "max_price": 200000},
    ("pittsburgh", "pa"):    {"poly": "-80.17+40.35,-79.86+40.35,-79.86+40.57,-80.17+40.57,-80.17+40.35", "state": "PA", "max_price": 150000},
    ("cincinnati", "oh"):    {"poly": "-84.70+39.04,-84.35+39.04,-84.35+39.25,-84.70+39.25,-84.70+39.04", "state": "OH", "max_price": 150000},
    ("columbus", "oh"):      {"poly": "-83.13+39.89,-82.77+39.89,-82.77+40.15,-83.13+40.15,-83.13+39.89", "state": "OH", "max_price": 150000},
    ("indianapolis", "in"):  {"poly": "-86.33+39.62,-85.94+39.62,-85.94+39.95,-86.33+39.95,-86.33+39.62", "state": "IN", "max_price": 150000},
    ("milwaukee", "wi"):     {"poly": "-88.08+42.88,-87.83+42.88,-87.83+43.18,-88.08+43.18,-88.08+42.88", "state": "WI", "max_price": 150000},
    ("birmingham", "al"):    {"poly": "-86.95+33.41,-86.63+33.41,-86.63+33.62,-86.95+33.62,-86.95+33.41", "state": "AL", "max_price": 120000},
    ("nashville", "tn"):     {"poly": "-87.06+36.02,-86.65+36.02,-86.65+36.32,-87.06+36.32,-87.06+36.02", "state": "TN", "max_price": 200000},
    ("st. louis", "mo"):     {"poly": "-90.39+38.52,-90.13+38.52,-90.13+38.77,-90.39+38.77,-90.39+38.52", "state": "MO", "max_price": 150000},
    ("jacksonville", "fl"):  {"poly": "-81.84+30.10,-81.44+30.10,-81.44+30.45,-81.84+30.45,-81.84+30.10", "state": "FL", "max_price": 200000},
    ("new orleans", "la"):   {"poly": "-90.16+29.88,-89.93+29.88,-89.93+30.10,-90.16+30.10,-90.16+29.88", "state": "LA", "max_price": 150000},
    ("baton rouge", "la"):   {"poly": "-91.27+30.31,-90.96+30.31,-90.96+30.57,-91.27+30.57,-91.27+30.31", "state": "LA", "max_price": 150000},
    ("richmond", "va"):      {"poly": "-77.60+37.45,-77.35+37.45,-77.35+37.62,-77.60+37.62,-77.60+37.45", "state": "VA", "max_price": 200000},
    ("charlotte", "nc"):     {"poly": "-80.98+35.06,-80.72+35.06,-80.72+35.33,-80.98+35.33,-80.98+35.06", "state": "NC", "max_price": 200000},
}

REDFIN_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Referer": "https://www.redfin.com/",
}


def _scrape_redfin_city(city: str, state: str, max_results: int = 25, min_dom: int = 0) -> list:
    """Fetch motivated seller listings from Redfin using bounding box + state filter."""
    key = (city.lower().strip(), state.lower().strip())
    config = REDFIN_CITY_CONFIG.get(key)
    if not config:
        for (c, s), cfg in REDFIN_CITY_CONFIG.items():
            if (city.lower() in c or c in city.lower()) and s == state.lower():
                config = cfg
                break
    if not config:
        return []

    target_state = config["state"]
    max_price = config["max_price"]
    poly = config["poly"]
    fetch_n = min(max_results * 4, 200)

    url = (
        f"https://www.redfin.com/stingray/api/gis"
        f"?al=1&num_homes={fetch_n}&status=9&uipt=1,2,3,4&v=8"
        f"&poly={poly}&max_price={max_price}&sort=5"
    )

    try:
        r = requests.get(url, headers=REDFIN_HEADERS, timeout=20)
        if not r.ok:
            return []
        raw = r.text
        idx = raw.find("{", 2)
        if idx < 0:
            return []
        data = json.loads(raw[idx:])
    except Exception:
        return []

    homes = data.get("payload", {}).get("homes", [])
    filtered = [
        hm for hm in homes
        if hm.get("state") == target_state
        and ((hm.get("price") or {}).get("value") or 0) <= max_price
    ]
    filtered.sort(key=lambda h: h.get("dom", {}).get("value", 0) or 0, reverse=True)

    if min_dom > 0:
        filtered = [h for h in filtered if (h.get("dom", {}).get("value") or 0) >= min_dom]

    results = []
    for hm in filtered[:max_results]:
        street = (hm.get("streetLine") or {}).get("value", "")
        if not street:
            continue
        hm_city = hm.get("city", city)
        hm_state = hm.get("state", target_state)
        price = (hm.get("price") or {}).get("value", 0) or 0
        dom = (hm.get("dom") or {}).get("value", 0) or 0
        beds = hm.get("beds") or 0
        baths = hm.get("baths") or 0
        sqft = (hm.get("sqFt") or {}).get("value", 0) or 0
        url_path = hm.get("url", "")
        source_url = f"https://www.redfin.com{url_path}" if url_path else ""
        sashes = [s.get("sashTypeName", "") for s in hm.get("sashes", [])]
        has_price_drop = any("Price" in s for s in sashes)

        if dom >= 60:
            motivation = "vacant"
        elif has_price_drop:
            motivation = "pre_foreclosure"
        else:
            motivation = "motivated_seller"

        results.append({
            "address": street,
            "city": hm_city,
            "state": hm_state,
            "price": price,
            "dom": dom,
            "beds": beds,
            "baths": baths,
            "sqft": sqft,
            "source_url": source_url,
            "motivation": motivation,
            "has_price_drop": has_price_drop,
        })

    return results


def scrape_craigslist_leads(
    city: str,
    state: str,
    max_results: int = 25,
    auto_email: bool = False,
) -> dict:
    """
    Scrape motivated seller listings for a target city using Redfin listings data.
    Filters for below-market, long-sitting properties — best indicators of motivated sellers.
    Saves all found properties as pipeline leads and optionally emails them.
    """
    listings = _scrape_redfin_city(city, state, max_results=max_results)

    found = []
    emailed = 0

    for listing in listings:
        address = listing["address"]
        hm_city = listing["city"]
        hm_state = listing["state"]
        price = listing["price"]
        dom = listing["dom"]
        motivation = listing["motivation"]
        source_url = listing["source_url"]

        notes = (
            f"Redfin: {listing['beds']}bd/{listing['baths']}ba "
            f"{listing['sqft']:,}sqft DOM:{dom}"
            + (" [PRICE DROP]" if listing["has_price_drop"] else "")
            + (f" | {source_url}" if source_url else "")
        )

        lead_result = save_lead(
            address=address,
            city=hm_city,
            state=hm_state,
            seller_name="Property Owner",
            seller_phone="",
            seller_email="",
            asking_price=price,
            motivation=motivation,
            notes=notes,
        )
        lead_id = lead_result.get("lead_id", "")

        entry = {
            "lead_id": lead_id,
            "address": address,
            "city": hm_city,
            "price": price,
            "dom": dom,
            "motivation": motivation,
            "has_price_drop": listing["has_price_drop"],
            "phone": None,
            "email": None,
            "url": source_url,
        }
        found.append(entry)

    return {
        "city": city,
        "state": state,
        "source": "Redfin",
        "leads_found": len(found),
        "leads_with_phone": 0,
        "leads_with_email": 0,
        "emails_sent": emailed,
        "leads": found,
        "summary": (
            f"Found {len(found)} motivated seller listings in {city}, {state} via Redfin. "
            f"All saved to pipeline. Run skip_trace_and_email_all to find contact info and send outreach."
        ),
    }


# ─── Tool: Send Outreach Email ────────────────────────────────────────────────

COMPANY_NAME = "Wholesale Omniverse LLC"
COMPANY_EMAIL = "info@wholesaleomniverse.com"

EMAIL_TEMPLATES = {
    "motivated_seller": {
        "subject": "Quick question about your property at {address}",
        "body": """Hi {owner_name},

My name is Tyreese Lumiere with {company_name}, and I'm a local real estate investor in {city}.

I came across your property at {address} and wanted to reach out directly. We work with homeowners who are looking for a fast, hassle-free sale — no agents, no repairs needed, no fees.

If you've ever considered selling, I'd love to make you a fair all-cash offer. We can close in as little as 2–3 weeks on your timeline.

There's zero obligation — just a quick conversation to see if it makes sense for you.

Would you be open to a brief call this week?

Best,
Tyreese Lumiere
{sender_email}
207-385-4041

P.S. If now isn't the right time, no worries at all — feel free to reach out whenever you're ready.""",
    },
    "probate": {
        "subject": "We can help simplify the sale of {address}",
        "body": """Hi {owner_name},

I hope this message finds you well. My name is Tyreese Lumiere with {company_name}.

I understand that managing an inherited property or estate can be overwhelming, especially during an already difficult time. We specialize in helping families sell inherited properties quickly and without the hassle of traditional listings — no repairs, no showings, no agent commissions.

If you'd like to discuss a straightforward all-cash sale for {address}, I'm happy to talk at your convenience and answer any questions.

There's no pressure or obligation whatsoever.

Warmly,
Tyreese Lumiere
{sender_email}
207-385-4041
""",
    },
    "pre_foreclosure": {
        "subject": "A potential solution for your property at {address}",
        "body": """Hi {owner_name},

My name is Tyreese Lumiere with {company_name}. I wanted to reach out about your property at {address} in {city}.

We work with homeowners who are facing difficult financial situations and need to sell quickly. We can offer:

  • A fair all-cash offer
  • Close in 2–3 weeks or on your timeline
  • No repairs, no showings, no agent fees
  • Help you walk away with cash and move forward

I know this can be a stressful time, and we'd genuinely like to help if we can. Even if we can't make a deal work, I'm happy to point you toward other resources.

Would you be open to a quick conversation?

Tyreese Lumiere
{sender_email}
207-385-4041""",
    },
    "vacant": {
        "subject": "Interested in buying your property at {address}",
        "body": """Hi {owner_name},

My name is Tyreese Lumiere with {company_name}. We are cash buyers actively purchasing properties in {city}.

I noticed your property at {address} and wanted to reach out to see if you'd consider selling. We buy properties as-is — no repairs, no cleanup, no agent needed. We handle everything and can close fast.

If you've been thinking about selling, I'd love to make you a straightforward cash offer with no strings attached.

Feel free to reply to this email anytime.

Tyreese Lumiere
{sender_email}
207-385-4041""",
    },
}


def _geocode_address(address: str, city: str, state: str) -> tuple:
    """Return (lat, lon) via Nominatim, or (None, None) on failure."""
    try:
        full = f"{address}, {city}, {state}, USA"
        resp = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": full, "format": "json", "limit": 1},
            headers={"User-Agent": "WholesaleOmniverse-Agent/1.0"},
            timeout=6,
        )
        if resp.ok:
            data = resp.json()
            if data:
                return float(data[0]["lat"]), float(data[0]["lon"])
    except Exception:
        pass
    return None, None


def _fetch_map_image_bytes(address: str, city: str, state: str) -> bytes:
    """Geocode the address, fetch the static map image, return raw PNG bytes or b''."""
    try:
        lat, lon = _geocode_address(address, city, state)
        if not lat:
            return b""
        url = (
            f"https://staticmap.openstreetmap.de/staticmap.php"
            f"?center={lat},{lon}&zoom=17&size=580x220&maptype=mapnik"
            f"&markers={lat},{lon},lightblue1"
        )
        resp = requests.get(url, headers=HEADERS, timeout=10)
        if resp.ok and resp.content:
            return resp.content
    except Exception:
        pass
    return b""


def _source_buttons_html(address: str, city: str, state: str, source_url: str = "") -> str:
    from urllib.parse import quote_plus
    q = quote_plus(f"{address}, {city}, {state}")
    slug = quote_plus(address)

    links = [
        ("Google Maps",  f"https://www.google.com/maps/search/?api=1&query={q}",  "#4285f4"),
        ("Street View",  f"https://www.google.com/maps?q={q}&layer=c",            "#34a853"),
        ("Zillow",       f"https://www.zillow.com/homes/{slug}_rb/",              "#006aff"),
        ("Redfin",       f"https://www.redfin.com/search#location={q}",           "#c82021"),
        ("County Records", f"https://www.google.com/maps/search/?api=1&query={q}", "#6b7280"),
    ]
    if source_url:
        links.append(("Source Record", source_url, "#0f172a"))

    html = '<table cellpadding="0" cellspacing="0"><tr>'
    for label, url, color in links:
        html += (
            f'<td style="padding:0 6px 0 0;">'
            f'<a href="{url}" style="display:inline-block;padding:7px 13px;'
            f'background-color:{color};color:#ffffff;text-decoration:none;'
            f'border-radius:4px;font-size:11px;font-weight:700;'
            f'letter-spacing:0.3px;">{label}</a></td>'
        )
    html += "</tr></table>"
    return html


def _load_logo_b64() -> str:
    """Return base64-encoded logo PNG for inline embedding, or empty string."""
    logo_path = DATA_DIR / "logo.png"
    if logo_path.exists():
        import base64
        with open(logo_path, "rb") as f:
            return base64.b64encode(f.read()).decode()
    return ""


def _build_html_email(plain_body: str, sender_name: str, sender_phone: str, sender_email: str, company_email: str,
                      address: str = "", city: str = "", state: str = "", source_url: str = "",
                      has_map: bool = False) -> str:
    """Wrap plain-text body in a branded HTML email with header, property map, source links, and footer."""
    map_cid = "map@wholesaleomniverse.com"
    map_img_html = ""
    if has_map and address and city:
        gmaps_url = f"https://www.google.com/maps/search/?api=1&query={requests.utils.quote(f'{address}, {city}, {state}')}"
        map_img_html = f"""
        <!-- PROPERTY MAP -->
        <tr>
          <td style="background-color:#ffffff;padding:0 40px 8px;border-left:1px solid #e5e7eb;border-right:1px solid #e5e7eb;">
            <p style="margin:0 0 8px;font-size:11px;font-weight:700;letter-spacing:1px;text-transform:uppercase;color:#94a3b8;">Property Location</p>
            <a href="{gmaps_url}" style="display:block;border-radius:6px;overflow:hidden;border:1px solid #e5e7eb;">
              <img src="cid:{map_cid}" alt="Property map — {address}, {city}, {state}"
                   width="520" style="display:block;width:100%;max-width:520px;border-radius:6px;" />
            </a>
            <p style="margin:6px 0 0;font-size:11px;color:#9ca3af;text-align:center;">
              Click map to open in Google Maps &nbsp;&rsaquo;
            </p>
          </td>
        </tr>"""

    # Build source link buttons
    source_url = source_url or ""
    buttons_html = _source_buttons_html(address, city, state, source_url) if address and city else ""
    sources_section = ""
    if buttons_html:
        sources_section = f"""
        <!-- SOURCE LINKS -->
        <tr>
          <td style="background-color:#ffffff;padding:16px 40px 28px;border-left:1px solid #e5e7eb;border-right:1px solid #e5e7eb;">
            <p style="margin:0 0 10px;font-size:11px;font-weight:700;letter-spacing:1px;text-transform:uppercase;color:#94a3b8;">Research This Property</p>
            {buttons_html}
          </td>
        </tr>"""

    # Logo for header — use CID reference (Gmail-compatible inline image)
    logo_path = DATA_DIR / "logo.png"
    logo_cid = "logo@wholesaleomniverse.com"
    if logo_path.exists():
        logo_html = (
            f'<img src="cid:{logo_cid}" alt="Wholesale Omniverse" width="116" '
            f'style="display:block;width:116px;height:auto;'
            f'filter:drop-shadow(0 0 10px rgba(245,158,11,0.7));" />'
        )
    else:
        logo_html = ""

    # Convert plain text to HTML paragraphs
    lines = plain_body.strip().split("\n")
    html_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("•"):
            html_lines.append(f'<li style="margin:6px 0;color:#374151;">{stripped[1:].strip()}</li>')
        elif stripped == "":
            if html_lines and html_lines[-1] != "</ul>":
                html_lines.append("</p><p>")
        else:
            html_lines.append(stripped)

    body_html = ""
    in_list = False
    prev_was_text = False
    for item in html_lines:
        if item.startswith("<li"):
            if not in_list:
                body_html += '<ul style="margin:12px 0 12px 20px;padding:0;">'
                in_list = True
            body_html += item
            prev_was_text = False
        elif item == "</p><p>":
            if in_list:
                body_html += "</ul>"
                in_list = False
            body_html += "</p><p>"
            prev_was_text = False
        else:
            if in_list:
                body_html += "</ul>"
                in_list = False
            if prev_was_text:
                body_html += "<br>"
            body_html += item
            prev_was_text = True
    if in_list:
        body_html += "</ul>"

    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"></head>
<body style="margin:0;padding:0;background-color:#f3f4f6;font-family:'Helvetica Neue',Helvetica,Arial,sans-serif;">

  <!-- Wrapper -->
  <table width="100%" cellpadding="0" cellspacing="0" style="background-color:#f3f4f6;padding:32px 16px;">
    <tr><td align="center">
      <table width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;">

        <!-- HEADER -->
        <tr>
          <td style="background-color:#0f172a;border-radius:8px 8px 0 0;padding:20px 32px;">
            <table width="100%" cellpadding="0" cellspacing="0">
              <tr>
                <!-- Left: branding text -->
                <td style="vertical-align:middle;">
                  <p style="margin:0 0 3px;font-size:10px;letter-spacing:3px;color:#94a3b8;text-transform:uppercase;">Real Estate Investment</p>
                  <p style="margin:0 0 3px;font-size:28px;font-weight:900;color:#ffffff;letter-spacing:1px;line-height:1;">
                    WHOLESALE <span style="color:#f59e0b;">OMNIVERSE</span>
                  </p>
                  <p style="margin:0;font-size:10px;letter-spacing:0.3px;">
                    <span style="color:#f59e0b;">Your </span><span style="color:#cbd5e1;">portal </span><span style="color:#f59e0b;">to </span><span style="color:#cbd5e1;">premium </span><span style="color:#f59e0b;">real </span><span style="color:#cbd5e1;">estate</span>
                  </p>
                </td>
                <!-- Right: logo -->
                <td style="vertical-align:middle;text-align:right;width:90px;">
                  {logo_html}
                </td>
              </tr>
            </table>
          </td>
        </tr>

        <!-- ACCENT BAR -->
        <tr>
          <td style="background:linear-gradient(90deg,#f59e0b,#ef4444,#f59e0b);height:3px;font-size:0;line-height:0;">&nbsp;</td>
        </tr>

        <!-- BODY -->
        <tr>
          <td style="background-color:#ffffff;padding:40px 40px 28px;border-left:1px solid #e5e7eb;border-right:1px solid #e5e7eb;">
            <p style="margin:0 0 16px;font-size:15px;line-height:1.7;color:#374151;">
              {body_html}
            </p>
          </td>
        </tr>

        {map_img_html}

        {sources_section}

        <!-- DIVIDER -->
        <tr>
          <td style="background-color:#ffffff;padding:0 40px;border-left:1px solid #e5e7eb;border-right:1px solid #e5e7eb;">
            <hr style="border:none;border-top:1px solid #e5e7eb;margin:0;">
          </td>
        </tr>

        <!-- CONTACT CARD -->
        <tr>
          <td style="background-color:#ffffff;padding:24px 40px 32px;border-left:1px solid #e5e7eb;border-right:1px solid #e5e7eb;">
            <table cellpadding="0" cellspacing="0">
              <tr>
                <td style="padding-right:16px;vertical-align:middle;">
                  <div style="width:44px;height:44px;background-color:#f59e0b;border-radius:50%;text-align:center;line-height:44px;font-size:18px;font-weight:800;color:#0f172a;">W</div>
                </td>
                <td style="vertical-align:middle;">
                  <p style="margin:0;font-size:14px;font-weight:700;color:#0f172a;">{sender_name}</p>
                  <p style="margin:2px 0 0;font-size:13px;color:#6b7280;">Wholesale Omniverse LLC</p>
                  <p style="margin:2px 0 0;font-size:13px;color:#6b7280;"><a href="mailto:{sender_email}" style="color:#f59e0b;text-decoration:none;">{sender_email}</a></p>
                </td>
              </tr>
            </table>
          </td>
        </tr>

        <!-- TRUST BADGES -->
        <tr>
          <td style="background-color:#f8fafc;padding:16px 40px;border:1px solid #e5e7eb;border-top:none;text-align:center;">
            <table cellpadding="0" cellspacing="0" width="100%">
              <tr>
                <td align="center" style="padding:0 8px;font-size:12px;color:#6b7280;border-right:1px solid #d1d5db;">&#10003;&nbsp; <strong>Cash Offers</strong></td>
                <td align="center" style="padding:0 8px;font-size:12px;color:#6b7280;border-right:1px solid #d1d5db;">&#10003;&nbsp; <strong>Close in 2–3 Weeks</strong></td>
                <td align="center" style="padding:0 8px;font-size:12px;color:#6b7280;border-right:1px solid #d1d5db;">&#10003;&nbsp; <strong>No Repairs Needed</strong></td>
                <td align="center" style="padding:0 8px;font-size:12px;color:#6b7280;">&#10003;&nbsp; <strong>No Agent Fees</strong></td>
              </tr>
            </table>
          </td>
        </tr>

        <!-- FOOTER -->
        <tr>
          <td style="background-color:#0f172a;border-radius:0 0 8px 8px;padding:20px 40px;text-align:center;">
            <p style="margin:0 0 10px;font-size:11px;color:#475569;line-height:1.6;">
              &copy; 2026 Wholesale Omniverse LLC. All rights reserved.<br>
              You received this email because your property matches our buying criteria.<br>
              To unsubscribe, reply with &ldquo;remove&rdquo; in the subject line.
            </p>
            <a href="mailto:{company_email}" style="color:#f59e0b;text-decoration:none;font-weight:700;font-size:13px;letter-spacing:0.3px;">{company_email}</a>
          </td>
        </tr>

      </table>
    </td></tr>
  </table>

</body>
</html>"""


def send_outreach_email(
    to_email: str,
    owner_name: str,
    property_address: str,
    city: str,
    state: str = "",
    template_type: str = "motivated_seller",
    sender_name: str = "",
    sender_phone: str = "",
    custom_subject: str = "",
    custom_body: str = "",
    source_url: str = "",
) -> dict:
    """
    Send a professional outreach email to a motivated seller.
    template_type options: motivated_seller, probate, pre_foreclosure, vacant
    Requires SENDER_EMAIL and SENDER_EMAIL_PASSWORD in environment.
    """
    sender_email = os.environ.get("SENDER_EMAIL", "")
    sender_password = os.environ.get("SENDER_EMAIL_PASSWORD", "")
    sender_name = sender_name or os.environ.get("SENDER_NAME", "A Local Investor")
    sender_phone = sender_phone or os.environ.get("SENDER_PHONE", "")

    if not sender_email or not sender_password:
        return {
            "error": "Email credentials not set. Add SENDER_EMAIL and SENDER_EMAIL_PASSWORD to your .env file.",
            "setup_instructions": "See .env.example for how to configure Gmail sending.",
        }

    if not to_email or "@" not in to_email:
        return {"error": f"Invalid email address: {to_email}"}

    template = EMAIL_TEMPLATES.get(template_type, EMAIL_TEMPLATES["motivated_seller"])
    subject = custom_subject or template["subject"].format(
        address=property_address, owner_name=owner_name, city=city
    )
    body = custom_body or template["body"].format(
        owner_name=owner_name,
        address=property_address,
        city=city,
        sender_name=sender_name,
        sender_phone=sender_phone,
        sender_email=sender_email,
        company_name=COMPANY_NAME,
        company_email=COMPANY_EMAIL,
    )

    try:
        # Correct MIME structure for inline images (Gmail-compatible):
        # multipart/mixed
        #   multipart/related
        #     multipart/alternative  (plain + html)
        #     image/png              (logo, inline CID)
        #   application/pdf          (contract attachment)

        msg = MIMEMultipart("mixed")
        msg["Subject"] = subject
        msg["From"] = f"{COMPANY_NAME} <{sender_email}>"
        msg["To"] = to_email

        related = MIMEMultipart("related")

        # Fetch map image server-side so it can be CID-embedded (Gmail blocks external URLs)
        map_bytes = b""
        if property_address and city:
            map_bytes = _fetch_map_image_bytes(property_address, city, state)

        alt = MIMEMultipart("alternative")
        plain_footer = f"\n\n---\nWholesale Omniverse LLC | {COMPANY_EMAIL}\nTo unsubscribe, reply with 'remove' in the subject line."
        alt.attach(MIMEText(body + plain_footer, "plain"))
        html_body = _build_html_email(
            body, sender_name, sender_phone, sender_email, COMPANY_EMAIL,
            address=property_address, city=city, state=state, source_url=source_url,
            has_map=bool(map_bytes),
        )
        alt.attach(MIMEText(html_body, "html"))
        related.attach(alt)

        # Attach logo as inline CID image
        logo_path = DATA_DIR / "logo.png"
        if logo_path.exists():
            with open(logo_path, "rb") as f:
                logo_part = MIMEBase("image", "png")
                logo_part.set_payload(f.read())
            encoders.encode_base64(logo_part)
            logo_part.add_header("Content-ID", "<logo@wholesaleomniverse.com>")
            logo_part.add_header("Content-Disposition", "inline", filename="logo.png")
            related.attach(logo_part)

        # Attach map as inline CID image
        if map_bytes:
            map_part = MIMEBase("image", "png")
            map_part.set_payload(map_bytes)
            encoders.encode_base64(map_part)
            map_part.add_header("Content-ID", "<map@wholesaleomniverse.com>")
            map_part.add_header("Content-Disposition", "inline", filename="map.png")
            related.attach(map_part)

        msg.attach(related)

        pdf_path = DATA_DIR / "wholesale_contract.pdf"
        if pdf_path.exists():
            with open(pdf_path, "rb") as f:
                part = MIMEBase("application", "octet-stream")
                part.set_payload(f.read())
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", "attachment", filename="Wholesale_Omniverse_Contract.pdf")
            msg.attach(part)

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(sender_email, sender_password)
            server.sendmail(sender_email, to_email, msg.as_string())

        # Log the email
        log = _load(EMAIL_LOG_FILE, [])
        log.append({
            "to": to_email,
            "owner_name": owner_name,
            "property_address": property_address,
            "city": city,
            "template": template_type,
            "subject": subject,
            "sent_at": _now(),
            "status": "sent",
            "attachment": "Wholesale_Omniverse_Contract.pdf" if pdf_path.exists() else None,
        })
        _save(EMAIL_LOG_FILE, log)

        return {
            "status": "sent",
            "to": to_email,
            "owner": owner_name,
            "property": property_address,
            "subject": subject,
            "template_used": template_type,
        }
    except smtplib.SMTPAuthenticationError:
        return {"error": "Gmail authentication failed. Make sure you're using an App Password, not your regular password. See: myaccount.google.com/apppasswords"}
    except Exception as e:
        return {"error": str(e)}


# ─── Tool: Bulk Email Campaign ────────────────────────────────────────────────

def run_email_campaign(
    city: str,
    state: str,
    template_type: str = "motivated_seller",
    sender_name: str = "",
    sender_phone: str = "",
) -> dict:
    """
    Run an email campaign across all leads in a city that have email addresses.
    Automatically picks the right template based on each lead's motivation.
    """
    leads = _load(LEADS_FILE, {})
    results = {"sent": [], "skipped": [], "failed": []}

    motivation_to_template = {
        "probate": "probate",
        "pre-foreclosure": "pre_foreclosure",
        "foreclosure": "pre_foreclosure",
        "vacant": "vacant",
        "tax delinquent": "motivated_seller",
        "divorce": "motivated_seller",
    }

    for lead_id, lead in leads.items():
        if lead.get("city", "").lower() != city.lower():
            continue
        email = lead.get("seller_email", "")
        if not email or "@" not in email:
            results["skipped"].append({"lead_id": lead_id, "reason": "no email on file"})
            continue

        motivation = lead.get("motivation", "").lower()
        tpl = "motivated_seller"
        for key, val in motivation_to_template.items():
            if key in motivation:
                tpl = val
                break
        tpl = template_type if template_type != "motivated_seller" else tpl

        result = send_outreach_email(
            to_email=email,
            owner_name=lead.get("seller_name", "Homeowner"),
            property_address=lead.get("address", ""),
            city=city,
            template_type=tpl,
            sender_name=sender_name,
            sender_phone=sender_phone,
        )

        if result.get("status") == "sent":
            results["sent"].append({"lead_id": lead_id, "to": email})
            leads[lead_id]["status"] = "contacted"
            leads[lead_id]["updated_at"] = _now()
        else:
            results["failed"].append({"lead_id": lead_id, "error": result.get("error")})
        time.sleep(2)  # Rate limit — 1 email per 2 seconds

    _save(LEADS_FILE, leads)
    return {
        "campaign_complete": True,
        "city": city, "state": state,
        "sent": len(results["sent"]),
        "skipped": len(results["skipped"]),
        "failed": len(results["failed"]),
        "details": results,
    }


# ─── Tool: Get Email Log ──────────────────────────────────────────────────────

def get_email_log(limit: int = 20) -> dict:
    """View the history of all outreach emails sent."""
    log = _load(EMAIL_LOG_FILE, [])
    recent = sorted(log, key=lambda x: x.get("sent_at", ""), reverse=True)[:limit]
    return {
        "total_sent": len(log),
        "recent_emails": recent,
        "unique_properties": len(set(e.get("property_address") for e in log)),
    }


# ─── Tool: Bulk Skip Trace ───────────────────────────────────────────────────

def skip_trace_and_email_all(city: str = "", state: str = "", limit: int = 50) -> dict:
    """
    Find contact info for every lead missing an email or phone, then immediately
    send outreach emails to any lead where an email is found.
    Filters by city/state if provided, otherwise processes the whole pipeline.
    """
    leads = _load(LEADS_FILE, {})
    GENERIC_NAMES = {"unknown", "property owner", "owner", "n/a", "na", "", "none"}
    targets = [
        l for l in leads.values()
        if not l.get("seller_email")
        and (not city or l.get("city","").lower() == city.lower())
        and l.get("status") not in ("assigned", "dead")
        and l.get("address")
    ][:limit]

    enriched, emailed_count = 0, 0
    template_map = {
        "tax_delinquent": "pre_foreclosure", "code_violations": "vacant",
        "foreclosure": "pre_foreclosure", "probate": "probate",
        "vacant": "vacant", "pre_foreclosure": "pre_foreclosure",
    }

    for lead in targets:
        name = lead.get("seller_name", "")
        lcity = lead.get("city", city)
        lstate = lead.get("state", state)
        address = lead.get("address", "")

        # Step 0: County assessor lookup if owner name is missing or generic
        if not name or name.lower() in GENERIC_NAMES:
            assessor = lookup_owner_by_address(address, lcity, lstate)
            if assessor.get("owner_name"):
                name = assessor["owner_name"]
                lead["seller_name"] = name
                lead["updated_at"] = _now()
                leads[lead["lead_id"]] = lead
            time.sleep(0.3)

        if not name or name.lower() in GENERIC_NAMES:
            continue  # still no owner name, skip

        # Free skip trace
        free = _free_skip_trace(name, lcity, lstate, address)
        phones_list = free.get("phones") or []
        emails_list = free.get("emails") or []
        phone = phones_list[0] if phones_list else None
        email = emails_list[0] if emails_list else None

        # Bing web search fallback
        if not email:
            contact = find_owner_contact(name, lcity, lstate, address)
            found_emails = contact.get("emails_found") or []
            found_phones = contact.get("phones_found") or []
            email = found_emails[0] if found_emails else None
            if not phone:
                phone = found_phones[0] if found_phones else None

        # BatchSkipTracing if still missing and key is set
        bst_key = os.environ.get("BATCHSKIPTRACING_API_KEY", "")
        if bst_key and not email:
            result = _batchskiptracing_lookup(name, address, lcity, lstate)
            bst_emails = result.get("emails") or []
            bst_phones = result.get("phones") or []
            email = bst_emails[0] if bst_emails else None
            if not phone:
                phone = bst_phones[0] if bst_phones else None

        if email or phone:
            lead["seller_email"] = email or lead.get("seller_email", "")
            lead["seller_phone"] = phone or lead.get("seller_phone", "")
            lead["updated_at"] = _now()
            leads[lead["lead_id"]] = lead
            enriched += 1

        if email:
            template = template_map.get(lead.get("motivation", ""), "motivated_seller")
            result = send_outreach_email(
                to_email=email,
                owner_name=name,
                property_address=address,
                city=lcity,
                template_type=template,
            )
            if result.get("status") == "sent":
                lead["status"] = "contacted"
                leads[lead["lead_id"]] = lead
                emailed_count += 1
            time.sleep(1.5)

        time.sleep(0.5)

    _save(LEADS_FILE, leads)
    return {
        "leads_processed": len(targets),
        "contacts_found": enriched,
        "emails_sent": emailed_count,
        "summary": f"Processed {len(targets)} leads — found contact info for {enriched}, sent {emailed_count} emails.",
    }


# ─── Tool: Notify Cash Buyers ─────────────────────────────────────────────────

def notify_cash_buyers(city: str = "", state: str = "") -> dict:
    """
    Email all cash buyers whose target markets overlap with current pipeline leads.
    Sends a deal summary showing available properties in their area.
    """
    buyers = _load(BUYERS_FILE, {})
    leads = _load(LEADS_FILE, {})

    lead_list = [
        l for l in leads.values()
        if l.get("status") not in ("assigned", "dead")
        and (not city or l.get("city","").lower() == city.lower())
    ]
    if not lead_list:
        return {"status": "no_leads", "message": "No active leads to notify buyers about."}

    cities_with_leads = list({l.get("city","") for l in lead_list if l.get("city")})
    notified = []

    for buyer_id, buyer in buyers.items():
        buyer_email = buyer.get("email", "")
        buyer_markets = buyer.get("markets", "").lower()
        if not buyer_email or "@" not in buyer_email:
            continue

        # Check if buyer operates in any city we have leads for
        matching_leads = [
            l for l in lead_list
            if l.get("city","").lower() in buyer_markets
            or l.get("state","").lower() in buyer_markets
            or not buyer_markets  # buyer with no specified market gets everything
        ]
        if not matching_leads:
            continue

        # Build deal list for email body
        deal_lines = []
        for l in matching_leads[:10]:
            addr = l.get("address", "Unknown")
            lcity = l.get("city", "")
            motivation = l.get("motivation", "motivated seller")
            arv = l.get("estimated_arv", 0)
            mao = l.get("estimated_mao", 0)
            line = f"  • {addr}, {lcity} — {motivation.replace('_',' ').title()}"
            if arv:
                line += f" | ARV ~${arv:,.0f}"
            if mao:
                line += f" | MAO ${mao:,.0f}"
            deal_lines.append(line)

        body = f"""Hi {buyer.get('name', 'Investor')},

We have {len(matching_leads)} new wholesale deal{'s' if len(matching_leads) != 1 else ''} available in your target market{'s' if len(cities_with_leads) != 1 else ''}.

AVAILABLE PROPERTIES:
{chr(10).join(deal_lines)}

These are off-market distressed properties — tax delinquent, pre-foreclosure, code violations, and motivated sellers. We move fast and can close in 2–3 weeks.

Reply to this email or call us to get the full deal package on any property.

{os.environ.get('SENDER_NAME', 'Wholesale Omniverse')}
{os.environ.get('SENDER_PHONE', '')}
{os.environ.get('SENDER_EMAIL', '')}

---
Wholesale Omniverse LLC | info@wholesaleomniverse.com
Reply STOP to unsubscribe.
"""
        result = send_outreach_email(
            to_email=buyer_email,
            owner_name=buyer.get("name", "Investor"),
            property_address=f"{len(matching_leads)} properties in {', '.join(cities_with_leads[:3])}",
            city=cities_with_leads[0] if cities_with_leads else city,
            template_type="motivated_seller",
            custom_subject=f"[Wholesale Omniverse LLC] {len(matching_leads)} New Deal{'s' if len(matching_leads) != 1 else ''} Available — {', '.join(cities_with_leads[:2])}",
            custom_body=body,
        )
        if result.get("status") == "sent":
            notified.append({"buyer": buyer.get("name"), "email": buyer_email, "deals_sent": len(matching_leads)})
        time.sleep(1)

    return {
        "buyers_notified": len(notified),
        "active_leads_shared": len(lead_list),
        "cities": cities_with_leads,
        "details": notified,
    }


# ─── Tool Registry ────────────────────────────────────────────────────────────

TOOLS = [
    {
        "name": "analyze_deal",
        "description": "Analyze a wholesale real estate deal — calculates MAO, equity, assignment fee, and gives a verdict (strong deal / does not work).",
        "input_schema": {
            "type": "object",
            "properties": {
                "address": {"type": "string"},
                "arv": {"type": "number", "description": "After Repair Value — what the property is worth fixed up"},
                "repair_cost": {"type": "number", "description": "Estimated cost of repairs"},
                "asking_price": {"type": "number", "description": "Seller's current asking price"},
                "assignment_fee": {"type": "number", "description": "Your wholesale fee (default $10,000)", "default": 10000},
                "closing_costs": {"type": "number", "description": "Estimated closing costs (default $2,000)", "default": 2000},
                "holding_costs": {"type": "number", "description": "Holding costs if applicable (default $0)", "default": 0},
            },
            "required": ["address", "arv", "repair_cost", "asking_price"],
        },
    },
    {
        "name": "research_market",
        "description": "Research a neighborhood — finds recent sales data, market trends, ARV signals, and investor activity.",
        "input_schema": {
            "type": "object",
            "properties": {
                "address": {"type": "string"},
                "city": {"type": "string"},
                "state": {"type": "string"},
            },
            "required": ["address", "city", "state"],
        },
    },
    {
        "name": "find_motivated_sellers",
        "description": "Search for motivated seller sources and lead strategies in a target market.",
        "input_schema": {
            "type": "object",
            "properties": {
                "city": {"type": "string"},
                "state": {"type": "string"},
                "strategy": {"type": "string", "description": "Lead type: foreclosure, probate, tax_delinquent, vacant, divorce, all", "default": "all"},
            },
            "required": ["city", "state"],
        },
    },
    {
        "name": "save_lead",
        "description": "Save a motivated seller lead to the pipeline.",
        "input_schema": {
            "type": "object",
            "properties": {
                "address": {"type": "string"},
                "city": {"type": "string"},
                "state": {"type": "string"},
                "seller_name": {"type": "string", "default": ""},
                "seller_phone": {"type": "string", "default": ""},
                "seller_email": {"type": "string", "default": ""},
                "asking_price": {"type": "number", "default": 0},
                "estimated_arv": {"type": "number", "default": 0},
                "estimated_repairs": {"type": "number", "default": 0},
                "lead_source": {"type": "string", "description": "Where the lead came from (e.g. driving for dollars, cold call, direct mail)", "default": ""},
                "motivation": {"type": "string", "description": "Why seller is motivated (e.g. foreclosure, probate, divorce)", "default": ""},
                "notes": {"type": "string", "default": ""},
            },
            "required": ["address", "city", "state"],
        },
    },
    {
        "name": "get_leads",
        "description": "Retrieve all seller leads, optionally filtered by status or city.",
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "description": "Filter by: new, contacted, negotiating, under_contract, assigned, dead", "default": ""},
                "city": {"type": "string", "default": ""},
            },
        },
    },
    {
        "name": "update_lead_status",
        "description": "Update a lead's pipeline status.",
        "input_schema": {
            "type": "object",
            "properties": {
                "lead_id": {"type": "string"},
                "status": {"type": "string", "description": "new / contacted / negotiating / under_contract / assigned / dead"},
                "notes": {"type": "string", "default": ""},
            },
            "required": ["lead_id", "status"],
        },
    },
    {
        "name": "add_cash_buyer",
        "description": "Add an investor/cash buyer to your buyers list.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "phone": {"type": "string", "default": ""},
                "email": {"type": "string", "default": ""},
                "buy_box": {"type": "string", "description": "What the buyer looks for (e.g. SFR 3/2, under $150k, 30%+ equity)", "default": ""},
                "markets": {"type": "string", "description": "Cities/states buyer operates in", "default": ""},
                "max_price": {"type": "number", "default": 0},
                "preferred_property_types": {"type": "string", "default": ""},
                "notes": {"type": "string", "default": ""},
            },
            "required": ["name"],
        },
    },
    {
        "name": "get_cash_buyers",
        "description": "Retrieve your cash buyers list.",
        "input_schema": {
            "type": "object",
            "properties": {
                "market": {"type": "string", "description": "Filter by market/city", "default": ""},
            },
        },
    },
    {
        "name": "create_contract",
        "description": "Record a property going under contract with the seller.",
        "input_schema": {
            "type": "object",
            "properties": {
                "lead_id": {"type": "string"},
                "contract_price": {"type": "number"},
                "assignment_fee": {"type": "number"},
                "close_date": {"type": "string", "description": "Target close date (YYYY-MM-DD)"},
                "earnest_money": {"type": "number", "default": 500},
                "inspection_period_days": {"type": "integer", "default": 14},
                "notes": {"type": "string", "default": ""},
            },
            "required": ["lead_id", "contract_price", "assignment_fee", "close_date"],
        },
    },
    {
        "name": "get_contracts",
        "description": "Retrieve all contracts.",
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "description": "Filter by: active, assigned, cancelled", "default": ""},
            },
        },
    },
    {
        "name": "assign_contract",
        "description": "Assign a contract to a cash buyer — closes the wholesale deal and records your assignment fee.",
        "input_schema": {
            "type": "object",
            "properties": {
                "contract_id": {"type": "string"},
                "buyer_id": {"type": "string"},
                "final_assignment_fee": {"type": "number"},
                "notes": {"type": "string", "default": ""},
            },
            "required": ["contract_id", "buyer_id", "final_assignment_fee"],
        },
    },
    {
        "name": "lookup_owner_by_address",
        "description": "Look up the property owner name from county assessor / open data records for a given address. Free, no API key needed. Use this before skip tracing when owner name is unknown.",
        "input_schema": {
            "type": "object",
            "properties": {
                "address": {"type": "string", "description": "Street address"},
                "city":    {"type": "string"},
                "state":   {"type": "string", "description": "2-letter state code"},
            },
            "required": ["address", "city", "state"],
        },
    },
    {
        "name": "skip_trace_guidance",
        "description": "Get guidance on how to find contact info for a property owner via skip tracing.",
        "input_schema": {
            "type": "object",
            "properties": {
                "property_address": {"type": "string"},
                "owner_name": {"type": "string", "default": ""},
            },
            "required": ["property_address"],
        },
    },
    {
        "name": "get_business_summary",
        "description": "Get a full wholesale business summary — pipeline, contracts, earnings, buyers list.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "lookup_property_records",
        "description": "Look up public property records for an address — finds owner name, assessed value, tax status, and links to county assessor.",
        "input_schema": {
            "type": "object",
            "properties": {
                "address": {"type": "string"},
                "city": {"type": "string"},
                "state": {"type": "string"},
            },
            "required": ["address", "city", "state"],
        },
    },
    {
        "name": "import_and_email_leads",
        "description": "Import a list of property owner names/addresses (from a manually downloaded government list, CSV, or any source), look up their emails via web search, save them as pipeline leads, and optionally send outreach emails automatically.",
        "input_schema": {
            "type": "object",
            "properties": {
                "leads": {
                    "type": "array",
                    "description": "List of lead objects. Each should have owner_name and/or address. Email and phone are optional.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "owner_name": {"type": "string"},
                            "address": {"type": "string"},
                            "email": {"type": "string"},
                            "phone": {"type": "string"},
                        },
                    },
                },
                "city": {"type": "string"},
                "state": {"type": "string"},
                "record_type": {
                    "type": "string",
                    "description": "Lead type for template selection: motivated_seller, tax_delinquent, code_violations, foreclosure, probate, vacant",
                    "default": "motivated_seller",
                },
                "auto_email": {"type": "boolean", "description": "If true, send outreach emails to all leads with found emails", "default": False},
            },
            "required": ["leads", "city", "state"],
        },
    },
    {
        "name": "prospect_from_government_records",
        "description": "Search local government websites (county assessor, tax collector, sheriff sale, probate court, code enforcement) to find motivated seller prospects. Scrapes owner names and emails from public records, does web searches for missing contacts, saves all as leads, and optionally auto-emails them.",
        "input_schema": {
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "City to search in"},
                "state": {"type": "string", "description": "Two-letter state code, e.g. TX, FL, GA"},
                "county": {"type": "string", "description": "County name (defaults to city if omitted)", "default": ""},
                "record_type": {
                    "type": "string",
                    "description": "Type of government record to search: tax_delinquent, code_violations, foreclosure, probate, or vacant",
                    "default": "tax_delinquent",
                },
                "max_prospects": {"type": "integer", "description": "Max number of prospects to return", "default": 10},
                "auto_email": {"type": "boolean", "description": "If true, automatically emails all prospects with found email addresses", "default": False},
            },
            "required": ["city", "state"],
        },
    },
    {
        "name": "find_owner_contact",
        "description": "Search for a property owner's email and phone number via web — checks LinkedIn, Facebook, Whitepages, and general web.",
        "input_schema": {
            "type": "object",
            "properties": {
                "owner_name": {"type": "string"},
                "city": {"type": "string"},
                "state": {"type": "string"},
                "address": {"type": "string", "default": ""},
            },
            "required": ["owner_name", "city", "state"],
        },
    },
    {
        "name": "send_outreach_email",
        "description": "Send a professional outreach email to a motivated seller. Automatically formats a compelling message based on their situation.",
        "input_schema": {
            "type": "object",
            "properties": {
                "to_email": {"type": "string", "description": "Seller's email address"},
                "owner_name": {"type": "string"},
                "property_address": {"type": "string"},
                "city": {"type": "string"},
                "template_type": {"type": "string", "description": "motivated_seller, probate, pre_foreclosure, or vacant", "default": "motivated_seller"},
                "sender_name": {"type": "string", "default": ""},
                "sender_phone": {"type": "string", "default": ""},
                "custom_subject": {"type": "string", "default": ""},
                "custom_body": {"type": "string", "default": ""},
            },
            "required": ["to_email", "owner_name", "property_address", "city"],
        },
    },
    {
        "name": "run_email_campaign",
        "description": "Run an outreach email campaign to all leads in a city that have email addresses on file. Auto-selects the right template per lead.",
        "input_schema": {
            "type": "object",
            "properties": {
                "city": {"type": "string"},
                "state": {"type": "string"},
                "template_type": {"type": "string", "default": "motivated_seller"},
                "sender_name": {"type": "string", "default": ""},
                "sender_phone": {"type": "string", "default": ""},
            },
            "required": ["city", "state"],
        },
    },
    {
        "name": "get_email_log",
        "description": "View history of all outreach emails sent — who was contacted, when, and which template was used.",
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 20},
            },
        },
    },
    {
        "name": "scan_and_import_csv_leads",
        "description": (
            "Scan the data/import/ directory for CSV files from PropStream, BatchLeads, or any export. "
            "Auto-detects columns, imports all rows as pipeline leads, optionally sends outreach emails, "
            "then moves processed files to data/import/processed/. "
            "Supports PropStream, BatchLeads, ListSource, and generic name+address CSVs."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "auto_email": {"type": "boolean", "description": "Send outreach emails to all imported leads with emails", "default": False},
                "record_type": {"type": "string", "description": "Lead type for template: motivated_seller, tax_delinquent, pre_foreclosure, probate, vacant", "default": "motivated_seller"},
                "default_city": {"type": "string", "description": "City to use if CSV doesn't have a city column", "default": ""},
                "default_state": {"type": "string", "description": "State to use if CSV doesn't have a state column", "default": ""},
            },
        },
    },
    {
        "name": "scrape_craigslist_leads",
        "description": (
            "Scrape motivated seller listings from Redfin for a target city. "
            "Pulls below-market properties sorted by days on market (highest first = most motivated). "
            "Covers 20+ cities including Detroit MI, Baltimore MD, Memphis TN, Cleveland OH, Chicago IL, Kansas City MO. "
            "Saves all found properties as pipeline leads. Contact info is found by running skip_trace_and_email_all afterward. "
            "100% free — no API key needed."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "Target city (e.g. Detroit, Baltimore, Memphis)"},
                "state": {"type": "string", "description": "Two-letter state code (e.g. MI, MD, TN)"},
                "max_results": {"type": "integer", "description": "Max listings to scrape (default 25)", "default": 25},
                "auto_email": {"type": "boolean", "description": "Auto-send outreach to any listing with an email address", "default": False},
            },
            "required": ["city", "state"],
        },
    },
    {
        "name": "skip_trace_and_email_all",
        "description": (
            "Bulk skip-trace all pipeline leads that are missing contact info, then immediately send outreach emails "
            "to any lead where a phone or email is found. Uses free sources (FastPeopleSearch, TruePeopleSearch) and "
            "optionally the BatchSkipTracing paid API. Updates each lead's status to 'contacted' after emailing."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "Filter leads by city (empty = all cities)", "default": ""},
                "state": {"type": "string", "description": "Filter leads by state (empty = all states)", "default": ""},
                "limit": {"type": "integer", "description": "Max leads to process (default 50)", "default": 50},
            },
        },
    },
    {
        "name": "notify_cash_buyers",
        "description": (
            "Email all cash buyers about active pipeline deals in their target markets. "
            "Matches each buyer's target cities to available leads, then sends a deal summary email listing "
            "available properties with address, ARV estimate, and asking price."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "Only notify buyers interested in this city (empty = all cities)", "default": ""},
                "state": {"type": "string", "description": "Only notify buyers interested in this state (empty = all states)", "default": ""},
            },
        },
    },
]

TOOL_FUNCTIONS = {
    "scan_and_import_csv_leads": scan_and_import_csv_leads,
    "analyze_deal": analyze_deal,
    "research_market": research_market,
    "find_motivated_sellers": find_motivated_sellers,
    "save_lead": save_lead,
    "get_leads": get_leads,
    "update_lead_status": update_lead_status,
    "add_cash_buyer": add_cash_buyer,
    "get_cash_buyers": get_cash_buyers,
    "create_contract": create_contract,
    "get_contracts": get_contracts,
    "assign_contract": assign_contract,
    "lookup_owner_by_address": lookup_owner_by_address,
    "skip_trace_guidance": skip_trace_guidance,
    "get_business_summary": get_business_summary,
    "lookup_property_records": lookup_property_records,
    "import_and_email_leads": import_and_email_leads,
    "prospect_from_government_records": prospect_from_government_records,
    "find_owner_contact": find_owner_contact,
    "send_outreach_email": send_outreach_email,
    "run_email_campaign": run_email_campaign,
    "get_email_log": get_email_log,
    "skip_trace_and_email_all": skip_trace_and_email_all,
    "notify_cash_buyers": notify_cash_buyers,
    "scrape_craigslist_leads": scrape_craigslist_leads,
}
