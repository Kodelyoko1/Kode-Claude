"""
HUDScout — government-foreclosed FHA property scraper for the wholesale pipeline.
Revenue: $97/mo subscription, $297 quarterly retainer, $497 white-label market pack.

What this gets you that Zillow/Redfin don't
-------------------------------------------
HUD Home Store lists former FHA-insured homes that defaulted; HUD now owns them
and is highly motivated to clear inventory at a discount. Each listing has a
public bid period: the first ~30 days are owner-occupant only, then the property
opens to investors — that investor window is the wholesaler's lane.

Source
------
HUD Home Store (https://www.hudhomestore.gov). Public, no API key. We hit the
same JSON endpoint the site's own JavaScript uses (`/SearchResult?handler=
GetFilteredResult`), seeded with the antiforgery token from `/searchresult`.
Results are normalized into the lead schema and dropped into `data/leads.json`
so the wholesale deal analyzer (`tools.py`) picks them up on its next run.

Pipeline (run_full_cycle)
-------------------------
    1. For each state in HUD_SEARCH_STATES, query the HUD JSON API.
    2. Normalize each property → lead dict matching tools.py's expectations
       (address, city, state, zip, price, bedrooms, bathrooms, sqft, source).
    3. De-dupe against the case-numbers we've already processed (stored inline
       in data/hd_leads.json under the "seen_cases" key).
    4. New leads → append to data/hd_leads.json AND data/leads.json.
    5. Build a markdown digest of today's new listings (data/hd_outputs/YYYY-MM-DD.md).
    6. Email the digest to the owner + any paying subscribers.
    7. Emit agent metrics for the ecosystem dashboard.

Resilience note
---------------
HUD's site is a Razor Pages SPA fronted by Yardi. If the JSON contract changes,
the two adjustment points are `_open_session()` (token + cookie bootstrap) and
`search_hud_properties()` (POST body + JSON field mapping in `_normalize`).
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests

sys.path.insert(0, str(Path(__file__).parent.parent))
from autonomous import storage, mailer, billing, metrics
from hudscout.health import record_state

# ============================================================================
# CONFIG
# ============================================================================

AGENT_KEY     = "hudscout"
DATA_DIR      = Path(__file__).parent.parent / "data"
LEADS_FILE    = DATA_DIR / "hd_leads.json"
GLOBAL_LEADS  = DATA_DIR / "leads.json"
DIGESTS_DIR   = DATA_DIR / "hd_outputs"

# HUD's JSON API takes a full state *name* in the `citystate` field (e.g. "Maine"),
# not the postal abbreviation. We accept either at config time and normalize.
STATE_NAMES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
    "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming", "DC": "District of Columbia",
    "PR": "Puerto Rico",
}

def _state_name(s: str) -> str:
    s = s.strip()
    return STATE_NAMES.get(s.upper(), s)

DEFAULT_STATES = ["ME", "NH", "VT", "MA", "CT", "RI", "NY"]
HUD_SEARCH_STATES = (
    [s.strip() for s in os.environ.get("HD_STATES", "").split(",") if s.strip()]
    or DEFAULT_STATES
)

SEARCH_TIMEOUT_SEC    = int(os.environ.get("HD_SEARCH_TIMEOUT", "20"))
DELIVERY_DAY_OF_WEEK  = int(os.environ.get("HD_DIGEST_DOW", "-1"))   # -1 = every day

# ---- HUD endpoints (adjust if HUD restructures) ---------------------------
BASE_URL    = "https://www.hudhomestore.gov"
LANDING_URL = f"{BASE_URL}/searchresult"
SEARCH_URL  = f"{BASE_URL}/SearchResult?handler=GetFilteredResult"
DETAIL_URL  = f"{BASE_URL}/Listing/Detail/"     # + case_number

# Alternative landing URLs to try if the primary 403s or redirects.
# HUD occasionally restructures Razor Pages paths; the JSON endpoint path
# stays stable, only the landing slug changes.
_LANDING_CANDIDATES = [
    f"{BASE_URL}/searchresult",
    f"{BASE_URL}/SearchResult",
    f"{BASE_URL}/home/index",
    f"{BASE_URL}/",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
}

# Token + cookies are pinned per-process: bootstrap once, reuse for every state.
# HUD uses both the hidden-input pattern and the cookie-header meta pattern —
# match either form regardless of attribute order or case.
_TOKEN_RE = re.compile(
    r'(?:'
    r'name=["\'](?:__RequestVerificationToken|request-verification-token)["\']'
    r'\s+value=["\']([^"\']+)["\']'
    r'|'
    r'value=["\']([^"\']+)["\']\s+name=["\'](?:__RequestVerificationToken|request-verification-token)["\']'
    r')',
    re.IGNORECASE,
)


# ============================================================================
# IO HELPERS
# ============================================================================

def _load(path: Path, default):
    if path.exists():
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            return default
    return default


def _save(path: Path, data) -> None:
    """Atomic write: tmp file in same dir + os.replace. Prevents the
    half-written-file class of bug that bit followup on 2026-06-02."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except Exception:
        try: os.unlink(tmp)
        except OSError: pass
        raise


