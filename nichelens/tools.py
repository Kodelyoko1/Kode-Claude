"""
NicheLens — paid hyper-niche curation newsletters.
Revenue: $7/mo per niche, $59/yr, affiliate injection.
"""
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from autonomous import storage, mailer, billing, metrics

AGENT_KEY = "nichelens"
SNAP_DIR = Path(__file__).parent.parent / "data" / "nl_snapshots"
NEWSLETTER_DIR = Path(__file__).parent.parent / "data" / "nl_newsletters"


def parse_items(html_path: Path) -> list:
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return []
    soup = BeautifulSoup(html_path.read_text(errors="ignore"), "html.parser")
    items = []
    for el in soup.find_all(["article", "div", "li"]):
        title_el = el.find(["h1", "h2", "h3", "h4"])
        if not title_el:
            continue
        title = title_el.get_text(strip=True)[:200]
        body = el.get_text(" ", strip=True)[:600]
        link = ""
        a = el.find("a", href=True)
        if a:
            link = a["href"]
        if title and len(body) > 60:
            items.append({"title": title, "summary": body, "url": link,
                          "source": html_path.stem})
    seen = set()
    unique = []
    for i in items:
        k = i["title"][:60].lower()
        if k in seen:
            continue
        seen.add(k)
        unique.append(i)
    return unique[:30]


def score_item(item: dict, niche_keywords: list) -> float:
    text = (item["title"] + " " + item["summary"]).lower()
    specificity = sum(1 for k in niche_keywords if k.lower() in text)
    novelty = 5 if "new" in text or "first" in text or "launch" in text else 3
    noise = 0
    if any(x in text for x in ("sponsored", "ad", "affiliate", "click here")):
        noise = 3
    actionability = 5 if any(x in text for x in ("how to", "guide", "review", "comparison")) else 3
    return specificity * 3 + novelty + actionability - noise


def inject_affiliates(text: str, niche: str) -> str:
    affiliate_map = storage.load("nl_affiliates.json", {})
    niche_links = affiliate_map.get(niche, {})
    for keyword, link in niche_links.items():
        if keyword.lower() in text.lower() and link not in text:
            text = re.sub(rf"\b{re.escape(keyword)}\b",
                          f"[{keyword}]({link})", text, count=1, flags=re.I)
    return text


def build_newsletter(niche: str, paid_tier: bool = False) -> str:
    config = storage.load("nl_niche_configs.json", {})
    cfg = config.get(niche, {"keywords": [niche.replace("-", " ")]})
    items = []
    niche_dir = SNAP_DIR / niche
    if not niche_dir.exists():
        return ""
    for snap in niche_dir.glob("*.html"):
        items.extend(parse_items(snap))
    if not items:
        return ""
    for i in items:
        i["score"] = score_item(i, cfg["keywords"])
    items.sort(key=lambda x: -x["score"])
    count = 7 if paid_tier else 5

    lines = [f"# NicheLens — {niche.replace('-', ' ').title()}",
             f"_{datetime.now():%B %d, %Y}_\n"]
    if not paid_tier:
        lines.append("**SPONSORED:** Want zero ads + 7 items? Upgrade to paid: $7/mo → paypal.me/wholesaleomniverse/7\n")

    for i, item in enumerate(items[:count], 1):
        curator_note = f"Why this matters: it sits squarely in the {cfg['keywords'][0]} conversation right now."
        summary = inject_affiliates(item["summary"][:300], niche)
        lines.append(f"\n## {i}. {item['title']}")
        if item.get("url"):
            lines.append(f"[Source]({item['url']})")
        lines.append(f"\n{summary}\n")
        lines.append(f"_{curator_note}_\n")

    body = "\n".join(lines)
    NEWSLETTER_DIR.mkdir(parents=True, exist_ok=True)
    suffix = "paid" if paid_tier else "free"
    (NEWSLETTER_DIR / f"{niche}_{datetime.now():%Y%m%d}_{suffix}.md").write_text(body)
    return body


def fulfill_cycle() -> dict:
    subs = storage.load("nl_subscribers.json", [])
    niches = {s["niche"] for s in subs if s.get("status", "active") == "active"}
    sent = 0
    for n in niches:
        free_body = build_newsletter(n, paid_tier=False)
        paid_body = build_newsletter(n, paid_tier=True)
        if not free_body and not paid_body:
            continue
        for s in subs:
            if s.get("niche") != n or s.get("status", "active") != "active":
                continue
            body = paid_body if s.get("tier") == "paid" else free_body
            subject = f"NicheLens — {n.replace('-', ' ').title()} — {datetime.now():%b %d}"
            result = mailer.send(AGENT_KEY, s["email"], subject, body, purpose="fulfillment")
            if result.get("status") == "sent":
                sent += 1
    return {"fulfillment_sent": sent}


def run_full_cycle() -> dict:
    f = fulfill_cycle()
    rev = billing.revenue_summary(AGENT_KEY)
    subs = storage.load("nl_subscribers.json", [])
    metrics.record(
        AGENT_KEY,
        fulfillment_sent=f["fulfillment_sent"],
        active_subs=sum(1 for s in subs if s.get("status", "active") == "active" and s.get("tier") == "paid"),
        free_subs=sum(1 for s in subs if s.get("tier") != "paid"),
        mrr=rev["mrr"],
        total_revenue=rev["total_paid"],
    )
    return {**f, **rev}
