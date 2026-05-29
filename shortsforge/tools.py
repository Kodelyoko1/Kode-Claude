"""
ShortsForge — YouTube Shorts content architect.
Niches: Motivational, Comedy Skits, Men's Wellness.

Revenue model:
  - AdSense (YouTube Partner Program once eligible)
  - Substack/email premium tier: $5/mo for deeper wellness/motivational content
  - Affiliate links in description (men's grooming, books, supplements)

Workflow:
  1. Owner drops a transcript file into data/sf_transcripts/{slug}.txt
     (with optional first line metadata: NICHE=motivational|comedy|wellness)
  2. Agent generates: hook, trim plan, captioning strategy, SEO pack, storyboard
  3. Outputs a ready-to-edit production brief in data/sf_briefs/{slug}.md
  4. Free Substack-style digest builds audience; paid tier delivered weekly
"""
import re
import sys
import random
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from autonomous import storage, mailer, billing, metrics

AGENT_KEY = "shortsforge"
TRANSCRIPTS_DIR = Path(__file__).parent.parent / "data" / "sf_transcripts"
BRIEFS_DIR = Path(__file__).parent.parent / "data" / "sf_briefs"
NEWSLETTERS_DIR = Path(__file__).parent.parent / "data" / "sf_newsletters"

CHANNEL_CONFIG_FILE = "sf_channel_config.json"


def get_channel_config() -> dict:
    cfg = storage.load(CHANNEL_CONFIG_FILE, {})
    return {
        "channel_name":   cfg.get("channel_name", "[CHANNEL NAME TBD]"),
        "channel_handle": cfg.get("channel_handle", "@channel"),
        "substack_url":   cfg.get("substack_url", "https://yourchannel.substack.com"),
        "owner_name":     cfg.get("owner_name", "Tyreese"),
    }


def set_channel_config(**kwargs):
    cfg = storage.load(CHANNEL_CONFIG_FILE, {})
    cfg.update(kwargs)
    storage.save(CHANNEL_CONFIG_FILE, cfg)
    return cfg


HOOK_PATTERNS = {
    "motivational": [
        "Nobody told you {topic}—but I will.",
        "If you're still {struggle}, watch this before you scroll.",
        "The reason you're stuck is simpler than you think.",
        "I wish someone told me this at 20.",
        "Stop {bad_habit}. Start this instead.",
    ],
    "comedy": [
        "POV: you just {situation}.",
        "When {character} thinks they're slick:",
        "Nobody:\nAbsolutely nobody:\n{character}: {action}",
        "Tell me you're {trait} without telling me you're {trait}.",
        "Me trying to {action} after {context}:",
    ],
    "wellness": [
        "Your {body_part} is screaming for this.",
        "I tried {practice} for 30 days. Here's what changed.",
        "If you're a guy over 25, you need to know this.",
        "The 3-second test that tells you you're {condition}.",
        "Doctors won't say this. I will.",
    ],
}


KEYWORDS_BY_NICHE = {
    "motivational": ["motivation", "mindset", "discipline", "selfimprovement",
                     "growthmindset", "lifehack", "stoicism"],
    "comedy":       ["funny", "comedy", "skit", "fyp", "viral",
                     "relatable", "lol"],
    "wellness":     ["menshealth", "wellness", "mentalhealth", "fitness",
                     "testosterone", "selfcare", "lifestyle"],
}


def detect_niche(transcript: str) -> str:
    text = transcript.lower()
    if m := re.search(r"niche\s*=\s*(\w+)", text[:200]):
        return m.group(1)
    scores = {"motivational": 0, "comedy": 0, "wellness": 0}
    for niche, kws in KEYWORDS_BY_NICHE.items():
        for kw in kws:
            scores[niche] += text.count(kw.lower())
    if max(scores.values()) == 0:
        # Heuristic on tone
        if any(w in text for w in ("lol", "haha", "joke", "funny", "pov")):
            return "comedy"
        if any(w in text for w in ("workout", "testosterone", "health", "sleep", "stress")):
            return "wellness"
        return "motivational"
    return max(scores, key=scores.get)


