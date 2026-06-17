"""
PropScout — free PropStream-style motivated-seller prospect engine.

Drives prospect_from_government_records() in tools.py across a curated grid of
(city, record_type) cells using verified Socrata + Carto endpoints. Saves
every prospect into data/ps_leads.json AND the shared data/leads.json pipeline,
writes a personalized cold-email draft per prospect into data/ps_drafts/, and
emails the owner a daily digest.

Entry point: run_full_cycle()
"""
import os
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import tools as core_tools  # prospect_from_government_records, _try_socrata, _try_carto
from autonomous import storage, mailer, billing, metrics
from propscout.health import record_cell
from propscout.attribution import tag_new_prospects

AGENT_KEY = "propscout"
DRAFTS_DIR = Path(__file__).parent.parent / "data" / "ps_drafts"

# Curated grid of (city, state, record_type) — every cell is verified to
# return real rows. Order matters: richest data first.
PROSPECT_GRID = [
    # ---- rich data (owner name + $ amount) ----
    ("philadelphia",  "PA", "tax_delinquent"),   # Carto: owner + $ owed + years
    ("norfolk",       "VA", "tax_delinquent"),   # Socrata: owner_name + address
    ("baltimore",     "MD", "tax_delinquent"),   # Socrata: owner + amount + address (data.baltimorecity.gov)
    ("detroit",       "MI", "tax_delinquent"),   # Socrata: owner + years delinquent (data.detroitmi.gov)
    ("cleveland",     "OH", "tax_delinquent"),   # Socrata: parcel + owner + taxes owed (data.clevelandohio.gov)
    ("pittsburgh",    "PA", "tax_delinquent"),   # Socrata: owner + balance + address (data.wprdc.org)
    ("st. louis",     "MO", "tax_delinquent"),   # Socrata: owner + address + amount (www.stlouis-mo.gov)
    # ---- foreclosure filings ----
    ("new york",      "NY", "foreclosure"),      # Socrata: respondent + address
    ("newark",        "NJ", "foreclosure"),      # Socrata: lis pendens filings (data.newjersey.gov)
    ("indianapolis",  "IN", "foreclosure"),      # Socrata: sheriff sales + address (data.indy.gov)
    ("memphis",       "TN", "foreclosure"),      # Socrata: chancery court lis pendens (data.memphistn.gov)
    # ---- code violations ----
    ("chicago",       "IL", "code_violations"),  # Socrata: address only
    ("new york",      "NY", "code_violations"),  # Socrata: address only
    ("san francisco", "CA", "code_violations"),  # Socrata: address only
    ("buffalo",       "NY", "code_violations"),  # Socrata: address only
    ("kansas city",   "MO", "code_violations"),  # Socrata: address only
    ("milwaukee",     "WI", "code_violations"),  # Socrata: address + status (data.milwaukee.gov)
    ("cincinnati",    "OH", "code_violations"),  # Socrata: address + violation type (data.cincinnati-oh.gov)
    ("columbus",      "OH", "code_violations"),  # Socrata: address (opendata.columbus.gov)
    ("dallas",        "TX", "code_violations"),  # Socrata: address + case type (www.dallasopendata.com)
    ("houston",       "TX", "code_violations"),  # Socrata: address + complaint type (data.houstontx.gov)
    ("atlanta",       "GA", "code_violations"),  # Socrata: address + status (atlantaga.gov open data)
    # ---- vacant properties ----
    ("chicago",       "IL", "vacant"),           # Socrata: address only
    ("detroit",       "MI", "vacant"),           # Socrata: vacant + dangerous buildings list (data.detroitmi.gov)
    ("baltimore",     "MD", "vacant"),           # Socrata: Vacant Building Notices (data.baltimorecity.gov)
]


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:60]


