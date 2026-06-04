"""
TrendScout — paid weekly digital-product-niche newsletter.
Revenue: $29/mo basic, $79/mo pro, $497/yr.
"""
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from autonomous import storage, mailer, billing, metrics
from trendscout import health

AGENT_KEY = "trendscout"
INPUT_DIR = Path(__file__).parent.parent / "data" / "ts_inputs"
REPORTS_DIR = Path(__file__).parent.parent / "data" / "ts_reports"

BLOCKED_NICHES = {
    "crypto", "nft", "weight loss pill", "supplement", "trump", "biden", "vaccine",
    "marvel", "disney", "harry potter", "pokemon", "lebron", "taylor swift",
    "gambling", "casino", "kratom", "delta-8",
}

STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "of", "in", "on", "at", "to", "for",
    "with", "from", "by", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "should", "could",
    "i", "you", "he", "she", "it", "we", "they", "me", "him", "her", "us", "them",
    "my", "your", "his", "its", "our", "their", "this", "that", "these", "those",
    "what", "which", "who", "whom", "whose", "when", "where", "why", "how",
    "all", "each", "every", "some", "any", "no", "not", "only", "own", "same",
    "so", "than", "too", "very", "can", "just", "as", "if", "then", "out",
    "up", "down", "into", "over", "under", "again", "further", "more", "most",
    "other", "such", "nor", "also", "about", "people", "going", "really", "like",
    "get", "got", "make", "made", "see", "know", "think", "want", "need", "say",
    "said", "one", "two", "three", "new", "good", "bad", "great", "best",
}


def parse_input(path: Path) -> list:
    """Pull noun phrases out of an owner-supplied snapshot."""
    text = path.read_text(errors="ignore")
    try:
        from bs4 import BeautifulSoup
        if "<html" in text.lower() or "<body" in text.lower():
            text = BeautifulSoup(text, "html.parser").get_text(" ")
    except ImportError:
        pass
    text = re.sub(r"https?://\S+", " ", text)
    # bigrams that look like niches
    words = [w.lower() for w in re.findall(r"[A-Za-z][A-Za-z']+", text)
             if w.lower() not in STOPWORDS and len(w) > 2]
    bigrams = [" ".join(words[i:i+2]) for i in range(len(words) - 1)]
    return bigrams


def is_safe(niche: str) -> bool:
    return not any(b in niche for b in BLOCKED_NICHES)


def scan_signals() -> Counter:
    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    source_counters = []
    for src in INPUT_DIR.glob("*"):
        if src.suffix.lower() in (".html", ".txt", ".md", ".csv"):
            bigrams = parse_input(src)
            cnt = Counter(b for b in bigrams if is_safe(b))
            source_counters.append(cnt)

    # Cross-source: only keep niches appearing in ≥2 sources
    if len(source_counters) < 2:
        if source_counters:
            return source_counters[0]
        return Counter()
    candidates = Counter()
    all_keys = set()
    for c in source_counters:
        all_keys.update(c.keys())
    for k in all_keys:
        sources = sum(1 for c in source_counters if c.get(k, 0) >= 2)
        if sources >= 2:
            candidates[k] = sum(c.get(k, 0) for c in source_counters)
    return candidates


def score_niches(counter: Counter, top_n: int = 10) -> list:
    scored = []
    for niche, count in counter.most_common(50):
        demand = min(10, count)
        competition = 5  # placeholder; owner can override
        difficulty = 4
        clarity = 7 if any(w in niche for w in ("template", "guide", "planner", "tracker", "kit", "printable")) else 5
        score = demand * 2 + clarity * 1.5 - competition - difficulty
        scored.append({
            "niche": niche, "score": round(score, 1),
            "demand": demand, "competition": competition,
            "difficulty": difficulty, "clarity": clarity,
            "raw_count": count,
        })
    scored.sort(key=lambda x: -x["score"])
    return scored[:top_n]


