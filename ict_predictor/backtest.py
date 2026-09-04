"""
Walk-forward backtest for the ICT strategy.

The point of this module is to answer the only question that matters: does
the rule set have an edge, or is it just running correctly?

Design constraints, in order of importance:

1. NO LOOK-AHEAD. At each decision point the predictor is handed only bars
   that had closed by that moment. This is the single easiest way for a
   backtest to lie, so the window slicing is strict and asserted.

2. The same code path as live. Signals come from predictor.build_prediction
   and structure.*, not a reimplementation — otherwise the backtest measures
   a strategy that isn't the one being traded.

3. Pessimistic fills. Entries are pending limit orders, so a trade only
   counts if price actually traded through the entry. When a single bar
   spans both stop and target we assume the STOP hit first — the true
   sequence is unknowable at bar resolution, and the pessimistic reading is
   the honest one.

4. Costs are charged, not waved away. Spread on entry, commission per round
   turn, slippage on stop exits, and swap on every night held. Gross and net
   expectancy are both reported so the drag is a measured number rather than
   a footnote.

5. Orders expire like the live ones do (ORDER_TIME_DAY), so an unfilled
   setup does not linger and get credited days later.

Results carry the sample size prominently. A high win rate over 9 trades is
noise, and the report says so.
"""
from __future__ import annotations

import os
import statistics
import sys
from bisect import bisect_right
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from ict_predictor import killzone, instruments
from ict_predictor.predictor import build_prediction

# --- Cost model ---------------------------------------------------------
# Every figure below is in PRICE UNITS of the instrument (dollars per ounce
# for gold, dollars per barrel for crude, quote-currency-per-unit for FX —
# e.g. ~0.00012 is about 1.2 pips on a 5-decimal pair), because that is the
# unit the fills are in. Converting a broker's per-lot quote is one division
# by the contract size: $7 round-turn commission on a 100 oz gold lot is
# 7/100 = 0.07.
#
# A $0.30 spread is a sane retail number for gold; on a 1.08-scale FX pair
# it would be a nonsensical 3,000-pip spread. So these globals are ONLY the
# explicit-override path — env vars a user sets to force one number across
# every asset. The actual per-run default is asset-aware: Costs() falls back
# to each instrument's own bt_spread/bt_slippage/bt_swap_per_night (see
# instruments.py) unless the matching IP_BT_* env var is explicitly set.
#
# Defaults are retail-realistic rather than flattering. Charging spread alone
# was the largest remaining overstatement in this backtest: an intraday
# strategy risking only a few price units per trade gives up a real slice of
# every R to costs, and an edge already sitting inside its own error bars
# cannot afford that slice.
_SPREAD_OVERRIDDEN = "IP_BT_SPREAD" in os.environ
DEFAULT_SPREAD = float(os.getenv("IP_BT_SPREAD", "0.30"))
# Round-turn commission. Zero on a typical "spread only" retail account,
# ~0.07 on a raw-spread gold account that charges per lot instead. There's no
# reliable "typical" per-asset default worth guessing here, so unlike the
# other three costs this one has no per-instrument fallback — it stays 0
# unless explicitly set.
DEFAULT_COMMISSION = float(os.getenv("IP_BT_COMMISSION", "0.0"))
# Adverse slippage on STOP exits only. Entries are pending limit orders: they
# slip by failing to fill, not by filling worse, and unfilled setups are
# already counted as "expired". Stops are market orders and do get filled
# through the level - especially on the displacement moves this strategy
# deliberately trades into.
_SLIPPAGE_OVERRIDDEN = "IP_BT_SLIPPAGE" in os.environ
DEFAULT_SLIPPAGE = float(os.getenv("IP_BT_SLIPPAGE", "0.10"))
# Overnight financing per night held, charged in both directions. A short
# usually earns where a long pays, and one signed number cannot model both, so
# the pessimistic reading charges either way. Setups are intraday and orders
# expire same-day, so this only touches trades whose exit lands in a later
# session.
_SWAP_OVERRIDDEN = "IP_BT_SWAP" in os.environ
DEFAULT_SWAP_PER_NIGHT = float(os.getenv("IP_BT_SWAP", "0.15"))