def _cold_email(prospect: dict) -> tuple[str, str]:
    """Generate a personalized subject + body for a prospect. Pure-Python,
    no API key needed. Pulls in motivation-specific hooks and any deep
    signals the source happened to give us (e.g. dollars owed for Philly)."""
    address = prospect.get("address", "your property")
    city    = (prospect.get("city") or "").title()
    state   = prospect.get("state") or ""
    owner   = (prospect.get("owner_name") or "").title()
    salutation = owner.split()[0] if owner else "there"
    motivation = prospect.get("record_type", "")
    notes      = prospect.get("notes", "")

    # Motivation-specific hook (one short, specific line — not salesy)
    hooks = {
        "tax_delinquent": (
            f"Tax records show {address} has a balance still showing past-due."
            if not notes else
            f"Tax records show {address} has {notes.lower()}."
        ),
        "code_violations": (
            f"The city has an open code-violation case open against {address}."
        ),
        "foreclosure": (
            f"I came across a housing-court filing tied to {address}."
        ),
        "vacant": (
            f"{address} shows up on the city's vacant-property list."
        ),
        "probate": (
            f"I came across an estate filing that lists {address}."
        ),
    }
    hook = hooks.get(motivation,
        f"I came across {address} while searching public {city} records.")

    subject_map = {
        "tax_delinquent": f"Quick question about {address}",
        "code_violations": f"About the violation at {address}",
        "foreclosure": f"Re: {address}",
        "vacant": f"Is {address} still vacant?",
        "probate": f"About {address}",
    }
    subject = subject_map.get(motivation, f"Quick question about {address}")

    body = (
        f"Hi {salutation},\n\n"
        f"{hook}\n\n"
        f"My name is Ty — I'm a local real-estate investor here in {city}{', ' + state if state else ''}. "
        f"I buy houses as-is in any condition, close on your timeline, "
        f"and pay all closing costs (no agent fees, no inspections, no repairs needed).\n\n"
        f"If you'd consider a cash offer on {address}, would you be open "
        f"to a 5-minute call this week? If not, no problem — I won't reach out again.\n\n"
        f"Either way, you can reply YES / NO to this email and I'll respect it.\n\n"
        f"— Ty\n"
        f"Wholesale Omniverse\n"
        f"207-385-4041 / paypal.me/wholesaleomniverse\n"
    )
    return subject, body


def _save_draft(prospect: dict, subject: str, body: str) -> str:
    """Write the cold email to disk so the owner can review/send manually."""
    DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    slug = _slug(f"{prospect.get('city','')}-{prospect.get('address','unknown')}")
    path = DRAFTS_DIR / f"{datetime.now():%Y%m%d}-{slug}.txt"
    contact_block = (
        f"# To:      {prospect.get('email') or '(no email — skip-trace failed)'}\n"
        f"# Phone:   {prospect.get('phone') or '(no phone)'}\n"
        f"# Owner:   {prospect.get('owner_name') or '(unknown)'}\n"
        f"# Address: {prospect.get('address','')}\n"
        f"# Source:  {prospect.get('source','')}\n"
        f"# Type:    {prospect.get('record_type','')}\n"
        f"# Subject: {subject}\n"
        f"# ----\n"
    )
    path.write_text(contact_block + body)
    return str(path)


def acquire_cycle(max_per_cell: int = 5, auto_email: bool = False) -> dict:
    """Run the prospect grid. Returns aggregated stats."""
    all_prospects = []
    per_cell = []
    visited_pages = []

    for city, state, record_type in PROSPECT_GRID:
        err_text = ""
        try:
            result = core_tools.prospect_from_government_records(
                city=city,
                state=state,
                county=city,
                record_type=record_type,
                max_prospects=max_per_cell,
                auto_email=False,  # we send our own personalized version below
            )
        except Exception as e:
            err_text = str(e)[:120]
            per_cell.append({
                "city": city, "state": state, "record_type": record_type,
                "found": 0, "error": err_text,
            })
            record_cell(city, record_type, 0, error=err_text)
            continue
        found = result.get("prospects", [])
        per_cell.append({
            "city": city, "state": state, "record_type": record_type,
            "found": len(found),
            "with_email": sum(1 for p in found if p.get("email")),
            "with_phone": sum(1 for p in found if p.get("phone")),
        })
        visited_pages.extend(result.get("government_pages_visited", []))
        all_prospects.extend(found)

        # Health tracking — surfaces cells that have gone silent.
        record_cell(city, record_type, len(found))

        # Attribution — stamp lead_source=PropScout on the leads parent_tools
        # just saved, so deal_analyzer can credit this agent.
        tag_new_prospects(found, city, state, record_type)

    # Persist our own snapshot for the dashboard / future runs
    storage.save("ps_leads.json", all_prospects)

    # Generate a personalized cold-email draft per prospect WITH an email;
    # auto-send when the caller opted in. Drafts for prospects without an
    # email were misleading — the digest reported "N drafts written" but
    # none were actually deliverable. Now drafts_written matches the
    # sendable inventory exactly.
    drafts_written = 0
    sent = 0
    for p in all_prospects:
        if not p.get("email"):
            continue
        subject, body = _cold_email(p)
        _save_draft(p, subject, body)
        drafts_written += 1
        if auto_email:
            r = mailer.send(AGENT_KEY, p["email"], subject, body,
                            purpose="outreach")
            if r.get("status") == "sent":
                sent += 1

    return {
        "cells_run":       len(PROSPECT_GRID),
        "prospects_found": len(all_prospects),
        "with_email":      sum(1 for p in all_prospects if p.get("email")),
        "with_phone":      sum(1 for p in all_prospects if p.get("phone")),
        "drafts_written":  drafts_written,
        "outreach_sent":   sent,
        "per_cell":        per_cell,
        "pages_visited":   len(visited_pages),
    }


