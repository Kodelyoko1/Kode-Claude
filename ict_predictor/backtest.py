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
   counts if price actually traded through the entry. Spread is charged on
   entry. When a single bar spans both stop and target we assume the STOP
   hit first — the true sequence is unknowable at bar resolution, and the
   pessimistic reading is the honest one.

4. Orders expire like the live ones do (ORDER_TIME_DAY), so an unfilled
   setup does not linger and get credited days later.

Results carry the sample size prominently. A high win rate over 9 trades is
noise, and the report says so.
"""
from __future__ import annotations

import os
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from ict_predictor import killzone
from ict_predictor.predictor import build_prediction

# Cost model. Gold spot spreads are typically 0.2-0.5 on a retail account;
# 0.30 is a reasonable default and is charged against every entry.
DEFAULT_SPREAD = float(os.getenv("IP_BT_SPREAD", "0.30"))
# Bars of history the predictor needs before it can map structure.
WARMUP_BARS = int(os.getenv("IP_BT_WARMUP", "60"))


class Trade:
    __slots__ = ("direction", "signal_t", "entry", "sl", "tp", "rr_planned",
                 "confidence", "fill_t", "exit_t", "exit_price", "outcome", "r_multiple")

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
        self.r_multiple: float = 0.0

    def to_dict(self) -> dict:
        return {
            "direction": self.direction,
            "signal_time": datetime.fromtimestamp(self.signal_t, tz=timezone.utc).isoformat(),
            "entry": round(self.entry, 2),
            "sl": round(self.sl, 2),
            "tp": round(self.tp, 2),
            "rr_planned": self.rr_planned,
            "confidence": self.confidence,
            "outcome": self.outcome,
            "r_multiple": round(self.r_multiple, 3),
        }


def _same_day(a: int, b: int) -> bool:
    da = datetime.fromtimestamp(a, tz=timezone.utc).date()
    db = datetime.fromtimestamp(b, tz=timezone.utc).date()
    return da == db


def _resolve_trade(trade: Trade, future_bars: list[dict], spread: float) -> Trade:
    """
    Walk forward bar by bar and decide what actually happened.

    Fill: a BUY limit fills when the low trades down through entry (a SELL
    limit when the high trades up through it). The spread is charged by
    filling a buy at entry+spread and a sell at entry-spread.

    Exit: whichever of stop/target the price reaches first. If one bar
    contains both, count it as a LOSS — at bar resolution the order is
    unknowable, and crediting the win would flatter the result.
    """
    is_long = trade.direction == "LONG"
    fill_price = trade.entry + spread if is_long else trade.entry - spread

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
            trade.exit_t, trade.exit_price = bar["t"], trade.sl
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
            trade.r_multiple = pnl / risk
    elif filled:
        # Filled but neither level reached before data ran out.
        trade.outcome = "open_at_end"
    return trade


def run_backtest(htf_bars: list[dict], ltf_bars: list[dict], asset: str = "GC",
                 spread: float = DEFAULT_SPREAD,
                 progress: Optional[Callable[[int, int], None]] = None,
                 params: Optional[dict] = None) -> dict:
    """
    Replay history bar-by-bar. `htf_bars` (15M) drive the decision clock;
    `ltf_bars` (5M) supply precision entries and trade resolution.
    Both must be sorted oldest-first.
    """
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
        htf_window = htf_bars[:i + 1]
        ltf_window = [b for b in ltf_bars if b["t"] <= decision_t]
        assert not htf_window or htf_window[-1]["t"] <= decision_t
        assert not ltf_window or ltf_window[-1]["t"] <= decision_t
        if len(ltf_window) < 20:
            continue

        pred = build_prediction(asset, kz, htf_window, ltf_window[-300:], "5M",
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
        future = [b for b in ltf_bars if b["t"] > decision_t]
        trade = _resolve_trade(trade, future, spread)
        trades.append(trade)
        if trade.exit_t:
            open_until = trade.exit_t
        elif trade.fill_t:
            open_until = trade.fill_t

    return _summarize(trades, signals_seen, killzone_bars, spread, funnel)


def _summarize(trades: list[Trade], signals: int, kz_bars: int, spread: float,
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
        "spread_charged": spread,
        "funnel": funnel or {},
        "equity_curve_r": curve,
        "trades": [t.to_dict() for t in trades],
    }


def confidence_note(n: int) -> str:
    """Blunt statement of what a sample this size can and cannot support."""
    if n == 0:
        return "NO TRADES — nothing can be concluded."
    if n < 30:
        return (f"{n} trades is FAR too few to conclude anything. Noise dominates; "
                f"treat any edge shown here as unproven.")
    if n < 100:
        return (f"{n} trades is a weak sample. Direction may be suggestive, but the "
                f"error bars are wide.")
    return (f"{n} trades is a usable sample, though still short of what a "
            f"confident edge claim needs (300+).")


def format_report(result: dict, asset: str, period: str) -> str:
    if "error" in result:
        return f"Backtest error: {result['error']}"

    n = result["resolved"]
    lines = [
        "=" * 62,
        f"ICT STRATEGY BACKTEST — {asset}",
        "=" * 62,
        f"Period analysed      : {period}",
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
        f"Spread charged       : {result['spread_charged']} per entry",
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
        f"{confidence_note(n)}",
    ]
    if n:
        verdict = ("POSITIVE expectancy on this sample" if result["expectancy_r"] > 0
                   else "NEGATIVE expectancy on this sample")
        lines.append(f"Result: {verdict} ({result['expectancy_r']:+.3f} R/trade).")
        lines.append("")
        lines.append("Caveats that apply regardless of the numbers above:")
        lines.append("  - Bar-resolution fills; intrabar sequence is assumed pessimistically.")
        lines.append("  - No slippage, commission, swap, or news-gap modelling.")
        lines.append("  - Single instrument, single period — not walk-forward validated")
        lines.append("    across regimes, so it may be fitted to this stretch of market.")
        lines.append("  - Past results do not establish future performance.")
    lines.append("=" * 62)
    return "\n".join(lines)