class Costs:
    """Everything charged against a trade beyond the raw price move.

    Resolution order for spread/slippage/swap, per field:
      1. explicit constructor argument
      2. IP_BT_SPREAD / IP_BT_SLIPPAGE / IP_BT_SWAP env var, if the user set it
      3. the `asset`'s own default from instruments.py
      4. the original gold-shaped hardcoded constant (only reached for an
         asset missing from the registry)
    """

    __slots__ = ("spread", "commission", "slippage", "swap_per_night")

    def __init__(self, spread=None, commission=None, slippage=None,
                 swap_per_night=None, asset: str = "GC"):
        meta = instruments.get(asset)
        self.spread = spread if spread is not None else (
            DEFAULT_SPREAD if _SPREAD_OVERRIDDEN else meta.get("bt_spread", DEFAULT_SPREAD))
        self.commission = DEFAULT_COMMISSION if commission is None else float(commission)
        self.slippage = slippage if slippage is not None else (
            DEFAULT_SLIPPAGE if _SLIPPAGE_OVERRIDDEN else meta.get("bt_slippage", DEFAULT_SLIPPAGE))
        self.swap_per_night = swap_per_night if swap_per_night is not None else (
            DEFAULT_SWAP_PER_NIGHT if _SWAP_OVERRIDDEN
            else meta.get("bt_swap_per_night", DEFAULT_SWAP_PER_NIGHT))

    def to_dict(self) -> dict:
        return {"spread": self.spread, "commission": self.commission,
                "slippage": self.slippage, "swap_per_night": self.swap_per_night}

    def describe(self) -> str:
        return (f"spread {self.spread:g} on entry, commission {self.commission:g} "
                f"round-turn, slippage {self.slippage:g} on stop exits, swap "
                f"{self.swap_per_night:g}/night (all in price units)")
# Bars of history the predictor needs before it can map structure.
WARMUP_BARS = int(os.getenv("IP_BT_WARMUP", "60"))
# Trailing precision-frame bars handed to the predictor. It only maps recent
# structure, so anything beyond this was being copied and discarded.
LTF_WINDOW_BARS = int(os.getenv("IP_BT_LTF_WINDOW", "300"))
# Trailing BIAS-frame bars. This is a modelling choice, not just an
# optimisation, so it is stated plainly: find_swing_points() rescans its whole
# input, and the input grew with every bar, making the replay quadratic in
# history length — 81 hours for a full sweep at ~18k bars. Bounding it also
# reflects the strategy's own logic: a liquidity level from ~3 weeks back is
# not the intraday draw on liquidity ICT targets, and an old swing that price
# never closed through is vanishingly rare. Measured against an unbounded
# window, 1000 bars (~10 trading days on 15M) changes 0.5% of decisions.
# Raise it to trade fidelity for time; IP_BT_HTF_WINDOW=0 disables the bound.
HTF_WINDOW_BARS = int(os.getenv("IP_BT_HTF_WINDOW", "1000"))
# Forward bars scanned when resolving a trade. These are 5M bars, so 1440 is
# ~5 days — far more than an intraday setup needs, since orders expire the
# same day and anything still unresolved is reported as "open_at_end".
FORWARD_BARS = int(os.getenv("IP_BT_FORWARD", "1440"))


