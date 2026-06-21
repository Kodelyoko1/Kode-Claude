"""
Backtesting framework for weather prediction trading strategies.

Simulates trading on historical PolyMarket-style markets using:
  - Model probability estimates
  - Synthetic or real historical market prices
  - Configurable edge threshold, position sizing (Kelly or fixed), and fees

Key metrics: P&L, Sharpe ratio, max drawdown, win rate, Brier skill score,
calibration error, and edge capture rate.
"""
from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

ROOT        = Path(__file__).parent.parent
REPORTS_DIR = ROOT / "data" / "pw_reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

# PolyMarket charges ~2% taker fee on options-style markets
DEFAULT_FEE  = 0.02
DEFAULT_BANKROLL = 1000.0   # starting USDC


# ---------------------------------------------------------------------------
# Signal generator interface
# ---------------------------------------------------------------------------

def threshold_signal(
    model_prob: float,
    market_price: float,
    min_edge: float = 0.07,
) -> Optional[str]:
    """
    Returns 'YES', 'NO', or None.
    Edge = |model_prob - market_price| must exceed min_edge.
    """
    if model_prob - market_price >= min_edge:
        return "YES"
    if market_price - model_prob >= min_edge:
        return "NO"
    return None


# ---------------------------------------------------------------------------
# Position sizing
# ---------------------------------------------------------------------------

def kelly_size(
    model_prob: float,
    market_price: float,
    side: str,
    bankroll: float,
    max_fraction: float = 0.05,
    kelly_fraction: float = 0.25,  # fractional Kelly for safety
) -> float:
    """
    Fractional Kelly criterion for binary prediction market.

    For YES bet: odds = (1 - market_price) / market_price at resolution
    Edge       = model_prob - market_price

    Full Kelly fraction f* = edge / (1 - market_price) for YES
    We apply kelly_fraction (default 0.25) and cap at max_fraction of bankroll.
    """
    if side == "YES":
        b    = (1 - market_price) / market_price  # net odds per unit bet
        edge = model_prob - market_price
    else:
        b    = market_price / (1 - market_price)
        edge = (1 - model_prob) - (1 - market_price)

    b = max(b, 0.01)
    full_kelly = edge / b if b > 0 else 0.0
    frac_kelly = full_kelly * kelly_fraction
    frac_kelly = max(0.0, min(frac_kelly, max_fraction))
    return round(bankroll * frac_kelly, 2)


def fixed_size(bankroll: float, fraction: float = 0.02) -> float:
    return round(bankroll * fraction, 2)


# ---------------------------------------------------------------------------
# Trade record
# ---------------------------------------------------------------------------

class Trade:
    def __init__(
        self,
        date:         str,
        market_id:    str,
        question:     str,
        side:         str,          # "YES" or "NO"
        entry_price:  float,
        exit_price:   float,        # 1.0 if correct, 0.0 if wrong
        size:         float,        # USDC staked
        model_prob:   float,
        market_price: float,
        fee:          float = DEFAULT_FEE,
    ):
        self.date         = date
        self.market_id    = market_id
        self.question     = question
        self.side         = side
        self.entry_price  = entry_price
        self.exit_price   = exit_price
        self.size         = size
        self.model_prob   = model_prob
        self.market_price = market_price
        self.fee_paid     = size * fee
        gross_pnl = (exit_price - entry_price) * (size / entry_price) if entry_price > 0 else 0.0
        self.pnl  = gross_pnl - self.fee_paid

    def to_dict(self) -> dict:
        return {
            "date":         self.date,
            "market_id":    self.market_id,
            "question":     self.question[:80],
            "side":         self.side,
            "entry_price":  self.entry_price,
            "exit_price":   self.exit_price,
            "size":         self.size,
            "model_prob":   self.model_prob,
            "market_price": self.market_price,
            "edge":         round(abs(self.model_prob - self.market_price), 4),
            "pnl":          round(self.pnl, 4),
            "fee_paid":     round(self.fee_paid, 4),
        }


# ---------------------------------------------------------------------------
# Backtest engine
# ---------------------------------------------------------------------------