def _format_digest(stats: dict, sample: list) -> str:
    lines = [
        f"PropScout — {datetime.now():%Y-%m-%d %H:%M}",
        "",
        f"Cells scanned:    {stats['cells_run']}",
        f"Prospects found:  {stats['prospects_found']}",
        f"  with email:     {stats['with_email']}",
        f"  with phone:     {stats['with_phone']}",
        f"Cold-email drafts written: {stats['drafts_written']}  (data/ps_drafts/)",
        f"Auto-sent:        {stats['outreach_sent']}",
        "",
        "By city / motivation:",
    ]
    for c in stats["per_cell"]:
        if c.get("error"):
            lines.append(
                f"  {c['city']}, {c['state']:>2}  {c['record_type']:<16}  ERROR — {c['error']}"
            )
        else:
            lines.append(
                f"  {c['city']:>13}, {c['state']:>2}  {c['record_type']:<16}  "
                f"{c['found']:>3} found  ({c.get('with_email',0)} email, "
                f"{c.get('with_phone',0)} phone)"
            )
    if sample:
        lines.append("")
        lines.append("Sample prospects:")
        for p in sample[:10]:
            who = p.get("owner_name") or "(unknown owner)"
            phone = p.get("phone") or "—"
            notes = f"  [{p.get('notes')}]" if p.get("notes") else ""
            lines.append(
                f"  • {who:>22}  {p.get('address','')}  ({p.get('city','')}, "
                f"{p.get('state','')})  phone={phone}{notes}"
            )
    return "\n".join(lines)


def fulfill_cycle(stats: dict) -> dict:
    """Email the owner a daily digest of the new prospects."""
    owner_email = os.environ.get("PS_OWNER_EMAIL",
                                  os.environ.get("SMTP_USER", ""))
    if not owner_email:
        return {"digest_sent": 0}
    prospects = storage.load("ps_leads.json", [])
    digest = _format_digest(stats, prospects[:15])
    r = mailer.send(AGENT_KEY, owner_email,
                    f"PropScout daily — {stats['prospects_found']} prospects ({datetime.now():%b %d})",
                    digest, purpose="fulfillment")
    return {"digest_sent": 1 if r.get("status") == "sent" else 0}


def run_full_cycle() -> dict:
    auto_email = os.environ.get("PS_AUTO_EMAIL", "0") == "1"
    max_per_cell = int(os.environ.get("PS_MAX_PER_CELL", "5"))
    stats = acquire_cycle(max_per_cell=max_per_cell, auto_email=auto_email)
    digest = fulfill_cycle(stats)
    rev = billing.revenue_summary(AGENT_KEY)
    subs = storage.load("ps_subscribers.json", [])
    metrics.record(
        AGENT_KEY,
        prospects_added=stats["prospects_found"],
        outreach_sent=stats["outreach_sent"],
        active_subs=sum(1 for s in subs if s.get("status") == "active"),
        mrr=rev["mrr"],
        total_revenue=rev["total_paid"],
    )
    return {**stats, **digest, **rev}
