"""
PaperBrief — vertical research summarization newsletter.
Revenue: $39/mo per vertical, $399/yr, $999/yr enterprise.
"""
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from autonomous import storage, mailer, billing, metrics
from paperbrief import health

AGENT_KEY = "paperbrief"
PDF_DIR = Path(__file__).parent.parent / "data" / "pb_pdfs"
BRIEFS_DIR = Path(__file__).parent.parent / "data" / "pb_briefs"


def extract_pdf(pdf_path: Path) -> str:
    try:
        import pypdf
    except ImportError:
        try:
            import PyPDF2 as pypdf
        except ImportError:
            return ""
    try:
        reader = pypdf.PdfReader(str(pdf_path))
        text = "\n".join(p.extract_text() or "" for p in reader.pages)
        return text
    except Exception:
        return ""


def sectionize(text: str) -> dict:
    """Extract abstract, method, results, limitations heuristically."""
    sections = {"abstract": "", "method": "", "results": "", "limitations": ""}
    lower = text.lower()
    for key, patterns in [
        ("abstract", [r"abstract[\s\n]+(.{200,3000}?)(?:1\.\s|introduction|keywords)"]),
        ("method",   [r"(?:methods?|methodology)[\s\n]+(.{200,3000}?)(?:results|experiments|discussion)"]),
        ("results",  [r"results?[\s\n]+(.{200,3000}?)(?:discussion|conclusion|references)"]),
        ("limitations", [r"(?:limitations?|threats? to validity)[\s\n]+(.{100,2000}?)(?:references|conclusion|acknowledg)"]),
    ]:
        for pat in patterns:
            m = re.search(pat, lower, re.S)
            if m:
                start = m.start(1)
                end = m.end(1)
                sections[key] = text[start:end].strip()[:1500]
                break
    return sections


def hedged_tldr(sections: dict) -> str:
    abstract = sections.get("abstract", "")
    if not abstract:
        return "Authors present new findings in their domain; see method and results below."
    sentences = re.split(r"(?<=[.!?])\s+", abstract)
    return " ".join(sentences[:2])[:400]


def build_brief(paper_id: str, vertical: str, target_industry: str = "") -> dict:
    pdf_path = PDF_DIR / f"{paper_id}.pdf"
    if not pdf_path.exists():
        health.record_build(paper_id, "missing_pdf", detail=str(pdf_path))
        return {"error": "missing_pdf", "paper_id": paper_id}
    text = extract_pdf(pdf_path)
    if len(text) < 500:
        health.record_build(paper_id, "extract_failed",
                            detail=f"extracted {len(text)} chars (< 500)")
        return {"error": "extract_failed", "paper_id": paper_id}
    sections = sectionize(text)
    tldr = hedged_tldr(sections)

    BRIEFS_DIR.mkdir(parents=True, exist_ok=True)
    brief_path = BRIEFS_DIR / f"{paper_id}.md"
    brief_path.write_text(f"""# {paper_id}

**Vertical:** {vertical}

## TL;DR
{tldr}

## What's new
- {sections.get('abstract', '')[:300]}

## Why it matters for {target_industry or vertical}
The methodology and results below may shift how practitioners approach this area.

## Methodology in plain English
{sections.get('method', 'Method section not clearly delineated; see paper.')[:800]}

## Results
{sections.get('results', 'Results section not clearly delineated; see paper.')[:800]}

## Caveats and limitations
{sections.get('limitations', 'No explicit limitations section detected — readers should evaluate scope carefully.')[:600]}

## Practical takeaway
Consider whether the technique reported here is applicable to your current pipeline.
""")
    health.record_build(paper_id, "success", detail=f"vertical={vertical}")
    return {"paper_id": paper_id, "brief_path": str(brief_path), "vertical": vertical}


def weekly_digest(vertical: str) -> str:
    queue = storage.load("pb_queue.json", [])
    briefs_for_vertical = [q for q in queue if q.get("vertical") == vertical and not q.get("delivered")]
    if len(briefs_for_vertical) < 3:
        return ""
    lines = [f"# PaperBrief — {vertical.title()} — {datetime.now():%B %d, %Y}\n"]
    for q in briefs_for_vertical[:5]:
        bp = BRIEFS_DIR / f"{q['paper_id']}.md"
        if not bp.exists():
            continue
        lines.append("---\n")
        lines.append(bp.read_text())
        q["delivered"] = True
    storage.save("pb_queue.json", queue)
    return "\n".join(lines)


def build_queue() -> dict:
    """Generate briefs for any queued papers."""
    queue = storage.load("pb_queue.json", [])
    built = 0
    for q in queue:
        if (BRIEFS_DIR / f"{q['paper_id']}.md").exists():
            continue
        result = build_brief(q["paper_id"], q.get("vertical", "general"), q.get("industry", ""))
        if "error" not in result:
            built += 1
    return {"briefs_built": built}


def fulfill_cycle() -> dict:
    subs = storage.load("pb_subscribers.json", [])
    verticals = {s["vertical"] for s in subs if s.get("status") == "active"}
    sent = 0
    for v in verticals:
        # Count undelivered briefs for the vertical BEFORE weekly_digest marks
        # them delivered — that's the metric we want to record.
        queue = storage.load("pb_queue.json", [])
        available = sum(1 for q in queue
                        if q.get("vertical") == v and not q.get("delivered"))
        digest = weekly_digest(v)
        if not digest:
            health.record_vertical(v, available_briefs=available, sent=0,
                                   skipped=True, skip_reason="low_undelivered")
            continue
        v_sent = 0
        for s in subs:
            if s.get("status") == "active" and s.get("vertical") == v:
                result = mailer.send(AGENT_KEY, s["email"],
                                     f"PaperBrief — {v.title()} — {datetime.now():%b %d}",
                                     digest, purpose="fulfillment")
                if result.get("status") == "sent":
                    sent += 1
                    v_sent += 1
        health.record_vertical(v, available_briefs=available, sent=v_sent, skipped=False)
    return {"fulfillment_sent": sent}


def acquire_cycle() -> dict:
    leads = storage.load("pb_leads.json", [])
    sent = 0
    for lead in leads:
        if lead.get("trial_sent"):
            continue
        # Free monthly state-of-field as lead magnet
        vertical = lead.get("vertical", "ai")
        digest = weekly_digest(vertical)
        body = (
            f"Free PaperBrief sample for {vertical.title()}:\n\n"
            f"{digest[:2000] or 'New digest publishing this week.'}\n\n"
            f"Subscribe for the full weekly drop: $39/mo → paypal.me/wholesaleomniverse/39\n"
            f"Annual: $399 → paypal.me/wholesaleomniverse/399"
        )
        result = mailer.send(AGENT_KEY, lead["email"],
                             f"PaperBrief sample — {vertical.title()}",
                             body, purpose="outreach")
        if result.get("status") == "sent":
            lead["trial_sent"] = datetime.now().isoformat()
            sent += 1
    storage.save("pb_leads.json", leads)
    return {"outreach_sent": sent}


def run_full_cycle() -> dict:
    q = build_queue()
    a = acquire_cycle()
    f = fulfill_cycle()
    rev = billing.revenue_summary(AGENT_KEY)
    subs = storage.load("pb_subscribers.json", [])
    metrics.record(
        AGENT_KEY,
        products_produced=q["briefs_built"],
        outreach_sent=a["outreach_sent"],
        fulfillment_sent=f["fulfillment_sent"],
        active_subs=sum(1 for s in subs if s.get("status") == "active"),
        mrr=rev["mrr"],
        total_revenue=rev["total_paid"],
    )
    return {**q, **a, **f, **rev}