# ============================================================================
# SCRAPER  (single function — easy to swap if HUD restructures)
# ============================================================================

def _open_session() -> tuple[requests.Session, str]:
    """Bootstrap a session against the HUD search page and extract the
    antiforgery token. Subsequent POSTs to SEARCH_URL must send this token
    in the `RequestVerificationToken` header AND carry the
    `.AspNetCore.Antiforgery.*` cookie that the GET deposited.

    Tries each URL in _LANDING_CANDIDATES in order, stopping at the first
    that returns HTTP 200 with a recognizable verification token. This
    handles the case where HUD restructures its Razor Pages routing.

    Raises RuntimeError with a diagnostic message distinguishing:
      - network-level block (403/connection refused)
      - page loaded but no token found (HTML structure changed)
    """
    session = requests.Session()
    session.headers.update(HEADERS)

    last_status = None
    last_url = None
    tried = []

    for candidate_url in _LANDING_CANDIDATES:
        tried.append(candidate_url)
        try:
            r = session.get(candidate_url, timeout=SEARCH_TIMEOUT_SEC,
                            allow_redirects=True)
        except requests.exceptions.ConnectionError as e:
            print(f"  [HUD] Connection error at {candidate_url}: {e}")
            continue
        except requests.exceptions.Timeout:
            print(f"  [HUD] Timeout at {candidate_url} (>{SEARCH_TIMEOUT_SEC}s)")
            continue
        except Exception as e:
            print(f"  [HUD] Request error at {candidate_url}: {e}")
            continue

        last_status = r.status_code
        last_url = r.url

        if r.status_code == 403:
            body_snippet = r.text[:120].replace("\n", " ").strip()
            print(f"  [HUD] 403 at {candidate_url}"
                  + (f" — {body_snippet}" if body_snippet else ""))
            continue
        if r.status_code != 200:
            print(f"  [HUD] HTTP {r.status_code} at {candidate_url}")
            continue

        # Page loaded — hunt for the token (hidden-input form).
        m = _TOKEN_RE.search(r.text)
        found_token = None
        if m:
            found_token = m.group(1) or m.group(2) or ""

        # Fallback: meta tag variant.
        if not found_token:
            meta_m = re.search(
                r'<meta\s+name=["\'](?:__RequestVerificationToken|request-verification-token)["\']'
                r'\s+content=["\']([^"\']+)["\']',
                r.text, re.IGNORECASE,
            )
            if meta_m:
                found_token = meta_m.group(1)

        if found_token:
            # Update module-level LANDING_URL so Referer header stays consistent.
            global LANDING_URL  # noqa: PLW0603
            LANDING_URL = candidate_url
            print(f"  [HUD] session bootstrapped from {candidate_url} "
                  f"(token len={len(found_token)}, cookies={len(session.cookies)})")
            return session, found_token

        title_m = re.search(r'<title[^>]*>([^<]+)', r.text, re.I)
        page_title = title_m.group(1)[:60] if title_m else "?"
        print(f"  [HUD] {candidate_url} returned 200 but no antiforgery token "
              f"(page title: {page_title})")

    # All candidates exhausted — give the operator a useful error.
    if last_status == 403:
        raise RuntimeError(
            f"HUD Home Store returned 403 Forbidden for all {len(tried)} landing "
            f"URL candidates. Possible causes: (1) the server's egress allowlist "
            f"does not include hudhomestore.gov — add it in network settings; "
            f"(2) HUD has implemented IP-based bot blocking — try adding a "
            f"residential proxy via HD_PROXY env var; (3) the site moved to a "
            f"new domain. Last URL tried: {last_url}"
        )
    if last_status is None:
        raise RuntimeError(
            f"Could not reach HUD Home Store — all {len(tried)} candidates "
            f"failed at the network level (connection refused or timeout). "
            f"Check that hudhomestore.gov is reachable from this host."
        )
    raise RuntimeError(
        f"HUD landing page (HTTP {last_status}) returned no request-verification-token "
        f"across {len(tried)} URL candidates. The site's HTML structure may have "
        f"changed. Patch _TOKEN_RE in hudscout/tools.py. Last URL: {last_url}"
    )


