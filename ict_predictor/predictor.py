"""
ICT Decision & Validation Matrix — turns fetched candles into a prediction.

Step 1 (session verification) and Step 2 (asset check) are enforced by the
caller (tools.py) before this module is even reached. This module owns
Steps 3–5:
  3. HTF bias & DOL       -> structure.unswept_liquidity() on the 15M frame
  4. Sweep & displacement -> structure.detect_sweep() + detect_mss() on the
                              precision (5M/1M) frame
  5. Risk profile         -> require >= MIN_RR reward:risk targeting the
                              opposing liquidity pool, else NO TRADE
"""
from __future__ import annotations

import os
from typing import Optional

from ict_predictor import structure

MIN_RR = float(os.getenv("IP_MIN_RR", "2.0"))
INVALIDATION_BUFFER_PCT = float(os.getenv("IP_INVALIDATION_BUFFER_PCT", "0.0015"))  # 0.15%
# How many precision-frame candles back to scan for a liquidity sweep. On a
# 5M chart, 12 bars covers a full hour — wide enough to catch a raid early
# in the killzone while the MSS/FVG that follows is still fresh.
SWEEP_LOOKBACK = int(os.getenv("IP_SWEEP_LOOKBACK", "12"))
# Minimum displacement-candle body, as a multiple of average bar range, for a
# Market Structure Shift to count. This is the strategy's tightest filter —
# measured on real gold it rejected ~88% of swept setups — so it needs to be
# configurable, otherwise a setting the sweep identifies cannot actually be
# backtested or traded.
DISPLACEMENT_MULT = float(os.getenv("IP_DISPLACEMENT_MULT", "1.3"))


def _nearest_fvg(gaps: list[dict], direction: str, current_price: float) -> Optional[dict]:
    """Prefer the FVG closest to (but not already fully traded through by)
    the current price, in the displacement direction."""
    candidates = [g for g in gaps if g["direction"] == direction]
    if not candidates:
        return None
    candidates.sort(key=lambda g: abs(g["mid"] - current_price))
    return candidates[0]


