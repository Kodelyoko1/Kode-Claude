"""
ShowNotes — transcript → structured podcast/video show notes.
Revenue: $29 per episode, $99/mo (4 episodes), $297/mo unlimited.

Pipeline:
  1. Source a transcript from one of:
       - data/sn_inputs/{slug}.txt  (owner-dropped)
       - data/tr_outputs/{slug}.txt (auto-chained from `transcribe` agent)
  2. Build structured show notes: TL;DR, key takeaways, timestamps,
     pull quotes, resources, SEO title + description.
  3. Optional .srt at the same path enables real timestamps.
  4. Deliver markdown to data/sn_outputs/{slug}.md.

Heuristics-only by default. If ANTHROPIC_API_KEY is set, the TL;DR step is
upgraded to a Claude call; everything else still runs without it.
"""
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from autonomous import storage, mailer, billing, metrics

AGENT_KEY = "shownotes"
INPUTS_DIR = Path(__file__).parent.parent / "data" / "sn_inputs"
OUTPUTS_DIR = Path(__file__).parent.parent / "data" / "sn_outputs"
TR_OUTPUTS_DIR = Path(__file__).parent.parent / "data" / "tr_outputs"

URL_RE = re.compile(r"https?://[^\s)>\]]+")
STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "of", "in", "on", "at", "to", "for",
    "with", "from", "by", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "should", "could",
    "i", "you", "he", "she", "it", "we", "they", "me", "him", "her", "us", "them",
    "my", "your", "his", "its", "our", "their", "this", "that", "these", "those",
    "what", "which", "who", "whom", "whose", "when", "where", "why", "how",
    "so", "if", "then", "than", "as", "just", "really", "very", "much", "more",
    "like", "know", "think", "going", "kind", "right", "yeah", "okay", "well",
    "thing", "things", "people", "way", "lot", "say", "said", "get", "got",
}


def _split_sentences(text: str) -> list:
    text = re.sub(r"\s+", " ", text).strip()
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if len(s.strip()) > 12]


def _key_sentences(text: str, top_n: int = 5) -> list:
    sentences = _split_sentences(text)
    if not sentences:
        return []
    tokens = [w for w in re.findall(r"[a-z]+", text.lower())
              if w not in STOPWORDS and len(w) > 3]
    freq = Counter(tokens)
    scored = []
    for s in sentences:
        words = [w for w in re.findall(r"[a-z]+", s.lower())
                 if w not in STOPWORDS and len(w) > 3]
        if not words:
            continue
        score = sum(freq[w] for w in words) / (len(words) ** 0.5)
        scored.append((score, s))
    scored.sort(key=lambda x: -x[0])
    seen = set()
    picked = []
    for _, s in scored:
        key = s[:60].lower()
        if key in seen:
            continue
        seen.add(key)
        picked.append(s)
        if len(picked) >= top_n:
            break
    return picked


def _claude_tldr(text: str) -> str:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return ""
    try:
        import anthropic
        client = anthropic.Anthropic()
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=200,
            messages=[{
                "role": "user",
                "content": (
                    "Write a 2-sentence TL;DR for this podcast/video transcript. "
                    "No preamble, just the TL;DR.\n\n"
                    + text[:8000]
                )
            }],
        )
        return msg.content[0].text.strip()
    except Exception:
        return ""


def _heuristic_tldr(text: str) -> str:
    sents = _key_sentences(text, top_n=2)
    return " ".join(sents) if sents else _split_sentences(text)[:2][0][:280]


def _parse_srt_timestamps(srt_text: str) -> list:
    """Return [(start_seconds, text), ...]."""
    entries = []
    blocks = re.split(r"\n\s*\n", srt_text.strip())
    for b in blocks:
        lines = [l for l in b.split("\n") if l.strip()]
        if len(lines) < 3:
            continue
        m = re.match(r"(\d+):(\d+):(\d+)[,.](\d+)", lines[1])
        if not m:
            continue
        h, mn, s, ms = map(int, m.groups())
        start = h * 3600 + mn * 60 + s + ms / 1000
        text = " ".join(lines[2:])
        entries.append((start, text))
    return entries


def _fmt_hms(seconds: float) -> str:
    s = int(seconds)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _chapter_timestamps(srt_entries: list, max_chapters: int = 6) -> list:
    """Pick evenly-spaced chapter markers with a generated title each."""
    if not srt_entries or len(srt_entries) < max_chapters:
        return []
    total = srt_entries[-1][0]
    step = total / max_chapters
    chapters = []
    for i in range(max_chapters):
        target = i * step
        nearest = min(srt_entries, key=lambda e: abs(e[0] - target))
        title = nearest[1].strip().rstrip(".,!?")
        if len(title) > 55:
            title = title[:55].rsplit(" ", 1)[0] + "…"
        chapters.append((nearest[0], title))
    return chapters


def _seo_pack(text: str) -> dict:
    sents = _split_sentences(text)
    title_seed = sents[0] if sents else "Podcast episode"
    title = re.sub(r"\s+", " ", title_seed)[:60].rstrip(",.;:") + ("…" if len(title_seed) > 60 else "")
    desc_seed = " ".join(sents[:3])
    desc = re.sub(r"\s+", " ", desc_seed)[:155].rstrip() + ("…" if len(desc_seed) > 155 else "")
    return {"title": title, "description": desc}


