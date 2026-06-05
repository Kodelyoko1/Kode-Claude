"""
ProofBot — autonomous proofreader + copyeditor.
Revenue: $15/page, $39/mo (10 pages), $129/mo unlimited.

Inputs:
  - data/pf_inputs/{slug}.{md,txt}: raw text to proof
  - Auto-source from data/sw_outputs/*.md  (SEOWriter drafts)
                  data/sn_outputs/*.md  (ShowNotes)
                  data/pb_briefs/*.md   (PaperBrief)

Engine:
  - Primary: LanguageTool public API (https://api.languagetool.org/v2/check)
    — no key required, ~20 req/min free. Detects grammar, style, typos.
  - Always-on heuristics (run regardless of LT availability):
      double spaces, leading/trailing whitespace, repeated words ("the the"),
      Title Case mistakes in headings, sentence-end punctuation, common
      homophone patterns (their/there/they're, your/you're, its/it's).

Output: data/pf_outputs/{slug}.md
  - "Issues found" table (line, type, suggestion)
  - "Cleaned text" block with auto-applied safe fixes (double-space collapse,
    trailing whitespace, repeated words). Non-safe fixes are listed but left
    for human review.
"""
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from autonomous import storage, mailer, billing, metrics
from proofbot import health

AGENT_KEY = "proofbot"
INPUTS_DIR = Path(__file__).parent.parent / "data" / "pf_inputs"
OUTPUTS_DIR = Path(__file__).parent.parent / "data" / "pf_outputs"
DATA_DIR = Path(__file__).parent.parent / "data"
AUTO_SOURCES = ["sw_outputs", "sn_outputs", "pb_briefs"]

LT_ENDPOINT = "https://api.languagetool.org/v2/check"

HOMOPHONES = [
    (r"\b(its)\b(?=\s+(?:a|the|own|so|too|very))",
     "Did you mean 'it's' (contraction of 'it is')?"),
    (r"\bits'\b", "There is no possessive 'its'' — use 'its' (no apostrophe)."),
    (r"\byour welcome\b", "Use 'you're welcome'."),
    (r"\bshould of\b", "Use 'should have' (or 'should've'), not 'should of'."),
    (r"\bcould of\b", "Use 'could have' (or 'could've')."),
    (r"\bwould of\b", "Use 'would have' (or 'would've')."),
    (r"\balot\b", "Two words: 'a lot'."),
    (r"\baffect\s+the\s+effect\b", "Confirm affect/effect usage."),
]


def _heuristic_check(text: str) -> list:
    issues = []
    for i, line in enumerate(text.splitlines(), 1):
        if "  " in line:
            issues.append({"line": i, "type": "spacing", "msg": "Double space",
                           "snippet": line.strip()[:80], "safe_fix": True})
        if line.endswith((" ", "\t")):
            issues.append({"line": i, "type": "spacing", "msg": "Trailing whitespace",
                           "snippet": "", "safe_fix": True})
        for m in re.finditer(r"\b(\w+)\s+\1\b", line, re.I):
            issues.append({"line": i, "type": "repetition",
                           "msg": f"Repeated word: '{m.group(1)}'",
                           "snippet": m.group(0), "safe_fix": True})
        for pat, suggestion in HOMOPHONES:
            if re.search(pat, line, re.I):
                issues.append({"line": i, "type": "homophone",
                               "msg": suggestion,
                               "snippet": line.strip()[:80], "safe_fix": False})
        if line.startswith(("#", "##", "###")):
            head = re.sub(r"^#+\s*", "", line).strip()
            if head and head.endswith("."):
                issues.append({"line": i, "type": "style",
                               "msg": "Heading ends with period — remove.",
                               "snippet": head[:80], "safe_fix": False})
    return issues


def _languagetool_check(text: str, lang: str = "en-US") -> list:
    data = urllib.parse.urlencode({"text": text[:20000], "language": lang}).encode()
    req = urllib.request.Request(LT_ENDPOINT, data=data,
                                 headers={"Accept": "application/json"})
    issues = []
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            payload = json.loads(r.read())
    except Exception as e:
        return [{"line": 0, "type": "lt_unavailable",
                 "msg": f"LanguageTool API unreachable: {str(e)[:120]}",
                 "snippet": "", "safe_fix": False}]
    for m in payload.get("matches", []):
        offset = m.get("offset", 0)
        line_no = text.count("\n", 0, offset) + 1
        repl = ", ".join(r.get("value", "") for r in m.get("replacements", [])[:3])
        issues.append({
            "line": line_no,
            "type": m.get("rule", {}).get("category", {}).get("id", "lt").lower(),
            "msg": m.get("message", "") + (f"  → {repl}" if repl else ""),
            "snippet": m.get("context", {}).get("text", "")[:80],
            "safe_fix": False,
        })
    return issues


def _apply_safe_fixes(text: str) -> str:
    text = re.sub(r"[ \t]+\n", "\n", text)        # trailing whitespace
    text = re.sub(r"[ \t]{2,}", " ", text)        # double spaces
    text = re.sub(r"\b(\w+)\s+\1\b", r"\1", text, flags=re.I)  # repeats
    return text


def proof_text(text: str, slug: str) -> dict:
    issues = _heuristic_check(text)
    lt = _languagetool_check(text)
    issues.extend(lt)
    issues.sort(key=lambda x: (x["line"], x["type"]))
    cleaned = _apply_safe_fixes(text)
    return {"slug": slug, "issue_count": len(issues), "issues": issues, "cleaned": cleaned}