def find_best_segment(transcript: str, target_seconds: int = 45) -> dict:
    """Pick the highest-density 30-50 second segment from the transcript."""
    # Words-per-second baseline of 2.5
    target_words = int(target_seconds * 2.5)
    words = transcript.split()
    if len(words) <= target_words:
        return {"start_word": 0, "end_word": len(words),
                "text": transcript, "estimated_seconds": len(words) / 2.5}

    # Score each window by sentence-stop density + capitalized starts
    best_score = -1
    best_start = 0
    for start in range(0, len(words) - target_words, max(1, target_words // 6)):
        chunk = " ".join(words[start:start + target_words])
        sentence_count = len(re.findall(r"[.!?]", chunk))
        emotional = len(re.findall(r"\b(never|always|every|nobody|stop|start|wish|need|must|will)\b",
                                    chunk, re.I))
        score = sentence_count + emotional * 2
        if score > best_score:
            best_score = score
            best_start = start
    chunk_text = " ".join(words[best_start:best_start + target_words])
    return {"start_word": best_start, "end_word": best_start + target_words,
            "text": chunk_text, "estimated_seconds": target_words / 2.5}


def generate_hook(text: str, niche: str) -> str:
    sentences = re.split(r"(?<=[.!?])\s+", text)
    first = sentences[0] if sentences else text[:120]
    pattern = random.choice(HOOK_PATTERNS.get(niche, HOOK_PATTERNS["motivational"]))
    # Slot in keywords from the first sentence
    words = [w for w in first.split() if len(w) > 3][:3]
    topic = words[0].lower() if words else "this"
    fills = {
        "topic": topic, "struggle": topic, "bad_habit": topic,
        "situation": topic, "character": topic.title(), "action": topic,
        "trait": topic, "context": topic, "body_part": topic,
        "practice": topic, "condition": topic,
    }
    try:
        return pattern.format(**fills)
    except KeyError:
        return pattern


def make_captions(text: str) -> list:
    """Split text into 2-4 word punchy caption chunks."""
    words = text.split()
    captions = []
    chunk_size = 3
    for i in range(0, len(words), chunk_size):
        chunk = " ".join(words[i:i + chunk_size])
        captions.append(chunk.upper().rstrip(".,!?"))
    return captions


def make_storyboard(text: str, niche: str) -> list:
    sentences = re.split(r"(?<=[.!?])\s+", text)
    storyboard = []
    timing = 0.0
    broll_by_niche = {
        "motivational": ["sunrise over mountains", "runner at dawn", "person writing in notebook",
                         "city skyline timelapse"],
        "comedy":       ["confused face zoom-in", "dramatic chair spin", "slow-mo reveal",
                         "reaction GIF overlay"],
        "wellness":     ["weight rack close-up", "stretching silhouette", "healthy meal flatlay",
                         "sleep timelapse"],
    }
    brolls = broll_by_niche.get(niche, broll_by_niche["motivational"])
    for i, sent in enumerate(sentences):
        if not sent.strip():
            continue
        words = sent.split()
        secs = max(1.5, len(words) / 2.5)
        storyboard.append({
            "time_in":  round(timing, 1),
            "time_out": round(timing + secs, 1),
            "voiceover": sent.strip()[:200],
            "broll":    brolls[i % len(brolls)],
            "caption":  " ".join(words[:4]).upper().rstrip(".,!?"),
        })
        timing += secs
    return storyboard


def seo_pack(text: str, niche: str, hook: str) -> dict:
    base_keywords = KEYWORDS_BY_NICHE.get(niche, [])
    cfg = get_channel_config()
    # Title — hook + niche flavor
    title = hook[:60].rstrip(".,!?") + " #shorts"
    # Description — 2 sentences
    description = (
        f"{hook} If this hit, follow {cfg['channel_handle']} for more.\n\n"
        f"Get the deeper version (free): {cfg['substack_url']}"
    )
    hashtags = ["#shorts", f"#{niche}", *[f"#{k}" for k in base_keywords[:4]]]
    return {"title": title[:100], "description": description, "hashtags": hashtags[:5]}


def build_brief(transcript_path: Path) -> dict:
    if not transcript_path.exists():
        return {"error": "missing_transcript"}
    raw = transcript_path.read_text(errors="ignore").strip()
    if len(raw) < 80:
        return {"error": "transcript_too_short"}

    niche = detect_niche(raw)
    # Strip any NICHE=xxx metadata line before processing
    transcript = re.sub(r"^.*niche\s*=\s*\w+.*\n?", "", raw, flags=re.I | re.M).strip()
    if len(transcript) < 80:
        transcript = raw  # fall back if stripping killed it
    segment = find_best_segment(transcript)
    hook = generate_hook(segment["text"], niche)
    captions = make_captions(segment["text"])
    storyboard = make_storyboard(segment["text"], niche)
    seo = seo_pack(segment["text"], niche, hook)
    cfg = get_channel_config()

    BRIEFS_DIR.mkdir(parents=True, exist_ok=True)
    brief_path = BRIEFS_DIR / f"{transcript_path.stem}.md"

    lines = [
        f"# ShortsForge Brief — {transcript_path.stem}",
        f"_Channel: {cfg['channel_name']} · Niche: {niche.title()}_\n",
        f"## 🎯 The Hook (first 3 seconds)",
        f"> **{hook}**\n",
        f"## ✂️ Trim Plan",
        f"- Source words: {len(transcript.split())}",
        f"- Keep words: {segment['end_word'] - segment['start_word']}",
        f"- Estimated runtime: ~{segment['estimated_seconds']:.0f}s",
        f"- Cut from word {segment['start_word']} to {segment['end_word']}\n",
        f"## 📝 Final Voiceover Script\n",
        segment["text"],
        f"\n## 🎬 Storyboard (CapCut-ready)\n",
    ]
    for i, s in enumerate(storyboard, 1):
        lines.append(f"**Beat {i}** · {s['time_in']}s–{s['time_out']}s")
        lines.append(f"- VO: {s['voiceover']}")
        lines.append(f"- B-roll: {s['broll']}")
        lines.append(f"- Caption: **{s['caption']}**\n")

    lines.append("## 📺 SEO Pack")
    lines.append(f"- **Title:** {seo['title']}")
    lines.append(f"- **Description:**\n  > {seo['description'].replace(chr(10), chr(10)+'  > ')}")
    lines.append(f"- **Hashtags:** {' '.join(seo['hashtags'])}\n")

    lines.append("## ⚠️ Compliance")
    lines.append("- Transformation: hook rewritten, pacing recut, captions added → original ✓")
    lines.append("- Credit original creator in description if remixing 3rd-party material")
    lines.append("- Use CapCut Auto-Captions (free) for accessibility + retention\n")

    lines.append("## 📋 Captions (copy/paste sequence)")
    for c in captions:
        lines.append(f"- {c}")

    brief_path.write_text("\n".join(lines))
    return {"brief_path": str(brief_path), "niche": niche, "hook": hook,
            "title": seo["title"], "runtime": segment["estimated_seconds"]}


def process_queue() -> dict:
    """Process every transcript in the queue directory."""
    TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    BRIEFS_DIR.mkdir(parents=True, exist_ok=True)
    processed = 0
    failed = 0
    briefs_made = []
    for txt in TRANSCRIPTS_DIR.glob("*.txt"):
        if (BRIEFS_DIR / f"{txt.stem}.md").exists():
            continue
        result = build_brief(txt)
        if "error" in result:
            failed += 1
        else:
            processed += 1
            briefs_made.append(result)
    return {"briefs_made": processed, "failed": failed, "items": briefs_made}


def build_substack_digest() -> str:
    """Curate the latest briefs into a free Substack-style weekly digest."""
    briefs = sorted(BRIEFS_DIR.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)[:5]
    if not briefs:
        return ""
    cfg = get_channel_config()
    lines = [f"# {cfg['channel_name']} — Weekly Mental Floss",
             f"_{datetime.now():%B %d, %Y}_\n"]
    for b in briefs:
        content = b.read_text()
        hook_m = re.search(r"> \*\*(.+?)\*\*", content)
        hook = hook_m.group(1) if hook_m else "[hook]"
        lines.append(f"## {hook}")
        lines.append(f"[Watch on YouTube]({cfg['substack_url']})\n")
    lines.append("---")
    lines.append("**Want the deeper takes?** Upgrade to Premium: $5/mo — exclusive wellness guides + early access to drops.")
    lines.append(f"→ paypal.me/wholesaleomniverse/5")
    return "\n".join(lines)


def send_newsletter() -> dict:
    subs = storage.load("sf_subscribers.json", [])
    if not subs:
        return {"newsletters_sent": 0}
    digest = build_substack_digest()
    if not digest:
        return {"newsletters_sent": 0}
    NEWSLETTERS_DIR.mkdir(parents=True, exist_ok=True)
    (NEWSLETTERS_DIR / f"digest_{datetime.now():%Y%m%d}.md").write_text(digest)

    sent = 0
    for s in subs:
        if s.get("status", "active") != "active":
            continue
        # Premium tier gets extended digest; free tier gets standard
        body = digest
        if s.get("tier") == "premium":
            body += "\n\n## Premium Deep Dive\n\nYour exclusive guide is attached below.\n\n_[Owner: drop deeper content here weekly.]_"
        result = mailer.send(AGENT_KEY, s["email"],
                             f"{get_channel_config()['channel_name']} — week of {datetime.now():%b %d}",
                             body, purpose="fulfillment")
        if result.get("status") == "sent":
            sent += 1
    return {"newsletters_sent": sent}


def acquire_cycle() -> dict:
    """Drive Substack signups via outreach to leads collected from YouTube/TikTok comments."""
    leads = storage.load("sf_leads.json", [])
    sent = 0
    cfg = get_channel_config()
    for lead in leads:
        if lead.get("contacted"):
            continue
        body = (
            f"Hey — saw your comment on the latest {cfg['channel_name']} drop.\n\n"
            f"I send a weekly free digest with the best clips + a deeper take you won't see on YouTube.\n\n"
            f"Sign up free: {cfg['substack_url']}\n\n"
            f"Or go Premium ($5/mo) for the full wellness/motivation library: paypal.me/wholesaleomniverse/5"
        )
        result = mailer.send(AGENT_KEY, lead["email"],
                             f"Saw your comment — here's the newsletter",
                             body, purpose="outreach")
        if result.get("status") == "sent":
            lead["contacted"] = datetime.now().isoformat()
            sent += 1
    storage.save("sf_leads.json", leads)
    return {"outreach_sent": sent}


def run_full_cycle() -> dict:
    q = process_queue()
    n = send_newsletter()
    a = acquire_cycle()
    rev = billing.revenue_summary(AGENT_KEY)
    subs = storage.load("sf_subscribers.json", [])
    metrics.record(
        AGENT_KEY,
        products_produced=q["briefs_made"],
        outreach_sent=a["outreach_sent"],
        fulfillment_sent=n["newsletters_sent"],
        active_subs=sum(1 for s in subs if s.get("status", "active") == "active" and s.get("tier") == "premium"),
        free_subs=sum(1 for s in subs if s.get("tier") != "premium"),
        mrr=rev["mrr"],
        total_revenue=rev["total_paid"],
    )
    return {**q, **n, **a, **rev}
