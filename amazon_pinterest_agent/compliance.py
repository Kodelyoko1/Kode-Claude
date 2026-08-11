"""
Compliance & anti-spam guardrails for the Amazon Influencer <> Pinterest agent.

Every pin is run through `check_pin()` before it is ever dispatched. This is
the module that keeps the agent inside:
  - FTC / Amazon Associates Operating Agreement disclosure requirements
  - Amazon's direct-linking policy (tagged, uncloaked amazon.com/amzn.to links)
  - Pinterest spam/shadowban-avoidance heuristics (cadence, hashtag caps,
    duplicate-image throttling)

Nothing here calls out to a network — it's pure validation over the pin dict
and the agent's own dispatch history, so it can run on every cycle for free.
"""
import hashlib
import os
import re
import time

# Amazon's own influencer guidance: on character-constrained / real-time posts
# there often isn't room for the full sentence, so "#CommissionsEarned" alone
# is accepted. Anything with more room should carry the full-sentence form.
SHORT_DISCLOSURE = "#CommissionsEarned"
FULL_DISCLOSURE = "As an Amazon Associate I earn from qualifying purchases. #ad"

# Claims that read as deceptive, medical, or as false Amazon affiliation —
# any of these get a pin auto-blocked rather than auto-fixed.
BANNED_CLAIM_PATTERNS = [
    r"\bguaranteed\b",
    r"\bcures?\b",
    r"\bfda[\s-]?approved\b",
    r"\bofficial amazon\b",
    r"\bamazon partner\b",
    r"\bamazon employee\b",
]

MAX_HASHTAGS = 20
MIN_SECONDS_BETWEEN_PINS = int(os.environ.get("AP_MIN_SECONDS_BETWEEN_PINS", str(20 * 60)))
MAX_SAME_IMAGE_REUSE_DAYS = int(os.environ.get("AP_IMAGE_REUSE_COOLDOWN_DAYS", "14"))


def disclosure_for(body_text: str, limit: int = 500) -> str:
    """Pick the disclosure string that fits the remaining character budget.
    Prefer the full sentence whenever there's room for it."""
    if len(body_text) + len(FULL_DISCLOSURE) + 2 <= limit:
        return FULL_DISCLOSURE
    return SHORT_DISCLOSURE


def _has_disclosure(text: str) -> bool:
    lowered = text.lower()
    return (
        SHORT_DISCLOSURE.lower() in lowered
        or "amazon associate" in lowered
        or "#ad" in lowered
    )


def _is_direct_amazon_link(link: str) -> bool:
    """Amazon requires tagged, un-cloaked links back to amazon.<tld> — or the
    Associates-issued amzn.to shortlink, which Amazon itself controls."""
    if "amzn.to/" in link:
        return True
    return bool(re.search(r"amazon\.[a-z.]{2,10}/.*\btag=", link))


def check_pin(pin: dict, recent_pins: list) -> dict:
    """Validate one pin dict {title, description, link, image_url, asin}
    against disclosure, linking, and anti-spam rules.

    `recent_pins` is the agent's own dispatch log (data/ap_pins.json) so
    cadence + duplicate-image checks are self-contained.

    Returns {"ok": bool, "violations": [...], "image_hash": str}.
    """
    violations = []

    title = pin.get("title", "")
    description = pin.get("description", "")
    link = pin.get("link", "")
    text = f"{title} {description}"

    if not _has_disclosure(text):
        violations.append("missing_disclosure")

    if not _is_direct_amazon_link(link):
        violations.append("untagged_or_uncloaked_link")

    for pattern in BANNED_CLAIM_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            violations.append(f"banned_claim:{pattern}")

    hashtags = re.findall(r"#\w+", description)
    if len(hashtags) > MAX_HASHTAGS:
        violations.append("too_many_hashtags")

    # Posting cadence — space pins out so the account doesn't read as automated.
    dispatched_only = [p for p in recent_pins if p.get("status") == "posted"]
    if dispatched_only:
        last_ts = dispatched_only[-1].get("dispatched_at_epoch", 0)
        if time.time() - last_ts < MIN_SECONDS_BETWEEN_PINS:
            violations.append("posting_too_fast")

    image_hash = hashlib.sha1(pin.get("image_url", "").encode()).hexdigest()
    cooldown = MAX_SAME_IMAGE_REUSE_DAYS * 86400
    for p in dispatched_only:
        if p.get("image_hash") == image_hash and time.time() - p.get("dispatched_at_epoch", 0) < cooldown:
            violations.append("duplicate_image_too_soon")
            break

    return {"ok": not violations, "violations": violations, "image_hash": image_hash}