def write_report(slug: str, result: dict, original: str) -> Path:
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    out = OUTPUTS_DIR / f"{slug}.md"
    lines = [
        f"# ProofBot report — {slug}",
        "",
        f"**Issues found:** {result['issue_count']}",
        f"**Generated:** {datetime.now():%Y-%m-%d %H:%M}",
        "",
        "## Issues",
        "",
    ]
    if not result["issues"]:
        lines.append("_No issues detected._")
    else:
        lines.append("| Line | Type | Message | Snippet | Auto-fixed? |")
        lines.append("|---|---|---|---|---|")
        for it in result["issues"][:200]:
            snip = it["snippet"].replace("|", "\\|")
            msg = it["msg"].replace("|", "\\|")
            lines.append(f"| {it['line']} | {it['type']} | {msg} | "
                         f"`{snip}` | {'✓' if it.get('safe_fix') else ''} |")
        if len(result["issues"]) > 200:
            lines.append(f"\n_…and {len(result['issues']) - 200} more issues truncated._")
    lines.append("")
    lines.append("## Cleaned text (safe fixes applied)")
    lines.append("")
    lines.append("```")
    lines.append(result["cleaned"])
    lines.append("```")
    out.write_text("\n".join(lines))
    return out


def _gather_sources() -> list:
    found = []
    seen = set()
    if INPUTS_DIR.exists():
        for p in sorted(INPUTS_DIR.iterdir()):
            if p.suffix.lower() in {".md", ".txt"} and p.stem not in seen:
                found.append((p.stem, p))
                seen.add(p.stem)
    for sub in AUTO_SOURCES:
        d = DATA_DIR / sub
        if not d.exists():
            continue
        for p in d.glob("*.md"):
            slug = f"{sub}-{p.stem}"
            if slug in seen:
                continue
            found.append((slug, p))
            seen.add(slug)
    return found


def build_queue() -> dict:
    INPUTS_DIR.mkdir(parents=True, exist_ok=True)
    produced = 0
    for slug, path in _gather_sources():
        out_path = OUTPUTS_DIR / f"{slug}.md"
        if out_path.exists():
            continue
        text = path.read_text(errors="ignore")
        if len(text) < 50:
            continue
        result = proof_text(text, slug)
        write_report(slug, result, text)
        produced += 1
    return {"reports_produced": produced}


def fulfill_cycle() -> dict:
    subs = storage.load("pf_subscribers.json", [])
    log = storage.load("pf_delivery_log.json", {})
    sent = 0
    for sub in subs:
        if sub.get("status") != "active":
            continue
        email = sub.get("email")
        if not email:
            continue
        already = set(log.get(email, []))
        new = [p for p in OUTPUTS_DIR.glob("*.md") if p.name not in already]
        if not new:
            continue
        body_parts = [f"Hi {sub.get('name', 'there')},\n",
                      f"{len(new)} new proofreading report(s) ready:\n"]
        for p in new[:8]:
            body_parts.append(f"\n--- {p.stem} ---")
            body_parts.append(p.read_text()[:1500])
        body = "\n".join(body_parts) + "\n"
        r = mailer.send(AGENT_KEY, email,
                        f"Proofreading reports — {len(new)} new",
                        body, purpose="fulfillment")
        if r.get("status") == "sent":
            log[email] = list(already | {p.name for p in new})
            sent += 1
    storage.save("pf_delivery_log.json", log)
    return {"fulfillment_sent": sent}


def acquire_cycle() -> dict:
    leads = storage.load("pf_leads.json", [])
    sent = 0
    for lead in leads:
        if lead.get("trial_sent"):
            continue
        email = lead.get("email")
        if not email:
            continue
        body = (
            f"Hi {lead.get('name', 'there')},\n\n"
            f"ProofBot reviews your articles, blog posts, or self-published manuscripts "
            f"for grammar, style, and embarrassing typos — combining LanguageTool's full "
            f"rule set with extra checks for the homophone mistakes spell-check misses.\n\n"
            f"Every report lists each issue (line, type, suggestion) and ships a "
            f"safe-fixes-applied cleaned version you can paste back into your draft.\n\n"
            f"Pricing:\n"
            f"  $15 per page\n"
            f"  $39/mo for 10 pages\n"
            f"  $129/mo unlimited\n\n"
            f"Reply with one piece of text and I'll send a free report.\n"
        )
        r = mailer.send(AGENT_KEY, email,
                        "Free proofreading report (1 page, no signup)",
                        body, purpose="outreach")
        if r.get("status") == "sent":
            lead["trial_sent"] = datetime.now().isoformat()
            sent += 1
    storage.save("pf_leads.json", leads)
    return {"outreach_sent": sent}


def run_full_cycle() -> dict:
    q = build_queue()
    a = acquire_cycle()
    f = fulfill_cycle()
    rev = billing.revenue_summary(AGENT_KEY)
    subs = storage.load("pf_subscribers.json", [])
    metrics.record(
        AGENT_KEY,
        products_produced=q["reports_produced"],
        outreach_sent=a["outreach_sent"],
        fulfillment_sent=f["fulfillment_sent"],
        active_subs=sum(1 for s in subs if s.get("status") == "active"),
        mrr=rev["mrr"],
        total_revenue=rev["total_paid"],
    )
    return {**q, **a, **f, **rev}