def build_prediction(asset: str, killzone: str, htf_candles: list[dict],
                      ltf_candles: list[dict], ltf_label: str = "5M",
                      min_rr: Optional[float] = None,
                      sweep_lookback: Optional[int] = None,
                      displacement_mult: Optional[float] = None) -> dict:
    """
    htf_candles: 15M candles, used for bias / draw-on-liquidity.
    ltf_candles: precision-entry candles (5M or 1M), used for the sweep,
                 MSS, and FVG.

    The three thresholds are injectable so the parameter sweep can vary them
    without mutating module state; passing None keeps the env-configured
    defaults, so live behaviour is unchanged.

    Returns a fully-populated report dict — see report.py for the shape.
    """
    min_rr = MIN_RR if min_rr is None else min_rr
    sweep_lookback = SWEEP_LOOKBACK if sweep_lookback is None else sweep_lookback
    displacement_mult = (DISPLACEMENT_MULT if displacement_mult is None
                         else displacement_mult)
    out = {
        "asset": asset,
        "timeframe": f"15M / {ltf_label}",
        "killzone": killzone,
        "direction": "NO TRADE",
        "confidence": "Low",
    }

    if len(htf_candles) < 10 or len(ltf_candles) < 10:
        out["reason"] = "Insufficient candle history for structure analysis"
        return out

    current_price = ltf_candles[-1]["c"]

    # --- Step 3: HTF bias & Draw on Liquidity -----------------------------
    htf_swings = structure.find_swing_points(htf_candles)
    dol = structure.unswept_liquidity(htf_candles, htf_swings)
    out["dol"] = {
        "bsl": dol["bsl"]["price"] if dol["bsl"] else None,
        "ssl": dol["ssl"]["price"] if dol["ssl"] else None,
    }
    if not dol["bsl"] and not dol["ssl"]:
        out["reason"] = "No clear unswept liquidity pool on the 15M frame"
        return out

    # --- Step 4: Sweep + displacement + MSS (precision frame) -------------
    ltf_swings = structure.find_swing_points(ltf_candles)

    sweep = None
    for level_key in ("ssl", "bsl"):
        level = dol[level_key]
        if not level:
            continue
        candidate = structure.detect_sweep(ltf_candles, level, lookback=sweep_lookback)
        if candidate:
            sweep = candidate
            break

    if not sweep:
        out["reason"] = "No liquidity sweep detected on the precision frame this cycle"
        return out
    out["sweep"] = {"level": sweep["level"], "swept": sweep["swept"], "t": sweep["t"]}

    # Opposite-side ltf swing point closest before the sweep, for the MSS break level.
    opposite_type = "high" if sweep["swept"] == "SSL" else "low"
    sweep_idx = next((i for i, c in enumerate(ltf_candles) if c["t"] == sweep["t"]), len(ltf_candles) - 1)
    prior_opposite = [s for s in ltf_swings if s["type"] == opposite_type and s["i"] <= sweep_idx]
    opposite_swing = prior_opposite[-1] if prior_opposite else None

    mss = structure.detect_mss(ltf_candles, sweep, opposite_swing,
                               displacement_mult=displacement_mult)
    if not mss:
        out["reason"] = "Sweep confirmed but no Market Structure Shift followed — awaiting displacement"
        return out
    out["mss"] = {"direction": mss["direction"], "break_level": mss["break_level"], "t": mss["t"]}

    direction = mss["direction"]  # "bullish" or "bearish"

    # --- Fair Value Gap / entry zone ---------------------------------------
    fvg_direction = "bullish" if direction == "bullish" else "bearish"
    gaps = structure.find_fvgs(ltf_candles, since_t=sweep["t"], direction=fvg_direction)
    fvg = _nearest_fvg(gaps, fvg_direction, current_price)
    if not fvg:
        out["reason"] = "MSS confirmed but no Fair Value Gap formed in the displacement leg"
        return out
    out["fvg"] = {"top": fvg["top"], "bottom": fvg["bottom"], "mid": fvg["mid"]}

    entry = fvg["mid"]

    # --- Step 5: Risk profile ----------------------------------------------
    sweep_extreme = sweep["candle"]["h"] if sweep["swept"] == "BSL" else sweep["candle"]["l"]
    buffer = entry * INVALIDATION_BUFFER_PCT
    invalidation = (sweep_extreme + buffer) if direction == "bearish" else (sweep_extreme - buffer)

    opposing_dol = dol["bsl"]["price"] if direction == "bullish" and dol["bsl"] else \
                   dol["ssl"]["price"] if direction == "bearish" and dol["ssl"] else None

    risk = abs(entry - invalidation)
    if risk <= 0:
        out["reason"] = "Degenerate risk (entry == invalidation) — skipping"
        return out

    if opposing_dol is not None:
        reward = abs(opposing_dol - entry)
    else:
        reward = 0.0

    rr = reward / risk if risk else 0.0
    out["risk_reward"] = round(rr, 2)

    if rr < min_rr:
        out["direction"] = "NO TRADE"
        out["reason"] = (
            f"Setup valid but R:R only {rr:.2f}:1 targeting opposing liquidity "
            f"(minimum {min_rr:.0f}:1 required)"
        )
        return out

    # --- Confidence score ----------------------------------------------------
    avg_rng = structure.avg_range(ltf_candles)
    body = abs(mss["candle"]["c"] - mss["candle"]["o"])
    displacement_score = min(body / avg_rng, 3.0) / 3.0 if avg_rng else 0.0
    rr_score = min(rr / 4.0, 1.0)
    fvg_tightness = 1.0 - min(abs(fvg["top"] - fvg["bottom"]) / entry / 0.01, 1.0)
    score = 0.45 * displacement_score + 0.35 * rr_score + 0.20 * max(fvg_tightness, 0)
    if score >= 0.70:
        confidence = "High (75%+)"
    elif score >= 0.45:
        confidence = "Medium (60%)"
    else:
        confidence = "Low"

    out.update({
        "direction": "LONG" if direction == "bullish" else "SHORT",
        "confidence": confidence,
        "entry_zone": {"low": min(fvg["top"], fvg["bottom"]), "high": max(fvg["top"], fvg["bottom"])},
        "entry": round(entry, 2),
        "target": round(opposing_dol, 2),
        "invalidation": round(invalidation, 2),
        "current_price": round(current_price, 2),
    })
    return out