class Trade:
    __slots__ = ("direction", "signal_t", "entry", "sl", "tp", "rr_planned",
                 "confidence", "fill_t", "exit_t", "exit_price", "outcome",
                 "r_multiple", "r_gross", "cost_r")

    def __init__(self, direction, signal_t, entry, sl, tp, rr_planned, confidence):
        self.direction = direction
        self.signal_t = signal_t
        self.entry = entry
        self.sl = sl
        self.tp = tp
        self.rr_planned = rr_planned
        self.confidence = confidence
        self.fill_t: Optional[int] = None
        self.exit_t: Optional[int] = None
        self.exit_price: Optional[float] = None
        self.outcome: str = "expired"       # expired | win | loss
        self.r_multiple: float = 0.0        # net of every cost
        self.r_gross: float = 0.0           # same fills, costs switched off
        self.cost_r: float = 0.0            # r_gross - r_multiple

    def to_dict(self, decimals: int = 2) -> dict:
        return {
            "direction": self.direction,
            "signal_time": datetime.fromtimestamp(self.signal_t, tz=timezone.utc).isoformat(),
            "entry": round(self.entry, decimals),
            "sl": round(self.sl, decimals),
            "tp": round(self.tp, decimals),
            "rr_planned": self.rr_planned,
            "confidence": self.confidence,
            "outcome": self.outcome,
            "r_multiple": round(self.r_multiple, 3),
            "r_gross": round(self.r_gross, 3),
            "cost_r": round(self.cost_r, 3),
        }


def _same_day(a: int, b: int) -> bool:
    da = datetime.fromtimestamp(a, tz=timezone.utc).date()
    db = datetime.fromtimestamp(b, tz=timezone.utc).date()
    return da == db


# Brokers charge swap at the 17:00 New York rollover, which is 22:00 UTC in
# winter and 21:00 in summer. Anchoring on 22:00 year-round is close enough for
# a cost that only bites the rare trade held past a session boundary, and the
# error is at most one night in the pessimistic direction.
_ROLLOVER_UTC_HOUR = 22


