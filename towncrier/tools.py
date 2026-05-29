"""
TownCrier — hyper-local event newsletter.
Revenue: $50–$200/slot sponsor placements + $25 featured events.
"""
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from autonomous import storage, mailer, billing, metrics

AGENT_KEY = "towncrier"
SNAPSHOT_DIR = Path(__file__).parent.parent / "data" / "tc_snapshots"
DIGEST_DIR = Path(__file__).parent.parent / "data" / "tc_digests"

CATEGORIES = {
    "Family": ["family", "kids", "children", "child"],
    "Music":  ["concert", "music", "band", "dj", "live music"],
    "Food":   ["food", "wine", "tasting", "festival", "dinner", "brunch"],
    "Outdoors": ["park", "hike", "trail", "outdoor", "garden"],
    "Civic":  ["meeting", "council", "town hall", "vote", "community"],
    "Free":   ["free", "no cost", "$0"],
}


def parse_event_snapshot(html_path: Path, source_name: str = "") -> list:
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return []
    soup = BeautifulSoup(html_path.read_text(errors="ignore"), "html.parser")
    events = []
    for el in soup.find_all(["div", "li", "article"]):
        text = el.get_text(" ", strip=True)
        if 30 < len(text) < 600:
            date_match = re.search(
                r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{1,2}",
                text.lower())
            time_match = re.search(r"(\d{1,2}(?::\d{2})?\s*(?:am|pm))", text.lower())
            if date_match:
                events.append({
                    "title": text.split(date_match.group(0))[0][:120].strip() or "Untitled",
                    "raw":   text[:400],
                    "date":  date_match.group(0).title(),
                    "time":  time_match.group(1) if time_match else "",
                    "source": source_name or html_path.stem,
                })
    # dedupe
    seen = set()
    uniq = []
    for e in events:
        k = (e["title"][:40].lower(), e["date"])
        if k not in seen:
            seen.add(k)
            uniq.append(e)
    return uniq[:40]


def categorize(event: dict) -> list:
    text = (event.get("title", "") + " " + event.get("raw", "")).lower()
    cats = []
    for cat, kws in CATEGORIES.items():
        if any(kw in text for kw in kws):
            cats.append(cat)
    return cats or ["General"]


def collect_events(city: str) -> list:
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    events = []
    for snap in SNAPSHOT_DIR.glob(f"{city}_*.html"):
        events.extend(parse_event_snapshot(snap, source_name=snap.stem))
    for e in events:
        e["categories"] = categorize(e)
    return events


def build_digest(city: str) -> dict:
    events = collect_events(city)
    if len(events) < 5:
        return {"sent": 0, "skipped": True, "reason": "insufficient_events", "count": len(events)}

    # Pull queued sponsors
    sponsors = storage.load("tc_sponsors.json", [])
    queued = [s for s in sponsors if s.get("status") == "paid" and s.get("sends_remaining", 0) > 0]

    DIGEST_DIR.mkdir(parents=True, exist_ok=True)
    digest_md = [f"# This Week in {city.replace('-', ' ').title()}\n",
                 f"_{datetime.now():%B %d, %Y}_\n"]
    if queued:
        s = queued[0]
        digest_md.append(f"\n**Sponsored by:** {s['name']} — {s.get('tagline', '')}\n")

    # Group by category
    by_cat = {}
    for e in events:
        for c in e.get("categories", ["General"]):
            by_cat.setdefault(c, []).append(e)

    for cat, items in sorted(by_cat.items()):
        digest_md.append(f"\n## {cat}\n")
        for e in items[:6]:
            digest_md.append(f"- **{e['title']}** — {e['date']} {e.get('time', '')}")

    body_md = "\n".join(digest_md)
    digest_file = DIGEST_DIR / f"{city}_{datetime.now():%Y%m%d}.md"
    digest_file.write_text(body_md)

    # Send to subscribers
    subs = storage.load("tc_subscribers.json", [])
    active_subs = [s for s in subs if s.get("status", "active") == "active"
                   and s.get("city", "").lower() == city.lower()]
    sent = 0
    failed = 0
    for s in active_subs:
        result = mailer.send(
            AGENT_KEY, s["email"],
            f"This Week in {city.title()} — {datetime.now():%b %d}",
            body_md, purpose="fulfillment",
        )
        if result.get("status") == "sent":
            sent += 1
        else:
            failed += 1

    # Decrement sponsor sends
    if queued:
        for s in sponsors:
            if s.get("invoice_id") == queued[0].get("invoice_id"):
                s["sends_remaining"] = s.get("sends_remaining", 0) - 1
        storage.save("tc_sponsors.json", sponsors)

    return {"sent": sent, "failed": failed, "events": len(events), "sponsors_used": 1 if queued else 0}


