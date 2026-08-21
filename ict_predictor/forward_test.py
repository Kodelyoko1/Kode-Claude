"""
Score the agent's own live predictions against what price actually did.

WHY THIS IS WORTH MORE THAN ANOTHER BACKTEST
--------------------------------------------
A backtest can be tuned until it looks good; that is what the parameter sweep
exists to expose. These predictions were made and logged BEFORE the price
data that resolves them existed, so they are out-of-sample by construction and
no amount of tuning can reach back and improve them. That makes this the only
number here that cannot be talked up.

It also audits the backtest itself. The backtest assumes a limit order fills
whenever price trades through the level, that stops fill through the level
while targets fill at it, and that a bar containing both resolves as a loss.
Those are assumptions, not observations. Resolution here runs through the
EXACT same code path (backtest._resolve_trade with the same Costs), so a
forward record that disagrees sharply with the backtest is evidence the fill
model is wrong rather than the strategy.

WHAT IT DOES NOT DO
-------------------
It resolves against bar data, not against real fills. In dry-run nothing was
ever sent to a broker, so this measures whether the LEVELS were right, not
whether an order would have been filled at them in practice. Slippage,
requotes, and rejections stay outside what this can see.

DEDUPING MATTERS MORE THAN IT LOOKS
-----------------------------------
The agent re-runs every 15 minutes inside a killzone and re-emits the same
setup each cycle. The broker-side exposure check stops those becoming
duplicate ORDERS, but the prediction log has no such guard, so one setup can
appear a dozen times. Counting each appearance as a trade would multiply a
single outcome into a dozen correlated ones and make any result look far more
significant than it is. Setups are therefore keyed on the structural event
that produced them (the MSS timestamp), and only the first sighting counts.

RESULTS ARE PERSISTED, DELIBERATELY
-----------------------------------
data/ip_predictions.json is a ROLLING log - it trims to the newest N entries.
Without a separate store, evidence would be silently destroyed by log rotation
exactly as it became statistically useful. Resolved outcomes are written to
data/ip_forward_results.json and are never trimmed.
"""
from __future__ import annotations

import os
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from autonomous import storage
from ict_predictor.backtest import Costs, Trade, _resolve_trade, significance

RESULTS_FILE = "ip_forward_results.json"
PREDICTIONS_FILE = "ip_predictions.json"

# Precision frame used to resolve trades - the same one the live agent enters
# on, so fills are judged at the resolution the setup was designed for.
RESOLVE_INTERVAL = os.getenv("IP_FWD_INTERVAL", "5m")
_INTERVAL_SEC = {"1m": 60, "5m": 300, "15m": 900}

# Ceiling on how many bars to request when resolving. A year of 5M bars is
# ~75k, which is inside a default terminal's maxbars.
MAX_FETCH_BARS = int(os.getenv("IP_FWD_MAX_BARS", "80000"))


def _epoch(iso: str | None) -> int:
    """ISO-8601 -> epoch seconds. Returns 0 when unparseable, which callers
    treat as 'cannot be scored' rather than as 1970."""
    if not iso:
        return 0
    try:
        dt = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return 0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def signal_id(pred: dict) -> str:
    """
    Stable identity for the SETUP, not for the log entry.

    The MSS timestamp is the structural event the whole setup hangs off, so
    two cycles that re-detect the same shift produce the same id. Falls back
    to the price levels when a prediction predates MSS timestamps being
    recorded - two genuinely different setups sharing an entry, stop AND
    target to the cent is not a case worth engineering around.
    """
    asset = pred.get("asset", "?")
    direction = pred.get("direction", "?")
    mss = pred.get("mss") or {}
    if mss.get("t"):
        return f"{asset}|{direction}|mss:{mss['t']}"
    return (f"{asset}|{direction}|lvl:{pred.get('entry')}"
            f"/{pred.get('invalidation')}/{pred.get('target')}")


def _scorable(pred: dict) -> tuple[bool, str]:
    """Can this prediction be resolved at all? Returns (ok, why-not)."""
    if pred.get("direction") not in ("LONG", "SHORT"):
        return False, "no trade signal"
    for field in ("entry", "invalidation", "target"):
        if pred.get(field) is None:
            return False, f"missing {field}"
    if pred.get("entry") == pred.get("invalidation"):
        return False, "degenerate risk"
    if not _epoch(pred.get("generated_at")):
        return False, "no usable timestamp"
    # Yahoo quotes COMEX futures while orders would go against broker spot.
    # Scoring those levels against MT5 bars compares two different
    # instruments and would produce a confident, meaningless number.
    if pred.get("price_source") == "yahoo":
        return False, "levels came from futures data, not the traded symbol"
    return True, ""


def collect(predictions: list[dict]) -> tuple[dict[str, dict], dict[str, int]]:
    """
    Reduce a raw prediction log to one entry per unique setup, oldest first.

    Returns (setups_by_id, skip_reasons). The skip tally is reported rather
    than discarded - "0 scorable setups" needs to say why.
    """
    setups: dict[str, dict] = {}
    skipped: dict[str, int] = {}
    for pred in sorted(predictions, key=lambda p: _epoch(p.get("generated_at"))):
        ok, why = _scorable(pred)
        if not ok:
            skipped[why] = skipped.get(why, 0) + 1
            continue
        sid = signal_id(pred)
        if sid in setups:
            setups[sid]["_sightings"] += 1
            continue
        entry = dict(pred)
        entry["_sightings"] = 1
        setups[sid] = entry
    return setups, skipped