def _nights_held(fill_t: Optional[int], exit_t: Optional[int]) -> int:
    """How many daily rollovers the position was open across."""
    if not fill_t or not exit_t or exit_t <= fill_t:
        return 0
    shift = _ROLLOVER_UTC_HOUR * 3600
    return max(0, (exit_t - shift) // 86400 - (fill_t - shift) // 86400)


def _resolve_trade(trade: Trade, future_bars: list[dict], costs: Costs) -> Trade:
    """
    Walk forward bar by bar and decide what actually happened.

    Fill: a BUY limit fills when the low trades down through entry (a SELL
    limit when the high trades up through it). The spread is charged by
    filling a buy at entry+spread and a sell at entry-spread.

    Exit: whichever of stop/target the price reaches first. If one bar
    contains both, count it as a LOSS — at bar resolution the order is
    unknowable, and crediting the win would flatter the result. A target is a
    resting limit and fills at its level; a stop is a market order and fills
    through it, so slippage is charged on losses only.

    Commission and swap are subtracted from the resulting P&L rather than
    folded into a price, since neither moves where the trade actually filled.
    """
    is_long = trade.direction == "LONG"
    fill_price = trade.entry + costs.spread if is_long else trade.entry - costs.spread

    filled = False
    for bar in future_bars:
        if not filled:
            # Order expires at end of the signal's day, matching ORDER_TIME_DAY.
            if not _same_day(trade.signal_t, bar["t"]):
                trade.outcome = "expired"
                return trade
            touched = bar["l"] <= trade.entry if is_long else bar["h"] >= trade.entry
            if touched:
                filled = True
                trade.fill_t = bar["t"]
                # A bar that fills can also resolve; fall through to check it.
            else:
                continue

        hit_sl = bar["l"] <= trade.sl if is_long else bar["h"] >= trade.sl
        hit_tp = bar["h"] >= trade.tp if is_long else bar["l"] <= trade.tp

        if hit_sl:  # checked first: pessimistic when both occur in one bar
            trade.outcome = "loss"
            slipped = (trade.sl - costs.slippage if is_long
                       else trade.sl + costs.slippage)
            trade.exit_t, trade.exit_price = bar["t"], slipped
            break
        if hit_tp:
            trade.outcome = "win"
            trade.exit_t, trade.exit_price = bar["t"], trade.tp
            break

    if trade.outcome in ("win", "loss") and trade.exit_price is not None:
        risk = abs(fill_price - trade.sl)
        if risk > 0:
            pnl = ((trade.exit_price - fill_price) if is_long
                   else (fill_price - trade.exit_price))
            pnl -= costs.commission
            pnl -= costs.swap_per_night * _nights_held(trade.fill_t, trade.exit_t)
            trade.r_multiple = pnl / risk
        # Cost-free benchmark on the same fills, so the report can show the
        # drag as a measured number instead of asserting one.
        planned_risk = abs(trade.entry - trade.sl)
        if planned_risk > 0:
            raw_exit = trade.tp if trade.outcome == "win" else trade.sl
            raw = ((raw_exit - trade.entry) if is_long
                   else (trade.entry - raw_exit))
            trade.r_gross = raw / planned_risk
            trade.cost_r = trade.r_gross - trade.r_multiple
    elif filled:
        # Filled but neither level reached before data ran out.
        trade.outcome = "open_at_end"
    return trade


def run_backtest(htf_bars: list[dict], ltf_bars: list[dict], asset: str = "GC",
                 spread: Optional[float] = None,
                 progress: Optional[Callable[[int, int], None]] = None,
                 params: Optional[dict] = None,
                 costs: Optional[Costs] = None) -> dict:
    """
    Replay history bar-by-bar. `htf_bars` (15M) drive the decision clock;
    `ltf_bars` (5M) supply precision entries and trade resolution.
    Both must be sorted oldest-first.

    `costs` carries the full trading-cost model; `spread` remains accepted on
    its own for callers that only want to vary that one term.
    """
    if costs is None:
        costs = Costs(spread=spread, asset=asset)
    elif spread is not None:
        costs.spread = float(spread)
    if len(htf_bars) < WARMUP_BARS + 10:
        return {"error": f"need > {WARMUP_BARS + 10} HTF bars, got {len(htf_bars)}"}

    trades: list[Trade] = []
    signals_seen = 0
    killzone_bars = 0
    open_until = 0  # no new signal while a trade is still live
    # Where the Decision & Validation Matrix rejects setups. Without this the
    # only visible outcome is "no trades", which can't distinguish a properly
    # selective strategy from one whose thresholds make it never fire.
    funnel = {"no_liquidity": 0, "no_sweep": 0, "no_mss": 0,
              "no_fvg": 0, "rr_too_low": 0, "other": 0}

    # Timestamps hoisted out so each decision can binary-search its window.
    # Rebuilding the slices with a linear scan per decision made the loop
    # O(decisions x len(ltf_bars)): ~72 billion element visits across a full
    # 60-cell sweep, which is hours rather than minutes.
    ltf_times = [b["t"] for b in ltf_bars]

    total = len(htf_bars)
    for i in range(WARMUP_BARS, total):
        if progress and i % 200 == 0:
            progress(i, total)

        decision_t = htf_bars[i]["t"]
        now = datetime.fromtimestamp(decision_t, tz=timezone.utc)

        kz = killzone.current_killzone(now)
        if not killzone.asset_active_in_killzone(asset, kz):
            continue
        killzone_bars += 1

        if decision_t <= open_until:
            continue  # mirrors the live duplicate/exposure guard

        # --- strict no-look-ahead slicing -------------------------------
        # bisect_right returns the first index STRICTLY after decision_t, so
        # ltf_bars[:cut] is exactly the set of bars that had closed by then —
        # the same result the linear filter produced, without rescanning.
        cut = bisect_right(ltf_times, decision_t)
        if cut < 20:
            continue
        htf_start = max(0, i + 1 - HTF_WINDOW_BARS) if HTF_WINDOW_BARS else 0
        htf_window = htf_bars[htf_start:i + 1]
        ltf_window = ltf_bars[max(0, cut - LTF_WINDOW_BARS):cut]
        assert htf_window[-1]["t"] <= decision_t
        assert ltf_window[-1]["t"] <= decision_t

        pred = build_prediction(asset, kz, htf_window, ltf_window, "5M",
                                **(params or {}))
        if pred.get("direction") not in ("LONG", "SHORT"):
            reason = (pred.get("reason") or "").lower()
            if "liquidity pool" in reason:
                funnel["no_liquidity"] += 1
            elif "sweep" in reason and "no liquidity sweep" in reason:
                funnel["no_sweep"] += 1
            elif "market structure shift" in reason:
                funnel["no_mss"] += 1
            elif "fair value gap" in reason:
                funnel["no_fvg"] += 1
            elif "r:r" in reason:
                funnel["rr_too_low"] += 1
            else:
                funnel["other"] += 1
            continue
        signals_seen += 1

        trade = Trade(pred["direction"], decision_t, pred["entry"],
                      pred["invalidation"], pred["target"],
                      pred.get("risk_reward", 0), pred.get("confidence", ""))
        # Bounded forward slice. Orders expire same-day and trades resolve at
        # SL/TP, so a few days of bars is far more than any trade consumes;
        # anything still unresolved inside it is reported "open_at_end" either
        # way, exactly as before.
        future = ltf_bars[cut:cut + FORWARD_BARS]
        trade = _resolve_trade(trade, future, costs)
        trades.append(trade)
        if trade.exit_t:
            open_until = trade.exit_t
        elif trade.fill_t:
            open_until = trade.fill_t

    from ict_predictor import predictor as _p
    eff = {"displacement_mult": (params or {}).get("displacement_mult", _p.DISPLACEMENT_MULT),
           "sweep_lookback":    (params or {}).get("sweep_lookback", _p.SWEEP_LOOKBACK),
           "min_rr":            (params or {}).get("min_rr", _p.MIN_RR)}
    out = _summarize(trades, signals_seen, killzone_bars, costs, funnel)
    out["params_used"] = (f"displacement_mult={eff['displacement_mult']} "
                          f"sweep_lookback={eff['sweep_lookback']} "
                          f"min_rr={eff['min_rr']}")
    return out


def _summarize(trades: list[Trade], signals: int, kz_bars: int, costs: Costs,
               funnel: dict | None = None) -> dict:
    resolved = [t for t in trades if t.outcome in ("win", "loss")]
    wins = [t for t in resolved if t.outcome == "win"]
    losses = [t for t in resolved if t.outcome == "loss"]
    expired = [t for t in trades if t.outcome == "expired"]

    rs = [t.r_multiple for t in resolved]
    gross_win = sum(t.r_multiple for t in wins)
    gross_loss = abs(sum(t.r_multiple for t in losses))

    equity, peak, max_dd = 0.0, 0.0, 0.0
    curve = []
    for t in resolved:
        equity += t.r_multiple
        curve.append(round(equity, 3))
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)

    expectancy = statistics.fmean(rs) if rs else 0.0
    stdev = statistics.pstdev(rs) if len(rs) > 1 else 0.0
    gross_rs = [t.r_gross for t in resolved]
    gross_expectancy = statistics.fmean(gross_rs) if gross_rs else 0.0

    return {
        "killzone_bars_scanned": kz_bars,
        "signals_generated": signals,
        "trades_taken": len(trades),
        "expired_unfilled": len(expired),
        "resolved": len(resolved),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": (len(wins) / len(resolved)) if resolved else 0.0,
        "expectancy_r": expectancy,
        "total_r": equity,
        "profit_factor": (gross_win / gross_loss) if gross_loss else
                         (float("inf") if gross_win else 0.0),
        "max_drawdown_r": max_dd,
        "stdev_r": stdev,
        "sharpe_per_trade": (expectancy / stdev) if stdev else 0.0,
        "avg_win_r": statistics.fmean([t.r_multiple for t in wins]) if wins else 0.0,
        "avg_loss_r": statistics.fmean([t.r_multiple for t in losses]) if losses else 0.0,
        "expectancy_gross_r": gross_expectancy,
        "cost_drag_r": gross_expectancy - expectancy,
        "costs": costs.to_dict(),
        "costs_described": costs.describe(),
        "spread_charged": costs.spread,
        "funnel": funnel or {},
        "equity_curve_r": curve,
        "trades": [t.to_dict(decimals=instruments.decimals_for(asset)) for t in trades],
    }


