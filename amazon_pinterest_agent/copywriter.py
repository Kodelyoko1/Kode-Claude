"""
SEO copywriting engine for Amazon-product Pinterest pins.

Heuristic template renderer by default (no API key needed). Upgrades to
Claude `claude-sonnet-4-6` for the title/description draft when
ANTHROPIC_API_KEY is set — same opt-in pattern as SEOWriter/ShowNotes.

The disclosure line is NEVER left to the model — `compliance.disclosure_for()`
appends it deterministically after the draft, so a bad completion can't ever
ship a pin missing its Amazon Associates disclosure.
"""
import os
import random
import re

from .compliance import disclosure_for

# Splits a product title into its leading "primary noun phrase" for keyword/
# hashtag use — cuts at the first comma/colon/dash/pipe so a long, punctuated
# title (e.g. "Widget: Learn About Passing X") doesn't get treated as one
# giant keyword.
_TITLE_SPLIT_RE = re.compile(r"[,:\-|]")

# Hashtags can't contain spaces, punctuation, or apostrophes — strip
# everything but letters/digits so a keyword like "Driver's Permit Test"
# becomes "#DriversPermitTest" instead of a malformed tag.
_HASHTAG_STRIP_RE = re.compile(r"[^A-Za-z0-9]")

# The exact system prompt fed to Claude for the copywriting step. Keep this
# in sync with the persona documented in BLUEPRINT.md §2 — that file is the
# human-readable copy of this same text.
SYSTEM_PROMPT = """You are PinCurator, the copywriting module of Wholesale Omniverse's
Amazon Influencer x Pinterest agent. You write short-form Pinterest pin copy for
real Amazon storefront products. You are not a general marketing writer — you
operate under strict Amazon Associates and Pinterest rules, and every word you
write gets programmatically checked against them before anything is posted.

TONE
- Direct, specific, benefit-first. Sound like a person who actually uses the
  product, not an ad. No exclamation-point stacking, no emoji spam, no
  clickbait ("You won't BELIEVE...").
- Write like a helpful curator pointing someone at something useful, not a
  salesperson closing a deal.

SEO RULES
- Title: <=100 characters, leads with the primary keyword/product noun,
  no ALL CAPS, no keyword-stuffing.
- Description: <=420 characters (a disclosure line gets appended after you,
  so leave room and never write your own disclosure), keyword-rich but
  readable as a sentence a human would say out loud, ends with a soft
  call-to-action ("See current price + reviews on Amazon.").
- Use the product's stated audience/benefit/keywords fields verbatim where
  they fit naturally — don't invent claims not present in the product data.

COMPLIANCE — NON-NEGOTIABLE
- Never write "guaranteed," medical/cure claims, "FDA approved," or anything
  implying you are Amazon or an Amazon employee/partner.
- Never write your own disclosure text or hashtag — the disclosure line is
  appended automatically by the compliance module after your draft.
- Never invent product specs, reviews, or ratings not given to you.

OUTPUT FORMAT
Return exactly two lines, nothing else:
TITLE: <title text>
DESCRIPTION: <description text, no disclosure line>
"""

HOOK_TEMPLATES = [
    "{keyword} that actually {benefit}",
    "The {keyword} everyone's adding to cart",
    "{keyword}: worth it or not?",
    "Found the {keyword} for {audience}",
    "{keyword} — {benefit}",
]

CTA_TEMPLATES = [
    "See current price + reviews on Amazon.",
    "Full details and reviews on Amazon.",
    "Check why {audience} keep buying this — link in pin.",
    "Tap to see the full listing on Amazon.",
]


def _keyword_bank(product: dict) -> list:
    primary = _TITLE_SPLIT_RE.split(product.get("title", ""), 1)[0]
    base = [primary]
    base += product.get("keywords", [])
    base += [product.get("category", "")]
    seen, out = set(), []
    for k in base:
        k = k.strip()
        if k and k.lower() not in seen:
            seen.add(k.lower())
            out.append(k)
    return out


def _to_hashtag(keyword: str) -> str:
    """Strip everything but letters/digits so punctuation in a product title
    or keyword (colons, apostrophes, dashes) can't leak into a malformed
    hashtag."""
    cleaned = _HASHTAG_STRIP_RE.sub("", keyword)
    return f"#{cleaned}" if cleaned else ""


def generate_title(product: dict) -> str:
    keywords = _keyword_bank(product)
    keyword = keywords[0] if keywords else product.get("title", "Amazon find")
    template = random.choice(HOOK_TEMPLATES)
    title = template.format(
        keyword=keyword,
        benefit=product.get("benefit", "actually works"),
        audience=product.get("audience", "reviewers"),
    )
    return title[:100]


def _heuristic_body(product: dict) -> str:
    keywords = _keyword_bank(product)
    cta = random.choice(CTA_TEMPLATES).format(audience=product.get("audience", "shoppers"))
    audience = product.get("audience") or f"anyone shopping for {product.get('category', 'this')}"
    body = f"{product.get('title', '')}. {product.get('summary', '')} Great for {audience}. {cta}".strip()
    hashtags = " ".join(tag for tag in (_to_hashtag(k) for k in keywords[:6]) if tag)
    return f"{body} {hashtags}".strip()


def _claude_copy(product: dict) -> dict:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return {}
    try:
        import anthropic
        client = anthropic.Anthropic()
        user_prompt = (
            f"Product: {product.get('title')}\n"
            f"Category: {product.get('category')}\n"
            f"Summary: {product.get('summary', '')}\n"
            f"Audience: {product.get('audience', 'general shoppers')}\n"
            f"Benefit: {product.get('benefit', '')}\n"
            f"Keywords: {', '.join(product.get('keywords', []))}\n"
        )
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=300,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
        text = msg.content[0].text.strip()
        title, description = "", ""
        for line in text.splitlines():
            if line.upper().startswith("TITLE:"):
                title = line.split(":", 1)[1].strip()[:100]
            elif line.upper().startswith("DESCRIPTION:"):
                description = line.split(":", 1)[1].strip()
        if title and description:
            return {"title": title, "description": description}
    except Exception:
        pass
    return {}


def generate_pin_copy(product: dict) -> dict:
    """Return {"title": ..., "description": ...} with the disclosure already
    appended to the description — this is the only function callers need."""
    claude = _claude_copy(product)
    title = claude.get("title") or generate_title(product)
    body = (claude.get("description") or _heuristic_body(product))[:420]
    disclosure = disclosure_for(body)
    description = f"{body}\n\n{disclosure}"[:500]
    return {"title": title, "description": description}