def pitch_sponsors(city: str) -> dict:
    """Identify venues mentioned in events; draft sponsor pitches."""
    events = collect_events(city)
    venue_mentions = {}
    for e in events:
        m = re.search(r"at\s+([A-Z][A-Za-z0-9' &]+)", e.get("raw", ""))
        if m:
            venue = m.group(1).strip()
            venue_mentions[venue] = venue_mentions.get(venue, 0) + 1

    pitches = storage.load("tc_sponsor_pitches.json", [])
    contacted = {p["venue"] for p in pitches}

    new = 0
    for venue, count in venue_mentions.items():
        if venue in contacted or count < 2:
            continue
        pitches.append({
            "venue": venue,
            "city": city,
            "mentions": count,
            "discovered_at": datetime.now().isoformat(),
            "contact_email": "",
            "status": "queued",
        })
        new += 1
    storage.save("tc_sponsor_pitches.json", pitches)

    # Send pitches to any pitch with contact_email
    sent = 0
    for p in pitches:
        if p.get("status") == "queued" and p.get("contact_email"):
            body = (
                f"Hi — TownCrier publishes a weekly newsletter for {city.title()}. "
                f"Your venue, {p['venue']}, was mentioned in {p['mentions']} events this week.\n\n"
                f"Sponsor slots available:\n"
                f"- Single send: $50\n"
                f"- 4-week run: $200 (best value)\n"
                f"- Featured event placement: $25\n\n"
                f"Reply YES + which tier and I'll send the PayPal invoice. Slots fill weekly."
            )
            result = mailer.send(AGENT_KEY, p["contact_email"],
                                 f"Sponsor slot in {city.title()} weekly newsletter",
                                 body, purpose="outreach")
            if result.get("status") == "sent":
                p["status"] = "contacted"
                sent += 1

    storage.save("tc_sponsor_pitches.json", pitches)
    return {"new_pitches": new, "outreach_sent": sent}


def run_full_cycle(city: str = "") -> dict:
    subs = storage.load("tc_subscribers.json", [])
    cities = {s.get("city", "").lower() for s in subs if s.get("city")} or {city or "default"}
    total = {"sent": 0, "failed": 0, "events": 0, "new_pitches": 0, "outreach_sent": 0, "sponsors_used": 0}
    for c in cities:
        if not c:
            continue
        d = build_digest(c)
        p = pitch_sponsors(c)
        for k in ("sent", "failed", "events", "sponsors_used"):
            total[k] += d.get(k, 0) if not d.get("skipped") else 0
        for k in ("new_pitches", "outreach_sent"):
            total[k] += p.get(k, 0)

    rev = billing.revenue_summary(AGENT_KEY)
    metrics.record(
        AGENT_KEY,
        fulfillment_sent=total["sent"],
        fulfillment_failed=total["failed"],
        outreach_sent=total["outreach_sent"],
        prospects_added=total["new_pitches"],
        active_subs=sum(1 for s in subs if s.get("status", "active") == "active"),
        mrr=rev["mrr"],
        total_revenue=rev["total_paid"],
    )
    return {**total, **rev}