def _to_trade(pred: dict) -> Trade:
    return Trade(
        direction=pred["direction"],
        signal_t=_epoch(pred["generated_at"]),
        entry=float(pred["entry"]),
        sl=float(pred["invalidation"]),
        tp=float(pred["target"]),
        rr_planned=float(pred.get("risk_reward") or 0.0),
        confidence=pred.get("confidence", "Low"),
    )


def _bars_needed(oldest_t: int) -> int:
    step = _INTERVAL_SEC.get(RESOLVE_INTERVAL, 300)
    span = max(0, int(time.time()) - oldest_t)
    return min(MAX_FETCH_BARS, int(span / step) + 500)


def resolve(setups: dict[str, dict], stored: dict, costs: Costs,
            fetch: Optional[Callable[[str, str, int], list[dict]]] = None,
            ) -> tuple[dict, list[str]]:
    """
    Resolve everything not already settled, and return (results, notes).

    A trade that filled but has not hit either level yet stays 'open_at_end'
    and is re-resolved on the next run; a settled win/loss/expired is never
    recomputed, so the record is stable even after the source log rotates.
    """
    if fetch is None:
        from ict_predictor.data_feed import get_history as fetch

    results = dict(stored)
    notes: list[str] = []

    pending = {sid: p for sid, p in setups.items()
               if results.get(sid, {}).get("outcome") in (None, "open_at_end")}
    if not pending:
        return results, notes

    by_asset: dict[str, list[str]] = {}
    for sid, pred in pending.items():
        by_asset.setdefault(pred.get("asset", "GC"), []).append(sid)

    for asset, sids in by_asset.items():
        oldest = min(_epoch(pending[s]["generated_at"]) for s in sids)
        try:
            bars = fetch(asset, RESOLVE_INTERVAL, _bars_needed(oldest))
        except Exception as exc:
            notes.append(f"{asset}: could not load history to resolve "
                         f"{len(sids)} setup(s) - {str(exc)[:110]}")
            continue
        if not bars:
            notes.append(f"{asset}: history returned no bars")
            continue

        # History that starts after the signal cannot resolve it. Saying so is
        # the difference between "no edge" and "not enough data to tell".
        #
        # The tolerance is one bar, not zero: a signal fires mid-bar, so the
        # oldest bar available can legitimately be the one that opened just
        # after it without anything being missing. Only a gap wider than that
        # means bars the trade might have filled in are absent.
        step = _INTERVAL_SEC.get(RESOLVE_INTERVAL, 300)
        first_bar = bars[0]["t"]
        for sid in sids:
            pred = pending[sid]
            signal_t = _epoch(pred["generated_at"])
            if signal_t < first_bar - step:
                notes.append(
                    f"{asset}: setup from "
                    f"{datetime.fromtimestamp(signal_t, timezone.utc):%Y-%m-%d %H:%M} "
                    f"predates the available history - cannot be scored")
                continue
            future = [b for b in bars if b["t"] > signal_t]
            trade = _resolve_trade(_to_trade(pred), future, costs)
            results[sid] = {
                "asset": asset,
                "signal_id": sid,
                "generated_at": pred["generated_at"],
                "sightings": pred.get("_sightings", 1),
                "confidence": pred.get("confidence"),
                "killzone": pred.get("killzone"),
                "rr_planned": pred.get("risk_reward"),
                "resolved_at": datetime.now(timezone.utc).isoformat(),
                **trade.to_dict(),
            }
    return results, notes


def score(results: dict, costs: Costs) -> dict:
    rows = list(results.values())
    settled = [r for r in rows if r.get("outcome") in ("win", "loss")]
    expired = [r for r in rows if r.get("outcome") == "expired"]
    open_now = [r for r in rows if r.get("outcome") == "open_at_end"]
    wins = [r for r in settled if r["outcome"] == "win"]

    rs = [float(r.get("r_multiple") or 0.0) for r in settled]
    gross = [float(r.get("r_gross") or 0.0) for r in settled]
    expectancy = statistics.fmean(rs) if rs else 0.0
    stdev = statistics.pstdev(rs) if len(rs) > 1 else 0.0

    stamps = [_epoch(r.get("generated_at")) for r in rows if r.get("generated_at")]
    return {
        "setups": len(rows),
        "settled": len(settled),
        "expired": len(expired),
        "still_open": len(open_now),
        "wins": len(wins),
        "losses": len(settled) - len(wins),
        "win_rate": (len(wins) / len(settled)) if settled else 0.0,
        # Of the orders that were placed, how many the market actually came
        # back to. This is the backtest's central fill assumption, measured.
        "fill_rate": (len(settled) + len(open_now)) / len(rows) if rows else 0.0,
        "expectancy_r": expectancy,
        "expectancy_gross_r": statistics.fmean(gross) if gross else 0.0,
        "total_r": sum(rs),
        "stdev_r": stdev,
        "significance": significance(expectancy, stdev, len(settled)),
        "costs": costs.to_dict(),
        "first_signal": min(stamps) if stamps else 0,
        "last_signal": max(stamps) if stamps else 0,
    }