def search_hud_properties(state: str, session=None, token=None) -> list:
    """Query HUD's filtered-result API for one state. Returns normalized lead
    dicts. State may be a postal abbreviation ('ME') or full name ('Maine')."""
    if session is None or token is None:
        session, token = _open_session()
    headers = {
        "RequestVerificationToken": token,
        "Content-Type":   "application/x-www-form-urlencoded",
        "Origin":         BASE_URL,
        "Referer":        LANDING_URL,
        "X-Requested-With": "XMLHttpRequest",
    }
    body = {
        "citystate":        _state_name(state),
        "viewport":         "",
        "zoom":             "10",
        "geopickertype":    "",
        "geopickeroutput":  "",
        "locationchanged":  "",
        "locationgeoid":    "",
        "locationLat":      "",
        "locationLong":     "",
        "isdefault":        "0",
        "shapeboundary":    "",
    }
    try:
        r = session.post(SEARCH_URL, headers=headers, data=body,
                         timeout=SEARCH_TIMEOUT_SEC)
    except Exception as e:
        print(f"  [{state}] request failed: {e}")
        return []
    if r.status_code != 200:
        print(f"  [{state}] HTTP {r.status_code}")
        return []
    try:
        payload = r.json()
    except ValueError:
        print(f"  [{state}] non-JSON response (first 200 chars: {r.text[:200]})")
        return []

    # HUD's response envelope key is "searchresult". If that key is missing
    # or the structure changed, log the top-level keys so patching is easy.
    raw = payload.get("searchresult")
    if raw is None:
        top_keys = list(payload.keys())[:10] if isinstance(payload, dict) else type(payload).__name__
        print(f"  [{state}] JSON response has no 'searchresult' key; "
              f"top-level keys: {top_keys}")
        return []
    if not raw:
        return []

    # Log sample field names from the first record so we can spot renames.
    if raw and os.environ.get("HD_DEBUG"):
        first_keys = list(raw[0].keys()) if isinstance(raw[0], dict) else []
        print(f"  [{state}] sample record keys: {first_keys[:15]}")

    results = []
    for p in raw:
        try:
            norm = _normalize_property(p)
            if norm:  # skip empty dicts from malformed records
                results.append(norm)
        except Exception as e:
            print(f"  [{state}] _normalize_property failed on record: {e} — {str(p)[:120]}")
    return results


def _to_int(v) -> Optional[int]:
    if v in (None, "", "null"):
        return None
    try:
        return int(float(str(v).replace(",", "").strip()))
    except (TypeError, ValueError):
        return None


