"""
Reddit listener — read-only opportunity finder.

Scans target subreddits for distress signals (motivated sellers), cash buyer
recruitment cues, and SAAS/OAS prospect questions. Saves matches to
data/reddit_leads.json. Does NOT post anything — fully compliant with
Reddit's Responsible Builder Policy.
"""
import os
import re
import json
import datetime
from pathlib import Path

DATA_DIR  = Path(__file__).parent.parent.parent / "data"
LEADS_FILE = DATA_DIR / "reddit_leads.json"

# Subreddits to monitor, grouped by what's likely to surface there
SUBREDDITS = [
    "realestateinvesting",
    "RealEstate",
    "wholesale",
    "RealEstateAdvice",
    "FirstTimeHomeBuyer",
    "RealEstateTechnology",
    "personalfinance",
]

# Keyword sets — each match adds to the score. Phrases are matched case-insensitively.
KEYWORDS = {
    "sellers": [
        r"\bsell\s+my\s+house\b",
        r"\bneed\s+to\s+sell\b",
        r"\bsell.{0,15}fast\b",
        r"\bsell.{0,15}quickly\b",
        r"\binherited.{0,20}(house|property|home)\b",
        r"\bbehind\s+on\s+(my\s+)?(mortgage|payments)\b",
        r"\bforeclosure\b",
        r"\bunderwater\s+on.{0,15}mortgage\b",
        r"\btax\s+delinquent\b",
        r"\bprobate\b",
        r"\bhouse\s+needs\s+work\b",
        r"\bcan'?t\s+afford\s+(my|the)\s+(house|home|mortgage)\b",
        r"\bdivorce.{0,15}(house|home|property)\b",
        r"\brelocat.{0,15}(house|home)\b",
        r"\bmoving\s+out\s+of\s+state\b",
    ],
    "buyers": [
        r"\blooking\s+for\s+cash\s+buyers\b",
        r"\bactive\s+cash\s+buyer\b",
        r"\bactively\s+buying\b",
        r"\bbuy\s+box\b",
        r"\boff[-\s]?market\s+deals?\b",
        r"\bfix.?and.?flip\b",
        r"\bbrrr\b",
        r"\blooking\s+to\s+buy\s+(rentals|investment)\b",
        r"\badd\s+me\s+to.{0,15}buyers?\s+list\b",
    ],
    "wholesalers": [
        r"\bdeal\s+analyz(er|ing|e)\b",
        r"\bhow\s+do\s+(you|I)\s+analyze\b",
        r"\b(comps?|comp(?:arable)?s?)\s+tool\b",
        r"\bARV\s+calculator\b",
        r"\brunning\s+comps?\b",
        r"\bbest\s+CRM\s+for\s+wholesal\b",
        r"\bmotivated\s+seller\s+lists?\b",
        r"\bskip\s+trac(ing|e)\b",
        r"\blead\s+(source|gen)\s+(for|wholesal)\b",
        r"\bcold\s+calling\s+(software|tool)\b",
        r"\bdirect\s+mail.{0,15}wholesal\b",
        r"\bwholesale.{0,30}(software|tool|automate)\b",
        r"\bjust\s+starting\s+wholesal\b",
        r"\bnew\s+to\s+wholesal\b",
    ],
}

# Cities/markets we care about — boosts score if mentioned
TARGET_MARKETS = [
    "detroit", "memphis", "atlanta", "cleveland", "chicago",
    "birmingham", "jacksonville", "tampa", "charlotte", "nashville",
    "kansas city", "milwaukee", "indianapolis", "philadelphia",
    "baltimore", "new orleans", "norfolk", "richmond",
]


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


def _get_client():
    import praw
    return praw.Reddit(
        client_id     = os.environ["REDDIT_CLIENT_ID"],
        client_secret = os.environ["REDDIT_CLIENT_SECRET"],
        username      = os.environ.get("REDDIT_USERNAME", ""),
        password      = os.environ.get("REDDIT_PASSWORD", ""),
        user_agent    = "wholesaleomniverse-listener/1.0",
    )


def _score_submission(title: str, body: str) -> dict:
    text = f"{title}\n{body}".lower()
    matched = {"sellers": [], "buyers": [], "wholesalers": []}
    for audience, patterns in KEYWORDS.items():
        for pat in patterns:
            if re.search(pat, text, re.IGNORECASE):
                matched[audience].append(pat)
    markets_hit = [m for m in TARGET_MARKETS if m in text]

    score = (
        len(matched["sellers"])     * 3 +   # sellers = highest value
        len(matched["wholesalers"]) * 2 +
        len(matched["buyers"])      * 2 +
        len(markets_hit)            * 2
    )

    audience = ""
    if matched["sellers"]:
        audience = "sellers"
    elif matched["wholesalers"]:
        audience = "wholesalers"
    elif matched["buyers"]:
        audience = "buyers"

    return {
        "score": score,
        "audience": audience,
        "matched_keywords": [k for lst in matched.values() for k in lst],
        "markets_hit": markets_hit,
    }


