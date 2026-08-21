"""
Parameter sensitivity sweep for the ICT strategy.

Motivation: the shipped defaults produce ~2 signals in 5 months on real gold,
with 88% of swept setups killed at the Market Structure Shift test. Something
has to be loosened — but loosening thresholds until the numbers look good,
against the same data you then quote, is how backtests get fabricated.

This module is built to make that failure mode visible rather than easy:

1. THE WHOLE GRID IS REPORTED, including the settings that lose money.
   Cherry-picking is only possible if the losers are hidden.

2. IN-SAMPLE / OUT-OF-SAMPLE SPLIT. Parameters are ranked on the first
   portion of history and then re-run, untouched, on the held-out remainder.
   A setting that is good in-sample and bad out-of-sample was fitted to
   noise, and the report says so directly.

3. PLATEAUS BEAT PEAKS. For each setting we also report the mean expectancy
   of its immediate neighbours in the grid. A configuration that only works
   at one exact value is almost certainly noise; one that works across a
   region of nearby values is more likely to reflect something real.

4. SAMPLE SIZE GATES THE CLAIM. Any setting with too few resolved trades is
   flagged unusable no matter how attractive its expectancy looks.

Nothing here tunes anything automatically. It produces evidence; the
decision stays with a human.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable, Optional

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from ict_predictor.backtest import Costs, run_backtest

# The sweep charges the same costs as a single backtest run. Tuning against
# cost-free numbers would rank settings by how well they exploit a fill model
# nobody trades against.
_costs = Costs()

# Grid. displacement_mult is first because it is the measured bottleneck.
DEFAULT_GRID = {
    "displacement_mult": [0.6, 0.8, 1.0, 1.3, 1.6],
    "sweep_lookback": [6, 12, 24, 48],
    "min_rr": [1.5, 2.0, 3.0],
}

# Below this many resolved trades, an expectancy figure is not evidence.
MIN_TRADES_FOR_CLAIM = 30


def _split_by_time(htf: list[dict], ltf: list[dict], frac: float):
    """
    Split both frames at the SAME instant, over their common period.

    Splitting each array independently by index is wrong whenever the two
    frames cover different spans — which is the norm, since brokers hold far
    more 15M history than 5M. Doing so put the halves in different eras: the
    in-sample half ended up with bias-frame bars that had no precision-frame
    data at all, so nearly every decision was skipped and its statistics were
    meaningless, while the out-of-sample half quietly received the only
    genuinely overlapping stretch.

    Here both frames are first trimmed to their intersection, then cut at one
    shared timestamp, so the two halves are consecutive slices of the same
    tradeable history.
    """
    start = max(htf[0]["t"], ltf[0]["t"])
    end = min(htf[-1]["t"], ltf[-1]["t"])
    htf = [b for b in htf if start <= b["t"] <= end]
    ltf = [b for b in ltf if start <= b["t"] <= end]
    if not htf or not ltf:
        return [], [], [], [], (start, end, start)

    boundary = htf[int(len(htf) * frac)]["t"] if len(htf) > 1 else htf[0]["t"]
    return (
        [b for b in htf if b["t"] < boundary],
        [b for b in ltf if b["t"] < boundary],
        [b for b in htf if b["t"] >= boundary],
        [b for b in ltf if b["t"] >= boundary],
        (start, end, boundary),
    )


def run_sweep(htf_bars: list[dict], ltf_bars: list[dict], asset: str = "GC",
              grid: Optional[dict] = None, is_frac: float = 0.6,
              progress: Optional[Callable[[int, int, dict], None]] = None) -> dict:
    """
    Evaluate every combination in `grid` on the in-sample portion, then re-run
    each on the held-out remainder. Returns both sets of results plus the
    split boundary so the report can show what was and wasn't fitted.
    """
    grid = grid or DEFAULT_GRID
    combos = []
    for dm in grid["displacement_mult"]:
        for sl in grid["sweep_lookback"]:
            for rr in grid["min_rr"]:
                combos.append({"displacement_mult": dm,
                               "sweep_lookback": sl,
                               "min_rr": rr})

    htf_is, ltf_is, htf_oos, ltf_oos, bounds = _split_by_time(
        htf_bars, ltf_bars, is_frac)
    if not htf_is or not htf_oos:
        return {"rows": [], "grid": grid, "is_frac": is_frac,
                "is_bars": 0, "oos_bars": 0, "bounds": bounds,
                "error": "no common period between the 15M and 5M frames"}

    rows = []
    for n, params in enumerate(combos, 1):
        if progress:
            progress(n, len(combos), params)
        r_is = run_backtest(htf_is, ltf_is, asset=asset, params=params)
        r_oos = run_backtest(htf_oos, ltf_oos, asset=asset, params=params)
        if "error" in r_is or "error" in r_oos:
            continue
        rows.append({
            "params": params,
            "is_signals": r_is["signals_generated"],
            "is_resolved": r_is["resolved"],
            "is_expectancy": r_is["expectancy_r"],
            "is_win_rate": r_is["win_rate"],
            "is_total_r": r_is["total_r"],
            "is_max_dd": r_is["max_drawdown_r"],
            "oos_signals": r_oos["signals_generated"],
            "oos_resolved": r_oos["resolved"],
            "oos_expectancy": r_oos["expectancy_r"],
            "oos_win_rate": r_oos["win_rate"],
            "oos_total_r": r_oos["total_r"],
        })

    _attach_neighbourhood(rows, grid)
    return {"rows": rows, "grid": grid, "is_frac": is_frac,
            "is_bars": len(htf_is), "oos_bars": len(htf_oos),
            "is_ltf_bars": len(ltf_is), "oos_ltf_bars": len(ltf_oos),
            "bounds": bounds}


def _attach_neighbourhood(rows: list[dict], grid: dict) -> None:
    """
    Mean in-sample expectancy of each row's immediate grid neighbours (one
    step along any single axis). A high score whose neighbourhood is poor is
    a spike — the signature of a fitted parameter rather than a real effect.
    """
    axes = list(grid.keys())
    index = {tuple(r["params"][a] for a in axes): r for r in rows}
    for r in rows:
        key = tuple(r["params"][a] for a in axes)
        neigh = []
        for ai, axis in enumerate(axes):
            values = grid[axis]
            pos = values.index(r["params"][axis])
            for step in (-1, 1):
                p = pos + step
                if 0 <= p < len(values):
                    nk = list(key)
                    nk[ai] = values[p]
                    hit = index.get(tuple(nk))
                    if hit:
                        neigh.append(hit["is_expectancy"])
        r["neighbourhood_expectancy"] = (sum(neigh) / len(neigh)) if neigh else 0.0
        r["neighbours"] = len(neigh)


def _split_line(sweep: dict) -> str:
    from datetime import datetime, timezone
    b = sweep.get("bounds")
    if not b:
        return "Split             : (unknown)"
    start, end, boundary = b
    f = lambda ts: datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
    return (f"Split             : {f(start)} -> {f(boundary)} in-sample "
            f"({sweep['is_bars']:,} 15M / {sweep.get('is_ltf_bars', 0):,} 5M bars)"
            f"  |  {f(boundary)} -> {f(end)} held out "
            f"({sweep['oos_bars']:,} 15M / {sweep.get('oos_ltf_bars', 0):,} 5M bars)")


def format_report(sweep: dict, asset: str, period: str) -> str:
    rows = sweep["rows"]
    if not rows:
        return "Sweep produced no evaluable combinations."

    lines = [
        "=" * 100,
        f"ICT PARAMETER SENSITIVITY SWEEP — {asset}",
        "=" * 100,
        f"Period            : {period}",
        _split_line(sweep),
        f"Combinations      : {len(rows)}",
        f"Trade threshold   : {MIN_TRADES_FOR_CLAIM} resolved trades before an "
        f"expectancy is treated as evidence",
        f"Costs             : {_costs.describe()}",
        "",
        "Every expectancy below is NET of those costs, so a setting that only",
        "looks good gross is already shown losing here.",
        "",
        "Full grid, ranked by IN-SAMPLE expectancy. Losers included deliberately.",
        "",
        f"{'disp':>5} {'look':>5} {'minRR':>6} | {'IS sig':>6} {'IS trd':>6} "
        f"{'IS exp':>8} {'IS win':>7} | {'OOS sig':>7} {'OOS trd':>7} "
        f"{'OOS exp':>8} | {'nbhd':>7} {'verdict':>16}",
        "-" * 100,
    ]

    for r in sorted(rows, key=lambda x: -x["is_expectancy"]):
        p = r["params"]
        verdict = _verdict(r)
        lines.append(
            f"{p['displacement_mult']:>5.1f} {p['sweep_lookback']:>5d} "
            f"{p['min_rr']:>6.1f} | "
            f"{r['is_signals']:>6d} {r['is_resolved']:>6d} "
            f"{r['is_expectancy']:>+8.3f} {r['is_win_rate']:>6.1%} | "
            f"{r['oos_signals']:>7d} {r['oos_resolved']:>7d} "
            f"{r['oos_expectancy']:>+8.3f} | "
            f"{r['neighbourhood_expectancy']:>+7.3f} {verdict:>16}"
        )

    usable = [r for r in rows
              if r["is_resolved"] >= MIN_TRADES_FOR_CLAIM
              and r["oos_resolved"] >= MIN_TRADES_FOR_CLAIM]

    lines += ["", "=" * 100, "READING THIS", "=" * 100]
    if not usable:
        best_n = max((r["is_resolved"] for r in rows), default=0)
        lines += [
            f"NO combination reached {MIN_TRADES_FOR_CLAIM} resolved trades in BOTH",
            f"halves. The best any setting managed in-sample was {best_n}.",
            "",
            "That is the finding. It does not mean the tuning failed — it means the",
            "strategy does not fire often enough on this data for ANY parameter",
            "choice to be evaluated. Every expectancy column above is noise, and",
            "picking the top row would be selecting the luckiest coin flip.",
            "",
            "Options, in order of honesty:",
            "  1. Get more history (--bars 40000+) and re-run.",
            "  2. Loosen the structural rules themselves, not just thresholds —",
            "     e.g. allow MSS confirmation without requiring a single outsized",
            "     displacement candle, which is the step killing ~88% of setups.",
            "  3. Accept the strategy as specified is too rare to trade and treat",
            "     it as a discretionary alert rather than an automated system.",
        ]
    else:
        holds = [r for r in usable if _verdict(r) == "holds OOS"]
        failed = [r for r in usable if _verdict(r) == "FAILED OOS"]
        flipped = [r for r in usable if _verdict(r) == "sign flip"]
        neg = [r for r in usable if _verdict(r) == "negative both"]
        pool = pooled(usable)
        lines += [
            f"{len(usable)} combination(s) cleared the trade threshold in both halves:",
            f"  {len(holds):>3} positive in BOTH halves        (holds OOS)",
            f"  {len(failed):>3} positive IS, negative OOS      (classic overfitting)",
            f"  {len(flipped):>3} negative IS, positive OOS      (sign flip)",
            f"  {len(neg):>3} negative in BOTH halves",
            "",
            f"Pooled across those {len(usable)} settings: {pool['trades']:,} trades, "
            f"{pool['expectancy']:+.3f} R/trade ({pool['total_r']:+.1f} R total).",
            "",
        ]
        if holds:
            top = max(holds, key=lambda r: r["oos_expectancy"])
            p = top["params"]
            spike = top["neighbourhood_expectancy"] < 0.3 * top["is_expectancy"]
            lines += [
                f"Most durable: displacement_mult={p['displacement_mult']}, "
                f"sweep_lookback={p['sweep_lookback']}, min_rr={p['min_rr']}",
                f"  in-sample  {top['is_expectancy']:+.3f} R over {top['is_resolved']} trades",
                f"  out-sample {top['oos_expectancy']:+.3f} R over {top['oos_resolved']} trades",
                f"  neighbourhood {top['neighbourhood_expectancy']:+.3f} R "
                f"({'SPIKE — treat with suspicion' if spike else 'plateau — more credible'})",
            ]
        elif failed:
            lines += [
                "Nothing held up. Settings that looked good on the first portion lost",
                "on the held-out data — the signature of fitting noise. Do not trade",
                "any row above on the strength of this table.",
            ]
        elif flipped and not neg:
            lines += [
                "Every viable setting changed SIGN between the two halves: negative in",
                "the first period, positive in the second. That is not an edge appearing",
                "— it is the strategy being regime-dependent. A rule set with a real edge",
                "is positive in both halves; one that loses in one market and wins in the",
                "next is indistinguishable from taking the market's direction with extra",
                "steps, and you cannot know which regime comes next.",
                "",
                f"The pooled figure ({pool['expectancy']:+.3f} R over {pool['trades']:,} trades) is the",
                "fairer read, and it is close enough to zero that commission, swap and",
                "slippage — none of which are modelled here — would plausibly sink it.",
            ]
        else:
            lines += [
                "No setting was positive in both halves. The results are either negative",
                "throughout or inconsistent between periods; neither supports trading",
                "this rule set as specified.",
            ]

    lines += [
        "",
        "Regardless of the table: bar-resolution fills, no commission/swap/slippage,",
        "one instrument, one period. A sweep narrows what to test next; it does not",
        "establish an edge.",
        "=" * 100,
    ]
    return "\n".join(lines)


def _verdict(r: dict) -> str:
    """
    All four quadrants, named for what they actually mean. An earlier version
    collapsed both negative-in-sample cases into "negative", which made a run
    where every viable setting flipped from negative to positive read as
    "looked good then failed" — the opposite of the truth.
    """
    if r["is_resolved"] < MIN_TRADES_FOR_CLAIM or r["oos_resolved"] < MIN_TRADES_FOR_CLAIM:
        return "too few trades"
    pos_is, pos_oos = r["is_expectancy"] > 0, r["oos_expectancy"] > 0
    if pos_is and pos_oos:
        return "holds OOS"
    if pos_is and not pos_oos:
        return "FAILED OOS"
    if not pos_is and pos_oos:
        return "sign flip"
    return "negative both"


def pooled(rows: list[dict]) -> dict:
    """Both halves combined. A setting whose halves disagree has a pooled
    figure closer to its true long-run behaviour than either half alone."""
    n = sum(r["is_resolved"] + r["oos_resolved"] for r in rows)
    total = sum(r["is_resolved"] * r["is_expectancy"] +
                r["oos_resolved"] * r["oos_expectancy"] for r in rows)
    return {"trades": n, "total_r": total, "expectancy": (total / n) if n else 0.0}