def build_report(week: str) -> dict:
    """Returns {"path": Path|None, "sources": N, "raw_signals": N,
                "scored": N, "top": [...]} so fulfill_cycle can persist
    yield metadata even when the report is skipped for low signal."""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    sources = sum(1 for src in INPUT_DIR.glob("*")
                  if src.is_file() and src.suffix.lower() in (".html", ".txt", ".md", ".csv"))
    signals = scan_signals()
    top = score_niches(signals)
    raw = sum(signals.values())
    if len(top) < 3:
        return {"path": None, "sources": sources, "raw_signals": raw,
                "scored": len(top), "top": top}
    lines = [f"# TrendScout — Week of {week}\n",
             f"_5 high-signal niches identified from {raw} signals._\n"]
    for i, n in enumerate(top[:5], 1):
        lines.append(f"\n## {i}. {n['niche'].title()}")
        lines.append(f"- **Score:** {n['score']}")
        lines.append(f"- **Demand signal:** {n['demand']}/10")
        lines.append(f"- **Why now:** mentioned across multiple owner-supplied sources this week")
        lines.append(f"- **Suggested format:** printable, template, or short guide")
        lines.append(f"- **Target buyer:** people searching '{n['niche']}' on Etsy/Pinterest")
    report_path = REPORTS_DIR / f"{week}.md"
    report_path.write_text("\n".join(lines))
    return {"path": report_path, "sources": sources, "raw_signals": raw,
            "scored": len(top), "top": top}


def acquire_cycle() -> dict:
    """Send free teaser (top 1 niche revealed) to all leads."""
    signals = scan_signals()
    top = score_niches(signals)
    leads = storage.load("ts_leads.json", [])
    sent = 0
    if not top:
        return {"outreach_sent": 0}
    teaser_niche = top[0]
    for lead in leads:
        if lead.get("teaser_sent"):
            continue
        body = (
            f"Hey — TrendScout free teaser.\n\n"
            f"This week's #1 untapped niche: **{teaser_niche['niche'].title()}**.\n"
            f"Score: {teaser_niche['score']}/20 — multi-source demand signals.\n\n"
            f"The other 4 niches (with target buyers + product formats) are in the paid digest.\n\n"
            f"Subscribe: $29/mo — paypal.me/wholesaleomniverse/29\n"
            f"Reply with your email after paying and we'll add you to Friday's drop."
        )
        result = mailer.send(AGENT_KEY, lead["email"],
                             f"Free trend: {teaser_niche['niche'].title()}",
                             body, purpose="outreach")
        if result.get("status") == "sent":
            lead["teaser_sent"] = datetime.now().isoformat()
            sent += 1
    storage.save("ts_leads.json", leads)
    return {"outreach_sent": sent}


def fulfill_cycle() -> dict:
    week = datetime.now().strftime("%Y-W%W")
    meta = build_report(week)
    top_score = meta["top"][0]["score"] if meta["top"] else 0
    top_niche = meta["top"][0]["niche"] if meta["top"] else ""
    if not meta["path"]:
        health.record_week(week, meta["sources"], meta["raw_signals"],
                           meta["scored"], top_score, top_niche, sent=0,
                           skipped=True, skip_reason="low_signal")
        return {"fulfillment_sent": 0, "reason": "low_signal"}
    body_md = meta["path"].read_text()
    subs = storage.load("ts_subscribers.json", [])
    sent = 0
    failed = 0
    for s in subs:
        if s.get("status") != "active":
            continue
        result = mailer.send(AGENT_KEY, s["email"],
                             f"TrendScout — 5 niches for {week}",
                             body_md, purpose="fulfillment")
        if result.get("status") == "sent":
            sent += 1
        else:
            failed += 1
    health.record_week(week, meta["sources"], meta["raw_signals"],
                       meta["scored"], top_score, top_niche, sent=sent, skipped=False)
    return {"fulfillment_sent": sent, "fulfillment_failed": failed}


def run_full_cycle() -> dict:
    a = acquire_cycle()
    f = fulfill_cycle()
    rev = billing.revenue_summary(AGENT_KEY)
    subs = storage.load("ts_subscribers.json", [])
    metrics.record(
        AGENT_KEY,
        outreach_sent=a.get("outreach_sent", 0),
        fulfillment_sent=f.get("fulfillment_sent", 0),
        fulfillment_failed=f.get("fulfillment_failed", 0),
        active_subs=sum(1 for s in subs if s.get("status") == "active"),
        mrr=rev["mrr"],
        total_revenue=rev["total_paid"],
    )
    return {**a, **f, **rev}