def build_show_notes(slug: str, transcript_text: str, srt_text: str = "") -> dict:
    """Produce a single markdown show-notes file."""
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUTS_DIR / f"{slug}.md"

    tldr = _claude_tldr(transcript_text) or _heuristic_tldr(transcript_text)
    takeaways = _key_sentences(transcript_text, top_n=5)
    resources = sorted(set(URL_RE.findall(transcript_text)))[:10]
    seo = _seo_pack(transcript_text)
    chapters = []
    if srt_text:
        srt_entries = _parse_srt_timestamps(srt_text)
        chapters = _chapter_timestamps(srt_entries)

    md = [f"# {slug}", ""]
    md.append("## TL;DR")
    md.append(tldr)
    md.append("")
    md.append("## Key takeaways")
    for t in takeaways:
        md.append(f"- {t}")
    md.append("")
    if chapters:
        md.append("## Chapters")
        for ts, title in chapters:
            md.append(f"- `{_fmt_hms(ts)}` — {title}")
        md.append("")
    if resources:
        md.append("## Resources mentioned")
        for url in resources:
            md.append(f"- {url}")
        md.append("")
    md.append("## SEO")
    md.append(f"- **Title:** {seo['title']}")
    md.append(f"- **Description:** {seo['description']}")
    md.append("")
    md.append("---")
    md.append(f"_Generated {datetime.now():%Y-%m-%d} by ShowNotes._")

    out_path.write_text("\n".join(md))
    return {"slug": slug, "out_path": str(out_path), "takeaways": len(takeaways),
            "chapters": len(chapters), "resources": len(resources)}


def _source_candidates() -> list:
    """Return [(slug, txt_path, srt_path_or_None)] from both input sources."""
    seen = set()
    out = []
    for d in (INPUTS_DIR, TR_OUTPUTS_DIR):
        if not d.exists():
            continue
        for txt in d.glob("*.txt"):
            slug = txt.stem
            if slug in seen:
                continue
            seen.add(slug)
            srt = txt.with_suffix(".srt")
            out.append((slug, txt, srt if srt.exists() else None))
    return out


def build_queue() -> dict:
    INPUTS_DIR.mkdir(parents=True, exist_ok=True)
    produced = 0
    for slug, txt_path, srt_path in _source_candidates():
        out_path = OUTPUTS_DIR / f"{slug}.md"
        if out_path.exists():
            continue
        text = txt_path.read_text(errors="ignore")
        if len(text) < 200:
            continue
        srt_text = srt_path.read_text(errors="ignore") if srt_path else ""
        build_show_notes(slug, text, srt_text)
        produced += 1
    return {"shownotes_produced": produced}


def fulfill_cycle() -> dict:
    subs = storage.load("sn_subscribers.json", [])
    log = storage.load("sn_delivery_log.json", {})
    sent = 0
    for sub in subs:
        if sub.get("status") != "active":
            continue
        email = sub.get("email")
        if not email:
            continue
        already = set(log.get(email, []))
        new_files = [p for p in OUTPUTS_DIR.glob("*.md") if p.stem not in already]
        if not new_files:
            continue
        body_parts = [f"Hi {sub.get('name', 'there')},\n",
                      f"New show notes ready: {len(new_files)}\n"]
        for p in new_files[:5]:
            body_parts.append(f"\n--- {p.stem} ---\n")
            body_parts.append(p.read_text()[:2500])
        body = "\n".join(body_parts)
        r = mailer.send(AGENT_KEY, email,
                        f"Show notes ready — {len(new_files)} new",
                        body, purpose="fulfillment")
        if r.get("status") == "sent":
            log[email] = list(already | {p.stem for p in new_files})
            sent += 1
    storage.save("sn_delivery_log.json", log)
    return {"fulfillment_sent": sent}


def acquire_cycle() -> dict:
    leads = storage.load("sn_leads.json", [])
    sample = ""
    samples = sorted(OUTPUTS_DIR.glob("*.md"))
    if samples:
        sample = samples[0].read_text()[:1500]
    sent = 0
    for lead in leads:
        if lead.get("trial_sent"):
            continue
        email = lead.get("email")
        if not email:
            continue
        body = (
            f"Hi {lead.get('name', 'there')},\n\n"
            f"I run a service that turns podcast/video transcripts into structured show notes:\n"
            f"TL;DR, key takeaways, chapter timestamps, resource links, and SEO title + description.\n\n"
            f"Sample format below. Want this for your next 3 episodes free?\n"
            f"Reply with a link to an episode and you'll get them within 48h.\n\n"
            f"Pricing after the trial:\n"
            f"  $29 per episode\n"
            f"  $99/mo for 4 episodes\n"
            f"  $297/mo unlimited\n\n"
            f"--- SAMPLE ---\n{sample}\n"
        )
        r = mailer.send(AGENT_KEY, email,
                        "Free show notes for your next 3 episodes",
                        body, purpose="outreach")
        if r.get("status") == "sent":
            lead["trial_sent"] = datetime.now().isoformat()
            sent += 1
    storage.save("sn_leads.json", leads)
    return {"outreach_sent": sent}


def run_full_cycle() -> dict:
    q = build_queue()
    a = acquire_cycle()
    f = fulfill_cycle()
    rev = billing.revenue_summary(AGENT_KEY)
    subs = storage.load("sn_subscribers.json", [])
    metrics.record(
        AGENT_KEY,
        products_produced=q["shownotes_produced"],
        outreach_sent=a["outreach_sent"],
        fulfillment_sent=f["fulfillment_sent"],
        active_subs=sum(1 for s in subs if s.get("status") == "active"),
        mrr=rev["mrr"],
        total_revenue=rev["total_paid"],
    )
    return {**q, **a, **f, **rev}
