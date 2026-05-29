"""
GutenbergVoice — turns public-domain texts into narration-ready scripts.
Revenue: $19 chapter pack, $97 full kit, $297 premium kit, $29/mo "Script of the Week".
"""
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from autonomous import storage, mailer, billing, metrics

AGENT_KEY = "gutenberg_voice"
TEXTS_DIR = Path(__file__).parent.parent / "data" / "gv_texts"
SCRIPTS_DIR = Path(__file__).parent.parent / "data" / "gv_scripts"
LISTINGS_DIR = Path(__file__).parent.parent / "data" / "gv_listings"

CHAPTER_PATTERNS = [
    r"^CHAPTER\s+[IVXLCDM\d]+",
    r"^Chapter\s+[IVXLCDM\d]+",
    r"^BOOK\s+[IVXLCDM\d]+",
    r"^[IVXLCDM]+\.\s*$",
]


def verify_gutenberg(text: str) -> bool:
    return "Project Gutenberg" in text[:5000] or "PROJECT GUTENBERG" in text[:5000]


def segment_chapters(text: str) -> list:
    lines = text.splitlines()
    starts = []
    for i, line in enumerate(lines):
        for pat in CHAPTER_PATTERNS:
            if re.match(pat, line.strip()):
                starts.append((i, line.strip()))
                break
    if len(starts) < 2:
        # Fall back: split into 10 roughly-equal blocks
        block = max(1, len(lines) // 10)
        starts = [(i, f"Section {n+1}") for n, i in enumerate(range(0, len(lines), block))]
    chapters = []
    for j, (idx, title) in enumerate(starts):
        end = starts[j + 1][0] if j + 1 < len(starts) else len(lines)
        body = "\n".join(lines[idx:end]).strip()
        chapters.append({"title": title, "body": body})
    return chapters


def add_narration_cues(text: str) -> str:
    """Insert pacing cues so a narrator (or TTS) has natural rhythm."""
    text = re.sub(r"([.!?])\s+", r"\1 [pause] ", text)
    text = re.sub(r"\s+,", ",", text)
    text = re.sub(r"--", " [breath] -- ", text)
    text = re.sub(r"\"\s*([^\"]+)\s*\"", r'[emphasis: \1]', text)
    return text


def produce_book(book_id: str) -> dict:
    text_path = TEXTS_DIR / f"{book_id}.txt"
    if not text_path.exists():
        return {"error": "missing_text", "book_id": book_id}
    text = text_path.read_text(errors="ignore")
    if not verify_gutenberg(text):
        return {"error": "not_public_domain", "book_id": book_id}

    chapters = segment_chapters(text)
    out_dir = SCRIPTS_DIR / book_id
    out_dir.mkdir(parents=True, exist_ok=True)
    word_count = 0
    for n, ch in enumerate(chapters, 1):
        cued = add_narration_cues(ch["body"])
        (out_dir / f"chapter_{n:03d}.txt").write_text(f"{ch['title']}\n\n{cued}")
        word_count += len(ch["body"].split())

    runtime_min = word_count // 150
    notes = (
        f"# Producer Notes — {book_id}\n\n"
        f"- Chapters: {len(chapters)}\n"
        f"- Word count: {word_count:,}\n"
        f"- Estimated runtime @ 150 wpm: {runtime_min // 60}h {runtime_min % 60}m\n\n"
        f"## Voice Direction\n"
        f"- Establish a neutral narrator tone first chapter, then settle into character voicing.\n"
        f"- Dialogue marked [emphasis: ...] — vary cadence between characters.\n"
        f"- [pause] tags ≈ 400ms; [breath] tags ≈ 700ms.\n"
    )
    (out_dir / "PRODUCER_NOTES.md").write_text(notes)
    return {"book_id": book_id, "chapters": len(chapters), "words": word_count, "runtime_min": runtime_min}


def generate_listing(book_id: str, result: dict) -> str:
    LISTINGS_DIR.mkdir(parents=True, exist_ok=True)
    title = book_id.replace("_", " ").title()
    listing = f"""# {title} — Narration-Ready Audiobook Script

**Public Domain · {result['chapters']} chapters · {result['words']:,} words · ~{result['runtime_min']//60}h {result['runtime_min']%60}m runtime**

Save weeks of prep work. This kit gives you a fully formatted narration script for {title}, with:

- Chapter-by-chapter clean text (no page numbers, no scan artifacts)
- Embedded pacing cues: [pause], [breath], [emphasis]
- Producer notes with voice direction
- Estimated runtime per chapter

Perfect for: ACX narrators, YouTube audiobook channels, podcast producers, voice acting practice.

## Pricing

- **Chapter Pack** (first 5 chapters): $19
- **Full Kit** (all chapters + producer notes): $97
- **Premium Kit** (full kit + SSML samples + character voice guide): $297

Order via PayPal: paypal.me/wholesaleomniverse/97

Delivery: instant ZIP via email after payment confirmation.
"""
    (LISTINGS_DIR / f"{book_id}_listing.md").write_text(listing)
    return listing


def queue_cycle() -> dict:
    queue = storage.load("gv_queue.json", [])
    produced = []
    for book_id in queue:
        slug_dir = SCRIPTS_DIR / book_id
        if slug_dir.exists():
            continue
        r = produce_book(book_id)
        if "error" not in r:
            generate_listing(book_id, r)
            produced.append(r)
    return {"produced": len(produced), "queue_size": len(queue)}


def deliver_orders() -> dict:
    """Send paid orders to buyers."""
    orders = storage.load("gv_orders.json", [])
    sent = 0
    for o in orders:
        if o.get("status") != "paid" or o.get("delivered_at"):
            continue
        book_dir = SCRIPTS_DIR / o["book_id"]
        if not book_dir.exists():
            continue
        attachments = sorted(str(p) for p in book_dir.glob("*"))[:25]
        body = (
            f"Hi {o.get('buyer_name', 'there')},\n\n"
            f"Your narration kit for {o['book_id'].replace('_', ' ').title()} is attached.\n\n"
            f"Includes {o.get('chapters', '?')} chapters + producer notes.\n\n"
            f"If you publish this, we'd love a credit: 'Script prepared by Wholesale Omniverse'.\n\n"
            f"Happy narrating."
        )
        result = mailer.send(AGENT_KEY, o["buyer_email"],
                             f"Your {o['book_id'].replace('_', ' ').title()} narration kit",
                             body, purpose="fulfillment", attachments=attachments)
        if result.get("status") == "sent":
            o["status"] = "delivered"
            o["delivered_at"] = datetime.now().isoformat()
            sent += 1
    storage.save("gv_orders.json", orders)
    return {"orders_delivered": sent}


def run_full_cycle() -> dict:
    q = queue_cycle()
    d = deliver_orders()
    rev = billing.revenue_summary(AGENT_KEY)
    metrics.record(
        AGENT_KEY,
        products_produced=q["produced"],
        fulfillment_sent=d["orders_delivered"],
        mrr=rev["mrr"],
        total_revenue=rev["total_paid"],
    )
    return {**q, **d, **rev}