class BacktestEngine:
    """
    Simulates a trading strategy against historical aligned data.

    Input rows (aligned_data) must contain:
        date, market_id, question, market_price, outcome (0 or 1),
        plus all weather feature columns.

    The `model_fn` is a callable that takes a list of feature dicts
    and returns a list of probabilities.
    """

    def __init__(
        self,
        model_fn:        Callable[[list[dict]], list[float]],
        bankroll:        float = DEFAULT_BANKROLL,
        min_edge:        float = 0.07,
        sizing:          str   = "kelly",    # "kelly" or "fixed"
        kelly_fraction:  float = 0.25,
        fixed_fraction:  float = 0.02,
        max_position_pct:float = 0.05,
        fee:             float = DEFAULT_FEE,
    ):
        self.model_fn         = model_fn
        self.bankroll         = bankroll
        self.initial_bankroll = bankroll
        self.min_edge         = min_edge
        self.sizing           = sizing
        self.kelly_fraction   = kelly_fraction
        self.fixed_fraction   = fixed_fraction
        self.max_position_pct = max_position_pct
        self.fee              = fee
        self.trades:   list[Trade] = []
        self.equity:   list[float] = [bankroll]
        self.equity_dates: list[str] = []

    def run(self, aligned_data: list[dict]) -> dict:
        """
        Run the backtest over `aligned_data` sorted chronologically.
        Returns metrics dict.
        """
        self.trades   = []
        self.equity   = [self.initial_bankroll]
        self.bankroll = self.initial_bankroll

        # Group by market so we only trade each market once (on the first day we see it)
        seen_markets: set[str] = set()

        for row in sorted(aligned_data, key=lambda r: r.get("date", "")):
            market_id = row.get("market_id", row.get("condition_id", "unknown"))
            outcome   = row.get("outcome")
            if outcome is None or market_id in seen_markets:
                continue
            seen_markets.add(market_id)

            market_price = float(row.get("market_price", 0.5))
            model_prob   = self.model_fn([row])[0]
            side         = threshold_signal(model_prob, market_price, self.min_edge)

            if side is None:
                continue

            if self.sizing == "kelly":
                size = kelly_size(
                    model_prob, market_price, side,
                    self.bankroll,
                    self.max_position_pct,
                    self.kelly_fraction,
                )
            else:
                size = fixed_size(self.bankroll, self.fixed_fraction)

            if size < 1.0 or size > self.bankroll:
                continue

            # Determine exit price from outcome
            if side == "YES":
                entry  = market_price
                exit_p = 1.0 if int(outcome) == 1 else 0.0
            else:
                entry  = 1.0 - market_price
                exit_p = 1.0 if int(outcome) == 0 else 0.0

            trade = Trade(
                date         = row.get("date", ""),
                market_id    = market_id,
                question     = row.get("question", ""),
                side         = side,
                entry_price  = entry,
                exit_price   = exit_p,
                size         = size,
                model_prob   = model_prob,
                market_price = market_price,
                fee          = self.fee,
            )
            self.bankroll += trade.pnl
            self.bankroll  = max(self.bankroll, 0.0)
            self.trades.append(trade)
            self.equity.append(self.bankroll)
            self.equity_dates.append(row.get("date", ""))

        return self._compute_metrics()

    # -----------------------------------------------------------------------
    # Metrics
    # -----------------------------------------------------------------------

    def _compute_metrics(self) -> dict:
        if not self.trades:
            return {"error": "no trades generated", "trades": 0}

        pnls       = [t.pnl for t in self.trades]
        total_pnl  = sum(pnls)
        win_trades = [t for t in self.trades if t.pnl > 0]
        win_rate   = len(win_trades) / len(self.trades)
        avg_pnl    = total_pnl / len(self.trades)

        # Sharpe (annualised, assuming ~252 trading opportunities/year)
        import math
        mean_r = sum(pnls) / len(pnls)
        var_r  = sum((p - mean_r) ** 2 for p in pnls) / len(pnls)
        std_r  = math.sqrt(var_r) if var_r > 0 else 1e-9
        sharpe = (mean_r / std_r) * math.sqrt(252)

        # Max drawdown
        peak = self.initial_bankroll
        max_dd = 0.0
        running = self.initial_bankroll
        for pnl in pnls:
            running += pnl
            peak     = max(peak, running)
            dd       = (peak - running) / peak if peak > 0 else 0.0
            max_dd   = max(max_dd, dd)

        # Calibration: avg |model_prob - market_price| on winning and losing trades
        yes_trades = [t for t in self.trades if t.side == "YES"]
        avg_edge   = (sum(abs(t.model_prob - t.market_price) for t in self.trades)
                      / len(self.trades))

        # Brier score for model calibration check
        brier = sum(
            (t.model_prob - t.exit_price) ** 2
            if t.side == "YES"
            else ((1 - t.model_prob) - t.exit_price) ** 2
            for t in self.trades
        ) / len(self.trades)

        return {
            "trades":          len(self.trades),
            "win_rate":        round(win_rate, 4),
            "total_pnl":       round(total_pnl, 2),
            "avg_pnl":         round(avg_pnl, 4),
            "final_bankroll":  round(self.bankroll, 2),
            "roi_pct":         round(100 * total_pnl / self.initial_bankroll, 2),
            "sharpe":          round(sharpe, 4),
            "max_drawdown_pct":round(100 * max_dd, 2),
            "avg_edge":        round(avg_edge, 4),
            "brier_score":     round(brier, 4),
            "fees_paid":       round(sum(t.fee_paid for t in self.trades), 2),
        }

    # -----------------------------------------------------------------------
    # Reporting
    # -----------------------------------------------------------------------

    def generate_report(self, name: str = "backtest") -> Path:
        metrics = self._compute_metrics()
        lines = [
            f"# PolyMarket Weather Backtest Report — {name}",
            f"Generated: {datetime.now():%Y-%m-%d %H:%M:%S UTC}",
            "",
            "## Summary",
            f"| Metric | Value |",
            f"|--------|-------|",
        ]
        for k, v in metrics.items():
            lines.append(f"| {k} | {v} |")

        lines += ["", "## Trade Log (last 50)", ""]
        lines += ["| Date | Question | Side | Entry | Exit | Size | PnL |",
                  "|------|----------|------|-------|------|------|-----|"]
        for t in self.trades[-50:]:
            lines.append(
                f"| {t.date} | {t.question[:40]}… | {t.side} | "
                f"{t.entry_price:.3f} | {t.exit_price:.0f} | "
                f"${t.size:.1f} | ${t.pnl:+.2f} |"
            )

        path = REPORTS_DIR / f"{name}_{datetime.now():%Y%m%d_%H%M%S}.md"
        path.write_text("\n".join(lines))
        return path

    def save_trades(self, name: str = "backtest") -> Path:
        path = REPORTS_DIR / f"{name}_trades.json"
        path.write_text(json.dumps([t.to_dict() for t in self.trades], indent=2))
        return path