def _str(v) -> str:
    """Safe string coerce — returns "" for None, non-string, or whitespace-only."""
    if v is None:
        return ""
    try:
        return str(v).strip()
    except Exception:
        return ""


def _normalize_property(p: dict) -> dict:
    """Map a HUD JSON record into the lead schema the wholesale analyzer
    reads from data/leads.json.

    All field extractions are guarded: missing/None/non-string values produce
    empty strings or None rather than crashing. The field names listed here
    were correct as of the last verified HUD API response; if HUD renames
    fields and returns 0 results, check the raw payload via --diagnose and
    update the mappings below.

    Known alternate field names (right side = legacy, left = current):
      listPrice       / listingPrice / price
      propertyAddress / streetAddress / address1
      squareFootage   / sqft / squareFeet
      bedrooms        / bedroomCount / beds
      bathrooms       / bathroomCount / baths
    """
    if not isinstance(p, dict):
        return {}

    # Case number — primary dedup key
    case_no = _str(
        p.get("propertyCaseNumber")
        or p.get("caseNumber")
        or p.get("case_number")
        or ""
    )

    # Price — try several known aliases
    raw_price = (
        p.get("listPrice")
        or p.get("listingPrice")
        or p.get("price")
        or p.get("askingPrice")
        or 0
    )

    # Address components
    address = _str(
        p.get("propertyAddress")
        or p.get("streetAddress")
        or p.get("address1")
        or p.get("address")
    )
    city = _str(p.get("propertyCity") or p.get("city"))
    state = _str(p.get("propertyState") or p.get("state"))
    zip_code = _str(p.get("propertyZip") or p.get("zip") or p.get("zipCode"))
    county = _str(p.get("propertyCounty") or p.get("county"))

    # Numeric fields
    bedrooms = _to_int(p.get("bedrooms") or p.get("bedroomCount") or p.get("beds"))
    bathrooms = _to_int(p.get("bathrooms") or p.get("bathroomCount") or p.get("baths"))
    sqft = _to_int(
        p.get("squareFootage")
        or p.get("sqft")
        or p.get("squareFeet")
        or p.get("livingArea")
    )
    year_built = _to_int(p.get("yearBuilt") or p.get("year_built"))

    # Status / metadata
    prop_type   = _str(p.get("propertyType") or p.get("propType") or p.get("type"))
    status      = _str(p.get("propertyStatus") or p.get("status") or p.get("listingStatus"))
    fha         = _str(p.get("fhaFinancing") or p.get("fhaEligible"))
    list_period = _str(p.get("listingPeriod") or p.get("listPeriod"))
    list_date   = _str(p.get("listDate") or p.get("listingDate"))
    bid_open    = _str(p.get("bidOpenDate") or p.get("bidOpen") or p.get("investorDate"))
    bidder_types = _str(p.get("bidderTypes") or p.get("bidderType"))
    eligible    = _str(p.get("eligibleBidders") or p.get("eligibleBidder"))

    # Coordinates — keep as-is (float or None)
    lat = p.get("latitude") or p.get("lat")
    lon = p.get("longitude") or p.get("lon") or p.get("lng")

    return {
        "source":           "hudscout",
        "case_number":      case_no,
        "address":          address,
        "city":             city,
        "state":            state,
        "zip":              zip_code,
        "county":           county,
        "price":            _to_int(raw_price) or 0,
        "bedrooms":         bedrooms,
        "bathrooms":        bathrooms,
        "sqft":             sqft,
        "year_built":       year_built,
        "property_type":    prop_type,
        "status":           status,
        "fha_financing":    fha,
        "listing_period":   list_period,
        "list_date":        list_date,
        "bid_open_date":    bid_open,
        "bidder_types":     bidder_types,
        "eligible_bidders": eligible,
        "latitude":         lat,
        "longitude":        lon,
        "detail_url":       DETAIL_URL + case_no if case_no else "",
        "first_seen":       datetime.now().isoformat(),
    }


# ============================================================================
# PIPELINE
# ============================================================================

