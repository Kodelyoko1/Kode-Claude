"""
Lead motivation scoring via Claude.

For the real-estate / asset-acquisition lead-gen funnel, the seller's free-text
answers ("Why are you selling?", "Condition?", "Timeline?") carry most of the
signal. We push the answers + lightweight metadata into a small Claude call
and ask for a Hot/Warm/Cold tier plus the specific phrases that drove the call.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

log = logging.getLogger("media_buyer.scoring")

# Per CLAUDE.md, the project uses claude-sonnet-4-6 for deal analysis. Sticking
# with the same family for consistency; bump to opus only if quality drops.
SCORING_MODEL = os.getenv("MB_SCORING_MODEL", "claude-sonnet-4-6")
SCORING_MAX_TOKENS = 400


SYSTEM_PROMPT = """\
You are scoring inbound real-estate seller leads for a wholesale buyer. Output
strict JSON only. Schema:
{
  "tier": "Hot" | "Warm" | "Cold",
  "urgency_signals": [string, ...],   // phrases the seller actually used
  "objection_signals": [string, ...], // things that lower motivation
  "reason": string                    // 1-sentence explanation, no marketing language
}
Tier rules:
- Hot:  explicit time pressure (foreclosure, divorce, relocation, inherited, "ASAP", <30 days) OR willingness to discount for speed
- Warm: motivated but flexible timeline (3-6 months), repair burden mentioned, tired-landlord signals
- Cold: testing the market, wants retail price, no problem to solve
Be conservative — bias toward Cold when signal is ambiguous."""


def _ensure_anthropic():
    try:
        from anthropic import Anthropic
    except ImportError as e:
        raise RuntimeError("anthropic SDK not installed (pip install anthropic)") from e
    return Anthropic()


def score_lead(lead: dict[str, Any]) -> dict[str, Any]:
    """Return {tier, urgency_signals, objection_signals, reason}.

    `lead` should contain at least: name, phone, email, property_address, and the
    free-text answers under whatever keys came back from the Meta Instant Form.
    """
    client = _ensure_anthropic()

    user_block = "Lead intake answers (JSON):\n" + json.dumps(
        {k: v for k, v in lead.items() if k not in ("id", "created_time")},
        indent=2, default=str,
    )

    resp = client.messages.create(
        model=SCORING_MODEL,
        max_tokens=SCORING_MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_block}],
    )
    raw = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()

    # Defensive parse — model usually returns clean JSON, but strip code fences if present.
    if raw.startswith("```"):
        raw = raw.strip("`").lstrip("json").strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        log.warning("Scoring model returned non-JSON; defaulting to Cold. raw=%r", raw[:200])
        return {"tier": "Cold", "urgency_signals": [], "objection_signals": [],
                "reason": "scoring_parse_failed"}
