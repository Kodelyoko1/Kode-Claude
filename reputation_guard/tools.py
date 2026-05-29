"""
ReputationGuard — autonomous review-management agent.
Persona: senior reputation consultant; warm, accountable, never defensive.

Revenue model:
  $79/mo per business location (recurring)
  $497 one-time deep audit

Flow:
  1. Owner drops Google/Yelp HTML snapshots into data/rg_snapshots/{biz_slug}.html
  2. Acquisition: identify businesses with ≥3 negative reviews → cold outreach with free sample
  3. Fulfillment: for active clients, draft replies weekly → email digest
"""
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from autonomous import storage, mailer, billing, metrics

AGENT_KEY = "reputation_guard"
SNAPSHOT_DIR = Path(__file__).parent.parent / "data" / "rg_snapshots"
REPLIES_DIR = Path(__file__).parent.parent / "data" / "rg_replies"

NEGATIVE_LEXICON = {
    "terrible", "awful", "horrible", "worst", "rude", "scam", "ripped off",
    "never again", "disappointed", "disgusting", "filthy", "dirty",
    "overpriced", "waste of money", "unprofessional", "incompetent",
    "broken", "ignored", "lied", "fraud", "stay away", "do not recommend",
}

REPLY_TEMPLATE = """Hi {reviewer},

Thank you for taking the time to share this — and I'm sorry your experience didn't meet what you should have gotten from us.

{specific_acknowledgment}

I'd like to make this right. Please reach out to me directly at {contact_email} or {contact_phone} and reference this review — I'll personally look into what went wrong and follow up with you.

— {owner_name}, {business_name}"""


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def parse_snapshot(html_path: Path) -> list:
    """Extract reviews from an owner-supplied HTML snapshot."""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return []
    if not html_path.exists():
        return []
    soup = BeautifulSoup(html_path.read_text(errors="ignore"), "html.parser")
    reviews = []
    # Generic extraction: look for anything that smells like a review block
    candidates = soup.find_all(["div", "article", "li"])
    for el in candidates:
        text = el.get_text(" ", strip=True)
        if 40 < len(text) < 2000:
            # rating heuristic
            stars = 5
            m = re.search(r"(\d)(?:\.\d)?\s*(?:star|out of 5|/5)", text.lower())
            if m:
                stars = int(m.group(1))
            name = "Customer"
            name_match = re.search(r"by\s+([A-Z][a-z]+\s?[A-Z]?[a-z]*)", text)
            if name_match:
                name = name_match.group(1)
            reviews.append({"text": text[:1500], "stars": stars, "reviewer": name})
    # Dedupe by text prefix
    seen = set()
    unique = []
    for r in reviews:
        key = r["text"][:80]
        if key not in seen:
            seen.add(key)
            unique.append(r)
    return unique[:50]


def is_negative(review: dict) -> bool:
    if review.get("stars", 5) <= 3:
        return True
    text = review.get("text", "").lower()
    return any(w in text for w in NEGATIVE_LEXICON)


def draft_reply(review: dict, business_name: str, owner_name: str = "the team",
                contact_email: str = "", contact_phone: str = "") -> str:
    text = review.get("text", "").lower()
    if "rude" in text or "unprofessional" in text:
        ack = "How our team interacted with you isn't who we are or who we want to be — that's on us to address internally, and we will."
    elif "dirty" in text or "filthy" in text or "broken" in text:
        ack = "Cleanliness and condition are non-negotiable for us, and we clearly missed the mark on your visit."
    elif "overpriced" in text or "waste" in text:
        ack = "Value matters, and if you walked away feeling you didn't get your money's worth, I want to understand why and what we can do."
    elif "ignored" in text or "wait" in text:
        ack = "Making you wait without communication isn't the experience we promise, and I take responsibility for that."
    else:
        ack = "What you described isn't the standard we hold ourselves to, and I want to learn exactly what happened."
    return REPLY_TEMPLATE.format(
        reviewer=review.get("reviewer", "there"),
        specific_acknowledgment=ack,
        contact_email=contact_email or "the business directly",
        contact_phone=contact_phone or "by phone",
        owner_name=owner_name,
        business_name=business_name,
    )


def scan_prospects() -> list:
    """Find businesses with ≥3 negative reviews — these are prospects."""
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    prospects = []
    for snap in SNAPSHOT_DIR.glob("*.html"):
        biz_slug = snap.stem
        reviews = parse_snapshot(snap)
        negs = [r for r in reviews if is_negative(r)]
        if len(negs) >= 3:
            prospects.append({
                "business_slug": biz_slug,
                "business_name": biz_slug.replace("-", " ").title(),
                "negative_count": len(negs),
                "total_reviews": len(reviews),
                "sample_reviews": negs[:3],
            })
    return prospects


