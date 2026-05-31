"""
ModBot — autonomous comment moderation classifier.
Revenue: $97/mo per account, $297/mo team (5 accounts), $497 one-time deep audit.

Input:
  data/cm_inputs/{slug}.json   — list of comments to classify, shape:
    {
      "account": "@yourbrand",
      "platform": "instagram|tiktok|youtube|linkedin|x|reddit",
      "comments": [
        {"id": "...", "author": "@user", "text": "...", "url": "..."}
      ]
    }

Output:
  data/cm_outputs/{slug}.json — decisions per comment:
    {action: "hide"|"reply"|"flag"|"leave", confidence: 0-1, reason: "...",
     suggested_reply: "..."}  (suggested_reply only when action == "reply")

Engine:
  - Heuristic rules (always on): link spam, all-caps shouting, repeated chars,
    profanity, scam tells (DM/whatsapp/telegram), promotional URL spam,
    questions (warrants reply), positive engagement (worth a like-reply).
  - Optional Claude escalation for ambiguous comments (when present).

Bulk reply templates per platform live in REPLY_TEMPLATES — tuned for the
brand persona of the parent business.
"""
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from autonomous import storage, mailer, billing, metrics

AGENT_KEY = "modbot"
INPUTS_DIR = Path(__file__).parent.parent / "data" / "cm_inputs"
OUTPUTS_DIR = Path(__file__).parent.parent / "data" / "cm_outputs"

URL_RE = re.compile(r"https?://\S+|\bwww\.\S+\b|\b\w+\.(com|net|io|co|me|app|xyz)\b/?\S*", re.I)
EMAIL_RE = re.compile(r"\b\w+@\w+\.\w+\b")
PROFANITY = {
    "fuck", "shit", "bitch", "asshole", "cunt", "dick", "fag", "retard",
    "nigger", "kike", "spic", "chink",
}
SCAM_TELLS = {
    "dm me", "message me", "whatsapp", "telegram", "click my bio",
    "free money", "guaranteed returns", "double your", "crypto investment",
    "binary options", "earn $", "make $", "work from home", "join my channel",
}
POSITIVE_MARKERS = {
    "love this", "amazing", "great post", "thank you", "needed this",
    "saving this", "exactly", "this helped", "underrated", "👏", "🔥", "💯",
}
QUESTION_MARKERS = {
    "how", "what", "where", "when", "why", "is there", "does", "can you", "?",
}

REPLY_TEMPLATES = {
    "question": "Great question — short version: {hook}. Want me to DM you the longer breakdown?",
    "positive": "Appreciate this! Glad it landed.",
    "share": "Thanks for sharing — drop the link if you write it up, would love to read.",
}


def _has_any(text: str, terms) -> bool:
    t = text.lower()
    return any(term in t for term in terms)


def _scrub_text(text: str) -> str:
    """For length-based heuristics ignore links and emojis."""
    return URL_RE.sub("", EMAIL_RE.sub("", text))


def classify(comment: dict) -> dict:
    raw = comment.get("text", "") or ""
    text = raw.strip()
    scrubbed = _scrub_text(text)
    lower = text.lower()
    reasons = []
    confidence = 0.6

    # Hard hides
    if any(p in lower.split() for p in PROFANITY):
        return {"action": "hide", "confidence": 0.95,
                "reason": "explicit profanity / slur", "suggested_reply": ""}
    if _has_any(lower, SCAM_TELLS):
        return {"action": "hide", "confidence": 0.9,
                "reason": "scam tells (DM/crypto/earn money)", "suggested_reply": ""}
    urls = URL_RE.findall(text)
    if len(urls) >= 2:
        return {"action": "hide", "confidence": 0.9,
                "reason": "multiple URLs — likely spam", "suggested_reply": ""}
    if urls and len(scrubbed.strip()) < 15:
        return {"action": "hide", "confidence": 0.85,
                "reason": "URL with no substantive comment", "suggested_reply": ""}
    if re.search(r"(.)\1{6,}", text):
        return {"action": "hide", "confidence": 0.8,
                "reason": "spam character repetition", "suggested_reply": ""}
    if text.isupper() and len(text) > 12:
        reasons.append("all-caps shouting")
        confidence = 0.6
        return {"action": "flag", "confidence": confidence,
                "reason": "; ".join(reasons), "suggested_reply": ""}

    # Positive / engagement triggers
    if _has_any(lower, POSITIVE_MARKERS):
        return {"action": "reply", "confidence": 0.85,
                "reason": "positive engagement", "suggested_reply": REPLY_TEMPLATES["positive"]}

    # Questions
    if "?" in text or any(text.lower().startswith(q) for q in QUESTION_MARKERS):
        return {"action": "reply", "confidence": 0.8,
                "reason": "user question — reply increases engagement",
                "suggested_reply": REPLY_TEMPLATES["question"].format(hook="[[fill in 1-sentence answer]]")}

    # Default
    return {"action": "leave", "confidence": 0.55,
            "reason": "no rule matched — leave for human review",
            "suggested_reply": ""}


def _claude_escalate(comment: dict) -> dict:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return {}
    try:
        import anthropic
        client = anthropic.Anthropic()
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=120,
            messages=[{
                "role": "user",
                "content": (
                    "Classify this social-media comment for moderation. Reply in JSON with "
                    "fields: action (hide|reply|flag|leave), reason (short), suggested_reply "
                    "(only if action=reply, else empty).\n\n"
                    f"Comment: {comment.get('text','')[:500]}\n"
                    f"Author: {comment.get('author','')}\n"
                    f"Platform: {comment.get('platform','')}\n\n"
                    "Reply with valid JSON only."
                ),
            }],
        )
        raw = msg.content[0].text.strip()
        m = re.search(r"\{.*\}", raw, re.S)
        return json.loads(m.group(0)) if m else {}
    except Exception:
        return {}


