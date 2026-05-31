"""
Creative Iteration Agent — analyzes top-performing creatives and drafts 3 new
variations to seed the next refresh batch.

Two prompt layers:
1. ANALYST prompt: ingest the top-N performing ads + their copy and extract the
   underlying "angle" (problem-centric for lead-gen, benefit/visual-hook for
   e-com). Output: a short structured pattern report.
2. WRITER prompt (prompt-within-a-prompt): take the analyst's pattern report and
   draft 3 variations of {hook, body, headline, primary_text}.

This is a generator only — output is returned to the caller. The caller (cron
runner) decides whether to email-for-review or auto-upload via meta_api.create_ad_creative.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from .monitor import Metrics

log = logging.getLogger("media_buyer.generator")

ANALYST_MODEL = os.getenv("MB_ANALYST_MODEL", "claude-sonnet-4-6")
WRITER_MODEL = os.getenv("MB_WRITER_MODEL", "claude-sonnet-4-6")


# ─────────────────────────── Prompts ───────────────────────────
ANALYST_SYSTEM = """\
You are a direct-response media buyer reviewing what made the top-performing ads
win. Your goal is to extract the *transferable pattern* — not the surface copy.

Given a list of winning ads (each with copy + their performance metrics), output
strict JSON:
{
  "winning_angle": string,              // 1 sentence — what emotional/practical lever is being pulled
  "pattern_elements": [string, ...],    // the structural building blocks (e.g. "problem-first hook", "specific timeline", "social proof number")
  "voice": string,                      // tone / register description
  "avoid": [string, ...]                // patterns from the loser set that fell flat
}
Be concrete. "uses urgency" is weak; "names a specific 14-day window" is useful."""


WRITER_SYSTEM_LEAD_GEN = """\
You are writing Meta Lead-Ad copy for a real-estate wholesale buyer targeting
motivated sellers. The buyer pays cash, closes in 14 days, and buys houses
as-is regardless of condition.

Given the analyst's pattern report, draft 3 distinct variations. Each variation
follows the winning pattern but probes a different angle (e.g. avoid-repairs vs.
relocation vs. inheritance fatigue).

Output strict JSON:
{
  "variations": [
    {
      "hook":         string,   // first 1-2 lines, must stop the scroll
      "primary_text": string,   // 2-4 sentence body for the Instant Form ad
      "headline":     string,   // <= 40 chars
      "cta":          "LEARN_MORE" | "GET_QUOTE" | "SIGN_UP"
    },
    ... (3 total)
  ]
}
Forbidden: emojis, all-caps lines, exclamation marks beyond one per variation.
Bias toward concrete numbers (days, dollar ranges) over adjectives."""


WRITER_SYSTEM_ECOM = """\
You are writing Meta product-ad copy for a high-velocity e-commerce funnel
(smart-home tech / functional wellness beverages / niche personal care). The
ads run on a purchase objective with the Pixel + CAPI deduplicated.

Given the analyst's pattern report, draft 3 distinct variations. Each variation
leads with a different visual/benefit hook (e.g. before-after, demo moment,
unboxing surprise) while preserving the underlying winning pattern.

Output strict JSON (same schema as lead-gen). CTA options: "SHOP_NOW" |
"LEARN_MORE" | "ORDER_NOW". Keep claims defensible — no "best in the world"."""


# ─────────────────────────── Client + parse helpers ───────────────────────────
def _client():
    from anthropic import Anthropic
    return Anthropic()


def _parse_json(raw: str) -> dict:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    return json.loads(raw)


# ─────────────────────────── Public API ───────────────────────────
def analyze_top_performers(top_ads: list[dict], loser_ads: list[dict] | None = None) -> dict:
    """Given ads + their metrics + their copy, extract the winning pattern.

    Each ad dict shape: {
      "ad_id": str, "name": str, "copy": {"hook": ..., "primary_text": ..., "headline": ...},
      "metrics": {...selected fields from Metrics...}
    }
    """
    client = _client()
    user = "WINNERS:\n" + json.dumps(top_ads, indent=2, default=str)
    if loser_ads:
        user += "\n\nLOSERS (for contrast, do not imitate):\n" + json.dumps(loser_ads, indent=2, default=str)

    resp = client.messages.create(
        model=ANALYST_MODEL, max_tokens=600,
        system=ANALYST_SYSTEM,
        messages=[{"role": "user", "content": user}],
    )
    raw = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    try:
        return _parse_json(raw)
    except json.JSONDecodeError:
        log.warning("Analyst returned non-JSON: %r", raw[:300])
        return {"winning_angle": "", "pattern_elements": [], "voice": "", "avoid": []}


def draft_variations(pattern_report: dict, *, kind: str, n: int = 3) -> list[dict]:
    """Draft n new ad creative variations following the winning pattern."""
    client = _client()
    system = WRITER_SYSTEM_LEAD_GEN if kind == "lead_gen" else WRITER_SYSTEM_ECOM
    user = (f"Analyst report:\n{json.dumps(pattern_report, indent=2)}\n\n"
            f"Draft {n} variations as specified.")
    resp = client.messages.create(
        model=WRITER_MODEL, max_tokens=900,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    raw = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    try:
        return _parse_json(raw).get("variations", [])
    except json.JSONDecodeError:
        log.warning("Writer returned non-JSON: %r", raw[:300])
        return []


def refresh_batch_for(ads: list[Metrics], copies_by_ad_id: dict[str, dict], *,
                      kind: str, top_k: int = 5) -> dict:
    """End-to-end: pick top-K + bottom-K by the kind's primary metric, analyze, draft.

    Returns {"pattern": ..., "variations": [...]}.
    """
    def primary_metric(m: Metrics) -> float:
        if kind == "lead_gen":
            # rank by efficiency: low CPL is best; punt None to infinity
            return m.cpl if m.cpl is not None else float("inf")
        # ecom — high ROAS is best, sort ascending then reverse below
        return -(m.roas or 0.0)

    # Only include ads that have the metric we're actually ranking on.
    metric_attr = "cpl" if kind == "lead_gen" else "roas"
    sortable = [m for m in ads if getattr(m, metric_attr) is not None]
    sortable.sort(key=primary_metric)
    top = sortable[:top_k]
    losers = sortable[-top_k:][::-1]

    top_dicts = [{
        "ad_id": m.object_id,
        "name": m.object_name,
        "copy": copies_by_ad_id.get(m.object_id, {}),
        "metrics": {"cpl": m.cpl, "roas": m.roas, "cpp": m.cpp, "frequency": m.frequency},
    } for m in top]
    loser_dicts = [{
        "ad_id": m.object_id,
        "name": m.object_name,
        "copy": copies_by_ad_id.get(m.object_id, {}),
        "metrics": {"cpl": m.cpl, "roas": m.roas, "frequency": m.frequency},
    } for m in losers]

    pattern = analyze_top_performers(top_dicts, loser_dicts)
    variations = draft_variations(pattern, kind=kind, n=3)
    return {"pattern": pattern, "variations": variations}