def acquire_cycle() -> dict:
    """Find prospects and draft outreach to owners (manual sender list maintained in JSON)."""
    prospects = scan_prospects()
    targets = storage.load("rg_prospects.json", [])
    contacted = {p.get("business_slug") for p in targets if p.get("contacted_at")}

    sent = 0
    new_prospects = 0
    for p in prospects:
        if p["business_slug"] in contacted:
            continue
        record = {
            **p,
            "contact_email": "",  # owner fills in after research
            "status": "queued",
            "discovered_at": datetime.now().isoformat(),
            "contacted_at": "",
        }
        targets.append(record)
        new_prospects += 1

    # Send outreach to any prospect that now has an email
    for r in targets:
        if r.get("status") == "queued" and r.get("contact_email"):
            sample_replies = [
                draft_reply(rv, r["business_name"])
                for rv in r.get("sample_reviews", [])
            ]
            body = (
                f"Hi — I'm a reputation management consultant. I noticed {r['business_name']} "
                f"has {r['negative_count']} negative reviews recently.\n\n"
                f"I drafted 3 free reply templates for you — see attached note. "
                f"If you'd like the full monthly service ($79/mo, all replies drafted weekly), "
                f"reply YES and I'll send the PayPal link.\n\n"
                f"— FREE SAMPLE REPLIES —\n\n"
                + "\n\n---\n\n".join(sample_replies)
            )
            result = mailer.send(
                AGENT_KEY,
                r["contact_email"],
                f"3 reply drafts for the recent reviews of {r['business_name']}",
                body,
                purpose="outreach",
            )
            if result.get("status") == "sent":
                r["status"] = "contacted"
                r["contacted_at"] = datetime.now().isoformat()
                sent += 1

    storage.save("rg_prospects.json", targets)
    return {"new_prospects": new_prospects, "outreach_sent": sent, "prospect_pool": len(targets)}


def fulfill_cycle() -> dict:
    """For each active client, draft new replies from latest snapshot and email digest."""
    REPLIES_DIR.mkdir(parents=True, exist_ok=True)
    clients = storage.load("rg_clients.json", [])
    sent = 0
    failed = 0
    for c in clients:
        if c.get("status") != "active":
            continue
        snap = SNAPSHOT_DIR / f"{c['business_slug']}.html"
        if not snap.exists():
            continue
        reviews = parse_snapshot(snap)
        negs = [r for r in reviews if is_negative(r)]
        if not negs:
            continue
        drafts = [
            {
                "review": rv,
                "draft":  draft_reply(
                    rv,
                    c["business_name"],
                    owner_name=c.get("owner_name", "the team"),
                    contact_email=c.get("contact_email", ""),
                    contact_phone=c.get("contact_phone", ""),
                ),
            }
            for rv in negs[:7]
        ]
        out_file = REPLIES_DIR / f"{c['business_slug']}_{datetime.now():%Y%m%d}.txt"
        with open(out_file, "w") as f:
            for d in drafts:
                f.write(f"REVIEW ({d['review']['stars']}★): {d['review']['text']}\n\n")
                f.write(f"DRAFT REPLY:\n{d['draft']}\n\n{'='*60}\n\n")

        body = (
            f"Hi {c.get('owner_name', 'there')},\n\n"
            f"Weekly reputation report for {c['business_name']}.\n\n"
            f"  • {len(negs)} new negative reviews detected\n"
            f"  • {len(drafts)} reply drafts ready for your approval\n\n"
            f"Drafts attached. Reply with any edits and I'll send revised versions.\n\n"
            f"— Reputation Team, Wholesale Omniverse LLC"
        )
        result = mailer.send(
            AGENT_KEY, c.get("contact_email", ""),
            f"Weekly reply drafts — {c['business_name']}",
            body, purpose="fulfillment",
            attachments=[str(out_file)],
        )
        if result.get("status") == "sent":
            sent += 1
        else:
            failed += 1
    return {"fulfillment_sent": sent, "fulfillment_failed": failed}


def run_full_cycle() -> dict:
    a = acquire_cycle()
    f = fulfill_cycle()
    rev = billing.revenue_summary(AGENT_KEY)
    clients = storage.load("rg_clients.json", [])
    metrics.record(
        AGENT_KEY,
        prospects_added=a["new_prospects"],
        outreach_sent=a["outreach_sent"],
        fulfillment_sent=f["fulfillment_sent"],
        fulfillment_failed=f["fulfillment_failed"],
        active_subs=sum(1 for c in clients if c.get("status") == "active"),
        mrr=rev["mrr"],
        total_revenue=rev["total_paid"],
    )
    return {**a, **f, **rev}