def _save_lead(submission, scoring: dict) -> str:
    leads = _load(LEADS_FILE, {})
    lead_id = f"RDT-{submission.id}"
    if lead_id in leads:
        return lead_id  # already seen
    leads[lead_id] = {
        "lead_id": lead_id,
        "submission_id": submission.id,
        "subreddit": str(submission.subreddit),
        "title": submission.title[:300],
        "body_snippet": (submission.selftext or "")[:500],
        "author": str(submission.author) if submission.author else "[deleted]",
        "url": f"https://reddit.com{submission.permalink}",
        "audience": scoring["audience"],
        "score": scoring["score"],
        "matched_keywords": scoring["matched_keywords"],
        "markets_hit": scoring["markets_hit"],
        "found_at": _now(),
        "engaged": False,
        "engaged_at": "",
        "notes": "",
    }
    _save(LEADS_FILE, leads)
    return lead_id


def scan_once(subreddits: list = None, limit: int = 50, min_score: int = 3) -> dict:
    """One-shot scan of `new` submissions in each subreddit."""
    subreddits = subreddits or SUBREDDITS
    client = _get_client()
    new_leads = []
    seen = 0

    for sub_name in subreddits:
        try:
            for submission in client.subreddit(sub_name).new(limit=limit):
                seen += 1
                title = submission.title or ""
                body  = submission.selftext or ""
                scoring = _score_submission(title, body)
                if scoring["score"] < min_score:
                    continue
                lead_id = _save_lead(submission, scoring)
                leads = _load(LEADS_FILE, {})
                if leads[lead_id]["found_at"] >= _now()[:10]:  # found today
                    new_leads.append({
                        "lead_id": lead_id,
                        "subreddit": sub_name,
                        "title": title[:120],
                        "score": scoring["score"],
                        "audience": scoring["audience"],
                        "url": f"https://reddit.com{submission.permalink}",
                    })
        except Exception as e:
            new_leads.append({"error": f"{sub_name}: {e}"})

    return {
        "subreddits_scanned": len(subreddits),
        "submissions_seen": seen,
        "new_leads": len(new_leads),
        "leads": new_leads,
    }


def stream(subreddits: list = None, min_score: int = 3):
    """
    Live tail — yields each high-scoring submission as it lands.
    Caller can decide what to do with each (print, email, etc.).
    """
    subreddits = subreddits or SUBREDDITS
    client = _get_client()
    multi = "+".join(subreddits)
    for submission in client.subreddit(multi).stream.submissions(skip_existing=True):
        title = submission.title or ""
        body  = submission.selftext or ""
        scoring = _score_submission(title, body)
        if scoring["score"] < min_score:
            continue
        lead_id = _save_lead(submission, scoring)
        yield {
            "lead_id": lead_id,
            "subreddit": str(submission.subreddit),
            "title": title[:120],
            "score": scoring["score"],
            "audience": scoring["audience"],
            "url": f"https://reddit.com{submission.permalink}",
            "body_snippet": body[:300],
        }


def recent_leads(hours: int = 24, audience: str = "", engaged: bool = None) -> list:
    """Return leads from the last N hours, optionally filtered."""
    leads = _load(LEADS_FILE, {})
    cutoff = datetime.datetime.now() - datetime.timedelta(hours=hours)
    out = []
    for lead in leads.values():
        try:
            found = datetime.datetime.fromisoformat(lead["found_at"])
        except Exception:
            continue
        if found < cutoff:
            continue
        if audience and lead.get("audience") != audience:
            continue
        if engaged is not None and bool(lead.get("engaged")) != engaged:
            continue
        out.append(lead)
    out.sort(key=lambda x: x.get("score", 0), reverse=True)
    return out


def mark_engaged(lead_id: str, notes: str = "") -> dict:
    leads = _load(LEADS_FILE, {})
    if lead_id not in leads:
        return {"error": f"Lead {lead_id} not found"}
    leads[lead_id]["engaged"] = True
    leads[lead_id]["engaged_at"] = _now()
    if notes:
        leads[lead_id]["notes"] = notes
    _save(LEADS_FILE, leads)
    return {"status": "marked_engaged", "lead_id": lead_id}


def summary() -> dict:
    leads = _load(LEADS_FILE, {})
    return {
        "total_leads": len(leads),
        "by_audience": {
            "sellers":     sum(1 for l in leads.values() if l.get("audience") == "sellers"),
            "buyers":      sum(1 for l in leads.values() if l.get("audience") == "buyers"),
            "wholesalers": sum(1 for l in leads.values() if l.get("audience") == "wholesalers"),
        },
        "engaged": sum(1 for l in leads.values() if l.get("engaged")),
        "open":    sum(1 for l in leads.values() if not l.get("engaged")),
    }