def significance(expectancy: float, stdev: float, n: int) -> dict:
    """
    Is the measured expectancy distinguishable from zero?

    Trade count alone is a poor guide — an earlier version of this report
    called 124 trades "a usable sample" while the result sat well inside its
    own error bars. What matters is the effect size relative to variance, so
    that is what gets reported.
    """
    import math
    if n < 2 or stdev <= 0:
        return {"n": n, "t": 0.0, "p": 1.0, "lo": 0.0, "hi": 0.0,
                "significant": False, "needed": 0}
    se = stdev / math.sqrt(n)
    t = expectancy / se
    p = 2 * (1 - 0.5 * (1 + math.erf(abs(t) / math.sqrt(2))))
    needed = (1.96 * stdev / expectancy) ** 2 if expectancy else float("inf")
    return {"n": n, "t": t, "p": p,
            "lo": expectancy - 1.96 * se, "hi": expectancy + 1.96 * se,
            "significant": abs(t) >= 1.96,
            "needed": needed}


def confidence_note(n: int) -> str:
    """Kept for callers that only have a trade count; significance() is better."""
    if n == 0:
        return "NO TRADES — nothing can be concluded."
    if n < 30:
        return (f"{n} trades is FAR too few to conclude anything. Noise dominates; "
                f"treat any edge shown here as unproven.")
    return f"{n} resolved trades."