def harvest_all_states() -> list:
    """Scrape every configured state, sharing one bootstrapped session so we
    only pay for the antiforgery handshake once per cycle.

    Records per-state health after each query so diagnose.py can flag silent
    degradation. If token bootstrap fails, every configured state gets a
    record with the error text — owner sees the root cause in --health-report.
    """
    try:
        session, token = _open_session()
    except Exception as e:
        err = f"token_bootstrap: {type(e).__name__}: {str(e)[:120]}"
        print(f"  HUD session bootstrap failed: {e}")
        for st in HUD_SEARCH_STATES:
            record_state(st, 0, error=err)
        return []
    all_props = []
    for st in HUD_SEARCH_STATES:
        props = search_hud_properties(st, session=session, token=token)
        print(f"  [{st}] {len(props)} listings")
        record_state(st, len(props))
        all_props.extend(props)
        time.sleep(1.5)        # be polite to HUD's servers
    return all_props


def dedupe_and_persist(props: list) -> list:
    """Filter out cases we've seen before, append the rest to:
       1. data/hd_leads.json  (this agent's own store, seen-cases inside)
       2. data/leads.json     (the shared analyzer queue)
    Returns the list of brand-new leads."""
    store = _load(LEADS_FILE, {"seen_cases": [], "leads": []})
    seen = set(store.get("seen_cases", []))

    new_leads = []
    for p in props:
        if not p.get("case_number") or p["case_number"] in seen:
            continue
        new_leads.append(p)
        seen.add(p["case_number"])

    if new_leads:
        store["seen_cases"] = sorted(seen)
        store["leads"].extend(new_leads)
        # Keep the in-agent store bounded.
        if len(store["leads"]) > 5000:
            store["leads"] = store["leads"][-5000:]
        _save(LEADS_FILE, store)

        _push_to_shared_queue(new_leads)

    return new_leads


def _push_to_shared_queue(new_leads: list) -> None:
    """data/leads.json is a dict keyed by LEAD-NNNN that the wholesale deal
    analyzer reads. Append each new HUD lead with the next available id and
    the schema the analyzer expects (asking_price, seller_*, lead_source)."""
    shared = _load(GLOBAL_LEADS, {})
    if not isinstance(shared, dict):
        return
    next_num = 1 + max(
        (int(k.split("-")[-1]) for k in shared if k.startswith("LEAD-")
         and k.split("-")[-1].isdigit()),
        default=0,
    )
    now = datetime.now().isoformat()
    for lead in new_leads:
        lead_id = f"LEAD-{next_num:04d}"
        next_num += 1
        shared[lead_id] = {
            "lead_id":          lead_id,
            "address":          lead["address"],
            "city":             lead["city"],
            "state":            lead["state"],
            "asking_price":     lead.get("price", 0),
            "estimated_arv":    None,
            "estimated_mao":    None,
            "estimated_repairs": None,
            "seller_name":      "HUD Home Store",
            "seller_phone":     "",
            "seller_email":     "",
            "lead_source":      "HUDScout",
            "motivation":       (f"HUD-owned REO — {lead.get('status') or 'listed'}. "
                                 f"Bid open {lead.get('bid_open_date') or 'n/a'}. "
                                 f"FHA financing: {lead.get('fha_financing') or 'unknown'}."),
            "notes":            f"HUD case #{lead['case_number']} — {lead['detail_url']}",
            "status":           "new",
            "created_at":       now,
            "updated_at":       now,
        }
    _save(GLOBAL_LEADS, shared)


