"""
SEOWriter — keyword → outlined SEO article draft.
Revenue: $39/article, $149/mo (5 articles), $499/mo unlimited.

Two input sources:
  1. Owner-dropped JSON in data/sw_inputs/{slug}.json:
       {
         "topic": "how to find cash buyers for real estate",
         "primary_keyword": "cash buyers for real estate",
         "secondary_keywords": ["cash investor list", "wholesale buyers"],
         "audience": "real estate wholesalers",
         "tone": "practical",
         "target_words": 1500
       }
  2. Auto-source from data/pb_briefs/*.md (PaperBrief) — each brief becomes
     a long-form SEO article in its vertical.

Engine:
  - If ANTHROPIC_API_KEY set → Claude `claude-sonnet-4-6` drafts the full article.
  - Else → heuristic template renderer produces a skeleton with H1/H2/H3,
    keyword density targets, FAQ, and [[TODO]] markers for owner polish.

Output: data/sw_outputs/{slug}.md with full article + meta block.
"""
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from autonomous import storage, mailer, billing, metrics

AGENT_KEY = "seowriter"
INPUTS_DIR = Path(__file__).parent.parent / "data" / "sw_inputs"
OUTPUTS_DIR = Path(__file__).parent.parent / "data" / "sw_outputs"
PB_BRIEFS_DIR = Path(__file__).parent.parent / "data" / "pb_briefs"


def _expand_keywords(primary: str, secondary: list) -> list:
    """Cheap related-term expansion — no API needed."""
    seeds = [primary] + list(secondary)
    variants = set()
    for s in seeds:
        s = s.strip().lower()
        if not s:
            continue
        variants.add(s)
        variants.add(f"best {s}")
        variants.add(f"how to {s}")
        variants.add(f"{s} guide")
        variants.add(f"{s} for beginners")
        variants.add(f"{s} tips")
    return sorted(variants)[:15]


def _claude_article(spec: dict) -> str:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return ""
    try:
        import anthropic
        client = anthropic.Anthropic()
        prompt = (
            f"Write a well-structured SEO article in markdown.\n\n"
            f"Topic: {spec.get('topic')}\n"
            f"Primary keyword: {spec.get('primary_keyword')}\n"
            f"Secondary keywords: {', '.join(spec.get('secondary_keywords', []))}\n"
            f"Audience: {spec.get('audience', 'general readers')}\n"
            f"Tone: {spec.get('tone', 'practical, direct')}\n"
            f"Target length: ~{spec.get('target_words', 1500)} words\n\n"
            f"Structure: H1 title, 2-paragraph intro, 4-6 H2 sections, "
            f"a 'Common mistakes' section, FAQ with 4 Q/A, and a conclusion. "
            f"Use the primary keyword in the H1 and first paragraph. "
            f"No filler. No 'in this article we will...' preambles. "
            f"Start with the strongest claim of the piece."
        )
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text.strip()
    except Exception:
        return ""


def _heuristic_article(spec: dict) -> str:
    topic = spec.get("topic", "Untitled")
    primary = spec.get("primary_keyword", topic)
    secondary = spec.get("secondary_keywords", [])
    audience = spec.get("audience", "readers")
    related = _expand_keywords(primary, secondary)

    title = topic if topic[:1].isupper() else topic.title()
    h2s = [
        f"What is {primary}?",
        f"Why {primary} matters in {datetime.now().year}",
        f"How to get started with {primary}",
        f"Top mistakes {audience} make with {primary}",
        f"Tools and resources for {primary}",
    ]

    out = [f"# {title}", ""]
    out.append(f"_If you're researching {primary}, this guide gives you the practical "
               f"playbook — what works, what to skip, and the mistakes that cost "
               f"{audience} the most time._")
    out.append("")
    out.append("[[TODO: replace this with a 2-paragraph opening that uses the primary keyword "
               "in the first sentence and addresses the reader's specific pain.]]")
    out.append("")
    for h in h2s:
        out.append(f"## {h}")
        out.append("")
        out.append(f"[[TODO: 2–4 paragraphs covering '{h}'. Mention the primary keyword once "
                   f"and at least one related term: {', '.join(related[:3])}.]]")
        out.append("")
    out.append("## Frequently asked questions")
    out.append("")
    for q in [
        f"What does {primary} actually mean?",
        f"How long does it take to see results from {primary}?",
        f"Is {primary} worth it for a beginner?",
        f"What's the cheapest way to start with {primary}?",
    ]:
        out.append(f"**{q}**")
        out.append("")
        out.append("[[TODO: 1–2 sentence answer]]")
        out.append("")
    out.append("## Conclusion")
    out.append("")
    out.append(f"[[TODO: 1-paragraph wrap-up that restates the main thesis and includes a CTA.]]")
    out.append("")
    out.append("---")
    out.append("**SEO meta**")
    meta_title = f"{title} ({datetime.now().year} Guide)"[:60]
    meta_desc = (f"A practical guide to {primary} for {audience} — "
                 f"what works, what doesn't, and the fastest path to results.")[:160]
    out.append(f"- Title tag: {meta_title}")
    out.append(f"- Meta description: {meta_desc}")
    out.append(f"- Related terms covered: {', '.join(related)}")
    out.append("")
    out.append(f"_Skeleton generated {datetime.now():%Y-%m-%d} by SEOWriter "
               f"(heuristic mode — set ANTHROPIC_API_KEY for full draft)._")
    return "\n".join(out)