def format_report(result: dict, asset: str, period: str) -> str:
    if "error" in result:
        return f"Backtest error: {result['error']}"

    n = result["resolved"]
    _c = result.get("costs") or {"spread": result.get("spread_charged", 0.0)}
    # Cost figures span very different scales (0.30 for gold vs ~0.00012 for
    # a 5-decimal FX pair) — round to the instrument's own display precision
    # rather than a flat 2 decimals, which would print every FX cost as 0.00.
    _cost_dp = max(instruments.decimals_for(asset), 2)
    lines = [
        "=" * 62,
        f"ICT STRATEGY BACKTEST — {asset}",
        "=" * 62,
        f"Period analysed      : {period}",
        f"Parameters           : {result.get('params_used', 'defaults')}",
        f"Killzone bars scanned: {result['killzone_bars_scanned']:,}",
        f"Signals generated    : {result['signals_generated']}",
        f"Orders placed        : {result['trades_taken']}",
        f"  expired unfilled   : {result['expired_unfilled']}",
        f"  resolved win/loss  : {n}",
        "",
        "--- PERFORMANCE (R multiples; 1R = the risk on one trade) ---",
        f"Win rate             : {result['win_rate']:.1%}  ({result['wins']}W / {result['losses']}L)",
        f"Expectancy per trade : {result['expectancy_r']:+.3f} R",
        f"Total return         : {result['total_r']:+.2f} R",
        f"Profit factor        : {result['profit_factor']:.2f}",
        f"Max drawdown         : {result['max_drawdown_r']:.2f} R",
        f"Avg win / avg loss   : {result['avg_win_r']:+.2f} R / {result['avg_loss_r']:+.2f} R",
        f"Sharpe (per trade)   : {result['sharpe_per_trade']:.3f}",
        "",
        f"--- TRADING COSTS (price units of {asset}) ---",
        f"Spread on entry      : {_c.get('spread', 0.0):.{_cost_dp}f}",
        f"Commission per turn  : {_c.get('commission', 0.0):.{_cost_dp}f}",
        f"Slippage on stops    : {_c.get('slippage', 0.0):.{_cost_dp}f}",
        f"Swap per night held  : {_c.get('swap_per_night', 0.0):.{_cost_dp}f}",
        f"Expectancy gross     : {result.get('expectancy_gross_r', 0.0):+.3f} R/trade",
        f"  cost drag          : -{result.get('cost_drag_r', 0.0):.3f} R/trade",
        f"Expectancy net       : {result['expectancy_r']:+.3f} R/trade",
        "",
        "--- WHERE SETUPS WERE REJECTED ---",
    ]
    f = result.get("funnel") or {}
    labels = {"no_liquidity": "no unswept liquidity (Step 3)",
              "no_sweep": "no liquidity sweep (Step 4a)",
              "no_mss": "sweep but no MSS (Step 4b)",
              "no_fvg": "MSS but no FVG (Step 4c)",
              "rr_too_low": "setup valid but R:R < minimum (Step 5)",
              "other": "other"}
    tot = sum(f.values()) or 1
    for k, lbl in labels.items():
        if f.get(k):
            lines.append(f"  {f[k]:>6}  ({f[k]/tot:5.1%})  {lbl}")
    lines += [
        "",
        "--- HONEST READING ---",
    ]
    sig = significance(result["expectancy_r"], result.get("stdev_r", 0.0), n)
    if n == 0:
        lines.append(confidence_note(n))
    elif n < 30:
        lines.append(confidence_note(n))
    else:
        lines += [
            f"Expectancy {result['expectancy_r']:+.3f} R, 95% CI "
            f"[{sig['lo']:+.3f}, {sig['hi']:+.3f}] over {n} trades "
            f"(t={sig['t']:.2f}, p={sig['p']:.2f}).",
        ]
        if sig["significant"]:
            lines.append("This IS statistically distinguishable from zero on this sample.")
        else:
            lines += [
                "This is NOT statistically distinguishable from zero — the confidence",
                "interval spans both losing and winning outcomes. A strategy with no",
                f"edge at all produces a result at least this good about {sig['p']*100:.0f}% of",
                "the time, so the sign of the expectancy carries little information.",
            ]
            if sig["needed"] and sig["needed"] != float("inf"):
                lines.append(f"Establishing significance at this effect size would need "
                             f"~{sig['needed']:,.0f} trades.")
        rec = (result["total_r"] / result["max_drawdown_r"]) if result["max_drawdown_r"] else 0.0
        if result["max_drawdown_r"] > 0:
            lines.append(
                f"Recovery factor {rec:.2f} (return {result['total_r']:+.2f} R vs worst "
                f"drawdown {result['max_drawdown_r']:.2f} R)"
                + ("  — the drawdown exceeded the entire profit." if rec < 1 else "."))
    lines += []
    if n:
        # Only state a directional verdict when the result actually supports
        # one. Announcing "POSITIVE expectancy" directly beneath "not
        # distinguishable from zero" invites reading the sign as the finding.
        if sig["significant"] and n >= 30:
            verdict = ("POSITIVE expectancy on this sample" if result["expectancy_r"] > 0
                       else "NEGATIVE expectancy on this sample")
            lines.append(f"Result: {verdict} ({result['expectancy_r']:+.3f} R/trade).")
        elif n < 30:
            # A handful of trades can clear a t-test and still mean nothing;
            # announcing a verdict here would contradict the line above it.
            lines.append(f"Result: INCONCLUSIVE — {n} trades. The point estimate is "
                         f"{result['expectancy_r']:+.3f} R/trade and is not worth acting on.")
        else:
            lines.append("Result: NO MEASURABLE EDGE on this sample. The point estimate "
                         f"is {result['expectancy_r']:+.3f} R/trade, but it cannot be "
                         "separated from zero.")
        lines.append("")
        lines.append("Caveats that apply regardless of the numbers above:")
        lines.append("  - Bar-resolution fills; intrabar sequence is assumed pessimistically.")
        lines.append("  - Costs are modelled at fixed retail figures; real spreads widen "
                     "around news")
        lines.append("    and rollover, which is exactly when these setups tend to trigger.")
        lines.append("  - No news-gap modelling: a stop gapped through fills far worse")
        lines.append("    than the fixed slippage charged above.")
        lines.append("  - Single instrument, single period — not walk-forward validated")
        lines.append("    across regimes, so it may be fitted to this stretch of market.")
        lines.append("  - Past results do not establish future performance.")
    lines.append("=" * 62)
    return "\n".join(lines)