def classify_batch(batch: dict, escalate_ambiguous: bool = True) -> dict:
    comments = batch.get("comments", [])
    platform = batch.get("platform", "")
    out = []
    counts = {"hide": 0, "reply": 0, "flag": 0, "leave": 0}
    for c in comments:
        c = dict(c)
        c.setdefault("platform", platform)
        decision = classify(c)
        if (escalate_ambiguous and decision["action"] == "leave"
                and decision["confidence"] < 0.6):
            extra = _claude_escalate(c)
            if extra:
                decision = {**decision, **extra, "escalated": True}
        counts[decision["action"]] = counts.get(decision["action"], 0) + 1
        out.append({"id": c.get("id"), "author": c.get("author"),
                    "text": c.get("text", "")[:280], **decision})
    return {"account": batch.get("account"), "platform": platform,
            "decisions": out, "counts": counts,
            "total": len(comments)}


def build_queue() -> dict:
    INPUTS_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    processed = 0
    total_comments = 0
    counts_total = {"hide": 0, "reply": 0, "flag": 0, "leave": 0}
    for path in sorted(INPUTS_DIR.glob("*.json")):
        slug = path.stem
        out_path = OUTPUTS_DIR / f"{slug}.json"
        if out_path.exists():
            continue
        try:
            batch = json.loads(path.read_text())
        except Exception:
            continue
        result = classify_batch(batch)
        out_path.write_text(json.dumps(result, indent=2))
        processed += 1
        total_comments += result["total"]
        for k, v in result["counts"].items():
            counts_total[k] = counts_total.get(k, 0) + v
    return {"batches_processed": processed,
            "comments_classified": total_comments,
            **{f"action_{k}": v for k, v in counts_total.items()}}


def fulfill_cycle() -> dict:
    subs = storage.load("cm_subscribers.json", [])
    log = storage.load("cm_delivery_log.json", {})
    sent = 0
    for sub in subs:
        if sub.get("status") != "active":
            continue
        email = sub.get("email")
        if not email:
            continue
        already = set(log.get(email, []))
        new = [p for p in OUTPUTS_DIR.glob("*.json") if p.name not in already]
        if not new:
            continue
        body_parts = [f"Hi {sub.get('name', 'there')},\n",
                      f"{len(new)} new moderation batch(es) classified:\n"]
        for p in new[:5]:
            try:
                r = json.loads(p.read_text())
                body_parts.append(
                    f"\n  {p.stem}: {r['total']} comments — "
                    f"hide:{r['counts'].get('hide',0)} reply:{r['counts'].get('reply',0)} "
                    f"flag:{r['counts'].get('flag',0)} leave:{r['counts'].get('leave',0)}"
                )
            except Exception:
                continue
        body = "\n".join(body_parts) + "\n\nFull decisions in data/cm_outputs/\n"
        r = mailer.send(AGENT_KEY, email,
                        f"Moderation results — {len(new)} new batches",
                        body, purpose="fulfillment")
        if r.get("status") == "sent":
            log[email] = list(already | {p.name for p in new})
            sent += 1
    storage.save("cm_delivery_log.json", log)
    return {"fulfillment_sent": sent}


def acquire_cycle() -> dict:
    leads = storage.load("cm_leads.json", [])
    sent = 0
    for lead in leads:
        if lead.get("trial_sent"):
            continue
        email = lead.get("email")
        if not email:
            continue
        body = (
            f"Hi {lead.get('name', 'there')},\n\n"
            f"ModBot classifies your incoming social-media comments into hide / reply / "
            f"flag / leave with a suggested reply for engagement-worthy ones. Spam, scams, "
            f"and slurs auto-hide. Real questions get a draft reply. Real fans get a 'nice' "
            f"acknowledgement so they keep showing up.\n\n"
            f"Drop your last 100 comments (export from IG/TikTok/YouTube) and you'll get back "
            f"a full classification report — free first batch.\n\n"
            f"Pricing:\n"
            f"  $97/mo per account\n"
            f"  $297/mo for 5 accounts (team rate)\n"
            f"  $497 one-time deep audit (your full comment history)\n\n"
            f"Reply with an export and I'll process it.\n"
        )
        r = mailer.send(AGENT_KEY, email,
                        "Free moderation pass on your last 100 comments",
                        body, purpose="outreach")
        if r.get("status") == "sent":
            lead["trial_sent"] = datetime.now().isoformat()
            sent += 1
    storage.save("cm_leads.json", leads)
    return {"outreach_sent": sent}


def run_full_cycle() -> dict:
    q = build_queue()
    a = acquire_cycle()
    f = fulfill_cycle()
    rev = billing.revenue_summary(AGENT_KEY)
    subs = storage.load("cm_subscribers.json", [])
    metrics.record(
        AGENT_KEY,
        products_produced=q["batches_processed"],
        outreach_sent=a["outreach_sent"],
        fulfillment_sent=f["fulfillment_sent"],
        active_subs=sum(1 for s in subs if s.get("status") == "active"),
        mrr=rev["mrr"],
        total_revenue=rev["total_paid"],
    )
    return {**q, **a, **f, **rev}
