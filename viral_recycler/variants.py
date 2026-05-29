"""
A/B/C variant generation (Descript/VidIQ-class).

Produces N versions of the same Short with different hooks, captions
positions, and color grades — owner can test which performs best.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shortsforge.tools import HOOK_PATTERNS, KEYWORDS_BY_NICHE


GRADE_BY_NICHE = {
    "motivational": "cinematic",
    "comedy":       "vivid",
    "wellness":     "teal_orange",
}


def make_variants(base_text: str, niche: str, count: int = 3) -> list:
    """Return `count` variant briefs (hook + grade + caption-position)."""
    hooks = HOOK_PATTERNS.get(niche, HOOK_PATTERNS["motivational"])
    variants = []
    positions = ["bottom", "top", "middle"]
    grades = ["cinematic", "vivid", "teal_orange", "vintage"]
    import re, random
    first_words = [w for w in base_text.split() if len(w) > 3][:3]
    topic = first_words[0].lower() if first_words else "this"
    fills = {k: topic for k in ("topic","struggle","bad_habit","situation",
                                "character","action","trait","context",
                                "body_part","practice","condition")}
    selected = random.sample(hooks, min(count, len(hooks)))
    for i, h in enumerate(selected):
        try:
            hook = h.format(**fills)
        except KeyError:
            hook = h
        variants.append({
            "variant_id": chr(65 + i),
            "hook":        hook,
            "color_grade": grades[i % len(grades)],
            "caption_position": positions[i % len(positions)],
            "hashtags":   [f"#{k}" for k in KEYWORDS_BY_NICHE.get(niche, [])[:5]],
        })
    return variants
