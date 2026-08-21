"""
Core ICT market-structure primitives: swing points, liquidity pools (DOL),
liquidity sweeps, Market Structure Shifts (MSS), and Fair Value Gaps (FVG).

Everything here is pure, deterministic price-action math over a candle list
(`[{"t","o","h","l","c","v"}, ...]`, oldest first) — no network, no LLM.
That's on purpose: the actual price levels a paying subscriber trades off
must come from real fetched data, never from a language model.
"""
from __future__ import annotations

from typing import Optional


def find_swing_points(candles: list[dict], left: int = 2, right: int = 2) -> list[dict]:
    """Fractal swing highs/lows: a bar whose high (low) is the strict local
    max (min) across `left` bars before and `right` bars after it."""
    swings = []
    n = len(candles)
    for i in range(left, n - right):
        window_h = candles[i - left:i] + candles[i + 1:i + 1 + right]
        if candles[i]["h"] > max(c["h"] for c in window_h):
            swings.append({"i": i, "t": candles[i]["t"], "price": candles[i]["h"], "type": "high"})
        window_l = candles[i - left:i] + candles[i + 1:i + 1 + right]
        if candles[i]["l"] < min(c["l"] for c in window_l):
            swings.append({"i": i, "t": candles[i]["t"], "price": candles[i]["l"], "type": "low"})
    return swings


def unswept_liquidity(candles: list[dict], swings: list[dict]) -> dict:
    """
    For each swing high/low, check whether a *later* candle's close has
    traded through it (i.e. it's been "used up"/accepted, not just wicked).
    Returns the most recent unswept BSL (swing high) and SSL (swing low) —
    the draws on liquidity a killzone raid is expected to target.
    """
    bsl = None
    ssl = None
    for s in sorted(swings, key=lambda x: x["i"], reverse=True):
        later = candles[s["i"] + 1:]
        if s["type"] == "high" and bsl is None:
            if not any(c["c"] > s["price"] for c in later):
                bsl = s
        elif s["type"] == "low" and ssl is None:
            if not any(c["c"] < s["price"] for c in later):
                ssl = s
        if bsl and ssl:
            break
    return {"bsl": bsl, "ssl": ssl}


def detect_sweep(candles: list[dict], level: dict, lookback: int = 3) -> Optional[dict]:
    """
    Did one of the last `lookback` candles wick through `level` and close
    back on the origin side? Returns the sweep candle + timestamp, or None.
    `level["type"]` is "high" (BSL, look for a bearish/upside raid) or
    "low" (SSL, look for a bullish/downside raid).
    """
    if not level:
        return None
    price = level["price"]
    for c in candles[-lookback:]:
        if level["type"] == "high" and c["h"] > price and c["c"] < price:
            return {"candle": c, "t": c["t"], "level": price, "swept": "BSL"}
        if level["type"] == "low" and c["l"] < price and c["c"] > price:
            return {"candle": c, "t": c["t"], "level": price, "swept": "SSL"}
    return None


def avg_range(candles: list[dict], n: int = 14) -> float:
    sample = candles[-n:] if len(candles) >= n else candles
    if not sample:
        return 0.0
    return sum(c["h"] - c["l"] for c in sample) / len(sample)


def detect_mss(candles: list[dict], sweep: dict, opposite_swing: Optional[dict],
                displacement_mult: float = 1.3) -> Optional[dict]:
    """
    Market Structure Shift: after a sweep, find a strong-bodied displacement
    candle in the reversal direction whose close breaks the most recent
    opposite-side swing point. `sweep["swept"]` == "SSL" -> looking for a
    bullish MSS (break above the last swing high formed before/around the
    sweep); "BSL" -> bearish MSS (break below the last swing low).
    """
    if not sweep or not opposite_swing:
        return None
    avg_rng = avg_range(candles)
    if avg_rng <= 0:
        return None

    sweep_idx = next((i for i, c in enumerate(candles) if c["t"] == sweep["t"]), None)
    if sweep_idx is None:
        return None

    direction = "bullish" if sweep["swept"] == "SSL" else "bearish"
    ref_level = opposite_swing["price"]

    for c in candles[sweep_idx:]:
        body = c["c"] - c["o"]
        is_displacement = abs(body) >= displacement_mult * avg_rng
        if direction == "bullish" and is_displacement and body > 0 and c["c"] > ref_level:
            return {"direction": "bullish", "break_level": ref_level, "t": c["t"], "candle": c}
        if direction == "bearish" and is_displacement and body < 0 and c["c"] < ref_level:
            return {"direction": "bearish", "break_level": ref_level, "t": c["t"], "candle": c}
    return None


def find_fvgs(candles: list[dict], since_t: Optional[int] = None, direction: Optional[str] = None) -> list[dict]:
    """
    3-candle Fair Value Gaps: for candles (a, b, c) consecutive, a bullish
    FVG exists when a.high < c.low (the gap is [a.high, c.low]); a bearish
    FVG when a.low > c.high (the gap is [c.high, a.low]).
    Only considers gaps formed at/after `since_t` (the displacement leg)
    and optionally filters to one direction.
    """
    gaps = []
    for i in range(1, len(candles) - 1):
        a, b, c = candles[i - 1], candles[i], candles[i + 1]
        if since_t is not None and b["t"] < since_t:
            continue
        if a["h"] < c["l"] and direction != "bearish":
            gaps.append({
                "direction": "bullish", "top": c["l"], "bottom": a["h"],
                "mid": (c["l"] + a["h"]) / 2, "t": b["t"],
            })
        if a["l"] > c["h"] and direction != "bullish":
            gaps.append({
                "direction": "bearish", "top": a["l"], "bottom": c["h"],
                "mid": (a["l"] + c["h"]) / 2, "t": b["t"],
            })
    return gaps