def _period(summary: dict) -> str:
    a, b = summary.get("first_signal"), summary.get("last_signal")
    if not a or not b:
        return "no dated setups"
    fmt = "%Y-%m-%d"
    days = max(1, int((b - a) / 86400) + 1)
    return (f"{datetime.fromtimestamp(a, timezone.utc):{fmt}} -> "
            f"{datetime.fromtimestamp(b, timezone.utc):{fmt}} ({days} days)")


def format_report(summary: dict, skipped: dict, notes: list[str],
                  backtest_expectancy: Optional[float] = None) -> str:
    n = summary["settled"]
    lines = [
        "=" * 62,
        "ICT FORWARD TEST - the agent's own predictions, scored",
        "=" * 62,
        f"Period               : {_period(summary)}",
        f"Unique setups logged : {summary['setups']}",
        f"  resolved win/loss  : {n}",
        f"  expired unfilled   : {summary['expired']}",
        f"  still open         : {summary['still_open']}",
    ]

    if skipped:
        lines.append("")
        lines.append("--- LOG ENTRIES NOT SCORED ---")
        for why, count in sorted(skipped.items(), key=lambda kv: -kv[1]):
            lines.append(f"  {count:>6}  {why}")

    if n:
        lines += [
            "",
            "--- REALIZED (R multiples, net of costs) ---",
            f"Win rate             : {summary['win_rate']:.1%}  "
            f"({summary['wins']}W / {summary['losses']}L)",
            f"Expectancy per trade : {summary['expectancy_r']:+.3f} R",
            f"Total return         : {summary['total_r']:+.2f} R",
            f"Before costs         : {summary['expectancy_gross_r']:+.3f} R",
            f"Order fill rate      : {summary['fill_rate']:.1%} of setups were "
            f"reached by price",
        ]
        sig = summary["significance"]
        lines += ["", "--- HONEST READING ---"]
        # Below two trades there is no variance to speak of, and printing a
        # confidence interval of [+0.000, +0.000] would look like precision.
        if n >= 2:
            lines.append(
                f"Expectancy {summary['expectancy_r']:+.3f} R, 95% CI "
                f"[{sig['lo']:+.3f}, {sig['hi']:+.3f}] over {n} trades "
                f"(t={sig['t']:.2f}, p={sig['p']:.2f}).")
        if n < 30:
            lines.append(
                f"{n} forward trade{'' if n == 1 else 's'} is far too few to "
                f"conclude anything. This number is worth watching as it grows, "
                f"not acting on.")
        elif sig["significant"]:
            lines.append("This IS statistically distinguishable from zero.")
        else:
            lines.append(
                "This is NOT distinguishable from zero - the interval spans "
                "both losing and winning outcomes.")

        if backtest_expectancy is not None:
            gap = summary["expectancy_r"] - backtest_expectancy
            lines += [
                "",
                f"Backtest said {backtest_expectancy:+.3f} R/trade; forward says "
                f"{summary['expectancy_r']:+.3f} R ({gap:+.3f} difference).",
            ]
            if n >= 30 and abs(gap) > 0.5:
                lines.append(
                    "A gap that size points at the FILL MODEL rather than the "
                    "strategy - the backtest is resolving trades in a way the "
                    "market did not.")
    else:
        lines += [
            "",
            "--- NOTHING TO SCORE YET ---",
            "No forward setup has resolved. That is the expected state early on:",
            "the agent only signals inside killzones, and most killzone passes",
            "correctly produce NO TRADE. Let it accumulate.",
        ]

    if notes:
        lines.append("")
        lines.append("--- NOTES ---")
        for note in notes:
            lines.append(f"  - {note}")

    lines += [
        "",
        "Forward results are out-of-sample by construction and cannot be tuned",
        "after the fact. They are also resolved against BAR data, not real",
        "fills - in dry-run nothing reached a broker, so this measures whether",
        "the levels were right, not whether an order would have filled at them.",
        "=" * 62,
    ]
    return "\n".join(lines)


def run_forward_test(costs: Optional[Costs] = None,
                     fetch: Optional[Callable] = None,
                     backtest_expectancy: Optional[float] = None) -> dict:
    """Resolve every unsettled prediction, persist, and return the summary."""
    costs = costs or Costs()
    predictions = storage.load(PREDICTIONS_FILE, [])
    if not isinstance(predictions, list):
        predictions = []
    stored = storage.load(RESULTS_FILE, {})
    if not isinstance(stored, dict):
        stored = {}

    setups, skipped = collect(predictions)
    results, notes = resolve(setups, stored, costs, fetch=fetch)
    storage.save(RESULTS_FILE, results)

    summary = score(results, costs)
    summary["report"] = format_report(summary, skipped, notes, backtest_expectancy)
    summary["notes"] = notes
    summary["skipped"] = skipped
    return summary