def build_article(spec: dict, slug: str) -> dict:
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUTS_DIR / f"{slug}.md"
    body = _claude_article(spec) or _heuristic_article(spec)
    out_path.write_text(body)
    return {"slug": slug, "out_path": str(out_path), "engine":
            "claude" if os.environ.get("ANTHROPIC_API_KEY") else "heuristic"}


def _spec_from_paperbrief(md_path: Path) -> dict:
    text = md_path.read_text(errors="ignore")
    title_m = re.search(r"^#\s+(.+)$", text, re.M)
    title = title_m.group(1).strip() if title_m else md_path.stem
    vertical_m = re.search(r"\*\*Vertical:\*\*\s+(.+)$", text, re.M)
    vertical = vertical_m.group(1).strip() if vertical_m else "general"
    return {
        "topic": f"What {title} means for {vertical}",
        "primary_keyword": title.lower(),
        "secondary_keywords": [vertical, "research", "industry trends"],
        "audience": f"{vertical} practitioners",
        "tone": "analytical, practical",
        "target_words": 1200,
    }


def build_queue() -> dict:
    INPUTS_DIR.mkdir(parents=True, exist_ok=True)
    produced = 0
    for spec_path in sorted(INPUTS_DIR.glob("*.json")):
        slug = spec_path.stem
        if (OUTPUTS_DIR / f"{slug}.md").exists():
            continue
        try:
            spec = json.loads(spec_path.read_text())
        except Exception:
            continue
        build_article(spec, slug)
        produced += 1

    if PB_BRIEFS_DIR.exists():
        for md in PB_BRIEFS_DIR.glob("*.md"):
            slug = f"pb-{md.stem}"
            if (OUTPUTS_DIR / f"{slug}.md").exists():
                continue
            spec = _spec_from_paperbrief(md)
            build_article(spec, slug)
            produced += 1
    return {"articles_produced": produced}


def fulfill_cycle() -> dict:
    subs = storage.load("sw_subscribers.json", [])
    log = storage.load("sw_delivery_log.json", {})
    sent = 0
    for sub in subs:
        if sub.get("status") != "active":
            continue
        email = sub.get("email")
        if not email:
            continue
        already = set(log.get(email, []))
        new_files = [p for p in OUTPUTS_DIR.glob("*.md") if p.name not in already]
        if not new_files:
            continue
        body_parts = [f"Hi {sub.get('name', 'there')},\n",
                      f"{len(new_files)} new SEO article draft(s) ready:\n"]
        for p in new_files[:5]:
            body_parts.append(f"\n--- {p.stem} ---\n")
            body_parts.append(p.read_text()[:2500])
        r = mailer.send(AGENT_KEY, email,
                        f"SEO articles ready — {len(new_files)} new",
                        "\n".join(body_parts), purpose="fulfillment")
        if r.get("status") == "sent":
            log[email] = list(already | {p.name for p in new_files})
            sent += 1
    storage.save("sw_delivery_log.json", log)
    return {"fulfillment_sent": sent}


def acquire_cycle() -> dict:
    leads = storage.load("sw_leads.json", [])
    sent = 0
    for lead in leads:
        if lead.get("trial_sent"):
            continue
        email = lead.get("email")
        if not email:
            continue
        body = (
            f"Hi {lead.get('name', 'there')},\n\n"
            f"I run an automated SEO article writing service for niche site owners and B2B blogs.\n"
            f"Send me one target keyword + audience and you'll get back a structured 1,200–1,800-word draft within 48 hours — free first one.\n\n"
            f"Pricing after the trial:\n"
            f"  $39 per article (one-off)\n"
            f"  $149/mo for 5 articles\n"
            f"  $499/mo unlimited\n\n"
            f"Reply with a keyword and target audience and I'll send the draft back.\n"
        )
        r = mailer.send(AGENT_KEY, email,
                        "Free 1,500-word SEO article draft",
                        body, purpose="outreach")
        if r.get("status") == "sent":
            lead["trial_sent"] = datetime.now().isoformat()
            sent += 1
    storage.save("sw_leads.json", leads)
    return {"outreach_sent": sent}


def run_full_cycle() -> dict:
    q = build_queue()
    a = acquire_cycle()
    f = fulfill_cycle()
    rev = billing.revenue_summary(AGENT_KEY)
    subs = storage.load("sw_subscribers.json", [])
    metrics.record(
        AGENT_KEY,
        products_produced=q["articles_produced"],
        outreach_sent=a["outreach_sent"],
        fulfillment_sent=f["fulfillment_sent"],
        active_subs=sum(1 for s in subs if s.get("status") == "active"),
        mrr=rev["mrr"],
        total_revenue=rev["total_paid"],
    )
    return {**q, **a, **f, **rev}