def build_digest(new_leads: list) -> Optional[Path]:
    if not new_leads:
        return None
    today = datetime.now().strftime("%Y-%m-%d")
    out = DIGESTS_DIR / f"{today}.md"
    DIGESTS_DIR.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# HUDScout digest — {today}",
        "",
        f"**{len(new_leads)} new HUD-owned property listings** across "
        f"{', '.join(sorted({l['state'] for l in new_leads}))}.",
        "",
        "Listings below are sorted by state then price (ascending). Each link",
        "opens the official HUD Home Store detail page.",
        "",
    ]
    by_state = {}
    for l in new_leads:
        by_state.setdefault(l["state"], []).append(l)
    for st in sorted(by_state):
        rows = sorted(by_state[st], key=lambda x: x.get("price") or 0)
        lines.append(f"## {st}  ({len(rows)} new)")
        lines.append("")
        for l in rows:
            price = f"${l['price']:,}" if l.get("price") else "n/a"
            status = f" — *{l['status']}*" if l.get("status") else ""
            beds = f"{l['bedrooms']} bd" if l.get("bedrooms") else ""
            baths = f"{l['bathrooms']} ba" if l.get("bathrooms") else ""
            specs = " · ".join(x for x in [beds, baths] if x)
            specs = f"  ({specs})" if specs else ""
            lines.append(f"- **{price}** — [{l['address'] or l['case_number']}]"
                          f"({l['detail_url']}){specs}{status}")
        lines.append("")
    out.write_text("\n".join(lines), encoding="utf-8")
    return out


def deliver_digest(digest_path: Optional[Path], new_leads: list) -> dict:
    """Send the digest to the owner + paying subscribers. Returns
    {fulfillment_sent: int}."""
    if not digest_path or not new_leads:
        return {"fulfillment_sent": 0}

    if DELIVERY_DAY_OF_WEEK >= 0 and datetime.now().weekday() != DELIVERY_DAY_OF_WEEK:
        return {"fulfillment_sent": 0}

    body = (
        f"Today's HUD Home Store sweep surfaced {len(new_leads)} new property "
        f"listings across {len({l['state'] for l in new_leads})} states.\n\n"
        f"The full markdown digest is attached. Every new lead has also been "
        f"pushed into data/leads.json so the wholesale deal analyzer will pick "
        f"them up on its next run.\n\n"
        f"— HUDScout, Wholesale Omniverse LLC"
    )
    sent = 0
    owner = os.environ.get("SMTP_USER")
    if owner:
        owner_result = mailer.send(
            AGENT_KEY, owner,
            f"HUDScout digest — {datetime.now():%b %d} ({len(new_leads)} new)",
            body, purpose="fulfillment",
            attachments=[str(digest_path)],
        )
        if owner_result.get("status") == "sent":
            sent += 1

    subs = storage.load("hd_subscribers.json", [])
    for s in subs:
        if s.get("status") != "active":
            continue
        result = mailer.send(
            AGENT_KEY, s["email"],
            f"HUDScout digest — {datetime.now():%b %d}",
            body, purpose="fulfillment",
            attachments=[str(digest_path)],
        )
        if result.get("status") == "sent":
            sent += 1
    return {"fulfillment_sent": sent}


# ============================================================================
# ENTRY POINT  (called by run_hudscout_auto.py)
# ============================================================================

def run_full_cycle() -> dict:
    print(f"HUDScout sweep: {', '.join(HUD_SEARCH_STATES)}")
    props = harvest_all_states()
    print(f"Harvested {len(props)} raw listings; deduping…")
    new_leads = dedupe_and_persist(props)
    digest = build_digest(new_leads)
    delivered = deliver_digest(digest, new_leads)

    rev  = billing.revenue_summary(AGENT_KEY)
    subs = storage.load("hd_subscribers.json", [])

    metrics.record(
        AGENT_KEY,
        prospects_added  = len(new_leads),
        outreach_sent    = 0,
        fulfillment_sent = delivered["fulfillment_sent"],
        active_subs      = sum(1 for s in subs if s.get("status") == "active"),
        mrr              = rev["mrr"],
        total_revenue    = rev["total_paid"],
    )
    return {
        "states_searched":  len(HUD_SEARCH_STATES),
        "raw_harvested":    len(props),
        "new_leads":        len(new_leads),
        "fulfillment_sent": delivered["fulfillment_sent"],
        "digest_path":      str(digest) if digest else None,
        **rev,
    }
