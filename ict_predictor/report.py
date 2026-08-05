"""
Renders a prediction dict (from predictor.build_prediction) into the exact
AUTONOMOUS ICT PREDICTION AGENT REPORT template, plus a heuristic or
Claude-assisted 2-sentence execution summary.

Claude is only ever used to *phrase* the rationale from numbers the rule
engine already computed — it never invents a price level.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone, timedelta

ASSET_LABELS = {"GC": "GC (Gold)", "CL": "CL (Crude Oil)"}


def _hhmm_est(t: int | None) -> str:
    if not t:
        return "--:--"
    dt = datetime.fromtimestamp(t, tz=timezone.utc) - timedelta(hours=5)
    return dt.strftime("%H:%M EST")


def _fmt(v, decimals: int = 2) -> str:
    if v is None:
        return "N/A"
    return f"{v:,.{decimals}f}"


def _dol_line(pred: dict) -> str:
    dol = pred.get("dol") or {}
    parts = []
    if dol.get("bsl") is not None:
        parts.append(f"BSL at {_fmt(dol['bsl'])}")
    if dol.get("ssl") is not None:
        parts.append(f"SSL at {_fmt(dol['ssl'])}")
    return "Target " + " / ".join(parts) if parts else "No unswept liquidity identified"


def _sweep_line(pred: dict) -> str:
    sweep = pred.get("sweep")
    if not sweep:
        return "None this cycle"
    return f"Swept {sweep['swept']} at {_fmt(sweep['level'])} at {_hhmm_est(sweep['t'])}"


def _mss_line(pred: dict) -> str:
    mss = pred.get("mss")
    if not mss:
        return "Not confirmed"
    verb = "above" if mss["direction"] == "bullish" else "below"
    return f"Confirmed {mss['direction'].capitalize()} MSS {verb} {_fmt(mss['break_level'])}"


def _fvg_line(pred: dict) -> str:
    fvg = pred.get("fvg")
    if not fvg:
        return "No FVG formed"
    direction = "Bullish" if pred.get("direction") == "LONG" else "Bearish"
    lo, hi = sorted((fvg["bottom"], fvg["top"]))
    return f"{direction} FVG between {_fmt(lo)} - {_fmt(hi)}"


def _heuristic_summary(pred: dict) -> str:
    if pred["direction"] == "NO TRADE":
        return (
            f"{pred.get('reason', 'Setup criteria not met this cycle')}. "
            f"No entry is issued — re-evaluate on the next killzone pass."
        )
    sweep = pred.get("sweep", {})
    mss = pred.get("mss", {})
    return (
        f"Liquidity was raided at {_fmt(sweep.get('level'))} before an aggressive "
        f"{mss.get('direction', '')} displacement broke {_fmt(mss.get('break_level'))}, "
        f"confirming a {pred['direction'].lower()} bias into the {pred.get('asset')} FVG. "
        f"Risk:reward to the opposing liquidity pool is {pred.get('risk_reward', 0):.2f}:1, "
        f"clearing the 1:2 minimum for submission."
    )


def _claude_summary(pred: dict) -> str:
    if not os.environ.get("ANTHROPIC_API_KEY") or pred["direction"] == "NO TRADE":
        return ""
    try:
        import anthropic
        client = anthropic.Anthropic()
        prompt = (
            "You are writing the EXECUTION_SUMMARY line of an ICT (Inner Circle Trader) "
            "prediction report. Using ONLY the facts below, write exactly 2 concise "
            "sentences explaining the trade rationale. Do not invent any price, level, "
            "or time not given below. No preamble, no markdown.\n\n"
            f"Asset: {pred.get('asset')}\n"
            f"Direction: {pred.get('direction')}\n"
            f"Killzone: {pred.get('killzone')}\n"
            f"Sweep: {_sweep_line(pred)}\n"
            f"MSS: {_mss_line(pred)}\n"
            f"FVG entry: {_fvg_line(pred)}\n"
            f"Target: {_fmt(pred.get('target'))}\n"
            f"Invalidation: {_fmt(pred.get('invalidation'))}\n"
            f"Risk:Reward: {pred.get('risk_reward')}:1\n"
        )
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        text = msg.content[0].text.strip()
        return text or ""
    except Exception:
        return ""


def execution_summary(pred: dict) -> str:
    return _claude_summary(pred) or _heuristic_summary(pred)


def format_report(pred: dict) -> str:
    asset_label = ASSET_LABELS.get(pred.get("asset"), pred.get("asset", "N/A"))
    direction = pred.get("direction", "NO TRADE")

    lines = [
        "=" * 50,
        "🤖 AUTONOMOUS ICT PREDICTION AGENT REPORT",
        "=" * 50,
        f"• Target Asset: {asset_label}",
        f"• Timeframe Analyzed: {pred.get('timeframe', 'N/A')}",
        f"• Active Session/Killzone: {pred.get('killzone') or 'None — outside killzone'}",
        "",
        "--- SETUP ANALYSIS ---",
        f"1. Draw on Liquidity (DOL): {_dol_line(pred)}",
        f"2. Sweep Identified: {_sweep_line(pred)}",
        f"3. Market Structure Shift (MSS): {_mss_line(pred)}",
        f"4. Entry Zone / FVG: {_fvg_line(pred)}",
        "",
        "--- UPSIDEONLY PREDICTION DIRECTIVE ---",
        f"• PREDICTION DIRECTION: {direction}",
        f"• CONFIDENCE LEVEL: {pred.get('confidence', 'Low')}",
        f"• DIRECTIONAL TARGET: {_fmt(pred.get('target'))}",
        f"• INVALIDATION LEVEL: {_fmt(pred.get('invalidation'))}",
        f"• EXECUTION SUMMARY: {execution_summary(pred)}",
        "=" * 50,
    ]
    return "\n".join(lines)


def no_trade_report(asset: str, reason: str) -> dict:
    """Build the Step-1 short-circuit prediction dict when outside every killzone."""
    return {
        "asset": asset,
        "timeframe": "15M / 5M",
        "killzone": None,
        "direction": "NO TRADE",
        "confidence": "Low",
        "reason": reason,
    }
