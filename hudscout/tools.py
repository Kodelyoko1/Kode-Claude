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

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# Token + cookies are pinned per-process: bootstrap once, reuse for every state.
_TOKEN_RE = re.compile(r'name="request-verification-token"\s+value="([^"]+)"')


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
    """Bootstrap a session against the search page and extract the antiforgery
    token. Subsequent POSTs to SEARCH_URL must send this token in the
    `RequestVerificationToken` header AND carry the `.AspNetCore.Antiforgery.*`
    cookie that the GET deposited."""
    session = requests.Session()
    session.headers.update(HEADERS)
    r = session.get(LANDING_URL, timeout=SEARCH_TIMEOUT_SEC)
    r.raise_for_status()
    m = _TOKEN_RE.search(r.text)
    if not m:
        raise RuntimeError("HUD landing page returned no request-verification-token; "
                           "site layout may have changed.")
    return session, m.group(1)


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
    raw = payload.get("searchresult") or []
    return [_normalize_property(p) for p in raw]


def _to_int(v) -> Optional[int]:
    if v in (None, "", "null"):
        return None
    try:
        return int(float(str(v).replace(",", "").strip()))
    except (TypeError, ValueError):
        return None


def _normalize_property(p: dict) -> dict:
    """Map the HUD JSON record into the lead schema the wholesale analyzer
    reads from data/leads.json."""
    case_no = (p.get("propertyCaseNumber") or "").strip()
    return {
        "source":         "hudscout",
        "case_number":    case_no,
        "address":        (p.get("propertyAddress") or "").strip(),
        "city":           (p.get("propertyCity") or "").strip(),
        "state":          (p.get("propertyState") or "").strip(),
        "zip":            (p.get("propertyZip") or "").strip(),
        "county":         (p.get("propertyCounty") or "").strip(),
        "price":          _to_int(p.get("listPrice")) or 0,
        "bedrooms":       _to_int(p.get("bedrooms")),
        "bathrooms":      _to_int(p.get("bathrooms")),
        "sqft":           _to_int(p.get("squareFootage")),
        "year_built":     _to_int(p.get("yearBuilt")),
        "property_type":  (p.get("propertyType") or "").strip(),
        "status":         (p.get("propertyStatus") or "").strip(),
        "fha_financing":  (p.get("fhaFinancing") or "").strip(),
        "listing_period": (p.get("listingPeriod") or "").strip(),
        "list_date":      (p.get("listDate") or "").strip(),
        "bid_open_date":  (p.get("bidOpenDate") or "").strip(),
        "bidder_types":   (p.get("bidderTypes") or "").strip(),
        "eligible_bidders": (p.get("eligibleBidders") or "").strip(),
        "latitude":       p.get("latitude"),
        "longitude":      p.get("longitude"),
        "detail_url":     DETAIL_URL + case_no,
        "first_seen":     datetime.now().isoformat(),
    }


# ============================================================================
# PIPELINE
# ============================================================================

def harvest_all_states() -> list:
    """Scrape every configured state, sharing one bootstrapped session so we
    only pay for the antiforgery handshake once per cycle."""
    try:
        session, token = _open_session()
    except Exception as e:
        print(f"  HUD session bootstrap failed: {e}")
        return []
    all_props = []
    for st in HUD_SEARCH_STATES:
        props = search_hud_properties(st, session=session, token=token)
        print(f"  [{st}] {len(props)} listings")
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
