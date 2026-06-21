"""
Risk management layer — enforces position limits, daily loss limits, and
operates the kill switch that halts trading when performance degrades.

All state is persisted to data/pw_trades/risk_state.json so limits
survive process restarts.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

ROOT       = Path(__file__).parent.parent
TRADES_DIR = ROOT / "data" / "pw_trades"
TRADES_DIR.mkdir(parents=True, exist_ok=True)

STATE_FILE = TRADES_DIR / "risk_state.json"

# Defaults (overridable via env vars in tools.py)
DEFAULT_MAX_POSITION_PCT  = 0.05   # 5 % of bankroll per trade
DEFAULT_MAX_DAILY_LOSS_PCT = 0.10  # halt if daily drawdown > 10 %
DEFAULT_MAX_OPEN_POSITIONS = 10
DEFAULT_MIN_EDGE           = 0.07  # ignore signals with edge < 7 %
DEFAULT_MAX_CONSECUTIVE_LOSSES = 5  # kill switch after N consecutive losses


class RiskManager:
    """
    Stateful risk manager. Load from disk on startup; persist after each decision.
    """

    def __init__(
        self,
        starting_bankroll:       float = 1000.0,
        max_position_pct:        float = DEFAULT_MAX_POSITION_PCT,
        max_daily_loss_pct:      float = DEFAULT_MAX_DAILY_LOSS_PCT,
        max_open_positions:      int   = DEFAULT_MAX_OPEN_POSITIONS,
        min_edge:                float = DEFAULT_MIN_EDGE,
        max_consecutive_losses:  int   = DEFAULT_MAX_CONSECUTIVE_LOSSES,
    ):
        self.starting_bankroll      = starting_bankroll
        self.max_position_pct       = max_position_pct
        self.max_daily_loss_pct     = max_daily_loss_pct
        self.max_open_positions     = max_open_positions
        self.min_edge               = min_edge
        self.max_consecutive_losses = max_consecutive_losses

        # Loaded / updated state
        self._state: dict = {}
        self._load_state()

    # -----------------------------------------------------------------------
    # State persistence
    # -----------------------------------------------------------------------

    def _load_state(self):
        if STATE_FILE.exists():
            try:
                self._state = json.loads(STATE_FILE.read_text())
            except (OSError, json.JSONDecodeError):
                self._state = {}
        self._state.setdefault("bankroll",            self.starting_bankroll)
        self._state.setdefault("daily_start_bankroll",self.starting_bankroll)
        self._state.setdefault("daily_date",          _today())
        self._state.setdefault("halted",              False)
        self._state.setdefault("halt_reason",         "")
        self._state.setdefault("open_positions",      [])
        self._state.setdefault("consecutive_losses",  0)
        self._state.setdefault("trade_count",         0)
        self._state.setdefault("daily_pnl",           0.0)
        self._state.setdefault("total_pnl",           0.0)

    def _save_state(self):
        STATE_FILE.write_text(json.dumps(self._state, indent=2))

    def _reset_daily_if_needed(self):
        today = _today()
        if self._state["daily_date"] != today:
            self._state["daily_date"]          = today
            self._state["daily_start_bankroll"] = self._state["bankroll"]
            self._state["daily_pnl"]            = 0.0

    # -----------------------------------------------------------------------
    # Kill switch
    # -----------------------------------------------------------------------

    @property
    def is_halted(self) -> bool:
        return bool(self._state.get("halted", False))

    def halt(self, reason: str):
        self._state["halted"]      = True
        self._state["halt_reason"] = reason
        self._state["halt_time"]   = datetime.now(timezone.utc).isoformat()
        self._save_state()

    def resume(self):
        self._state["halted"]              = False
        self._state["halt_reason"]         = ""
        self._state["consecutive_losses"]  = 0
        self._save_state()

    # -----------------------------------------------------------------------
    # Pre-trade checks
    # -----------------------------------------------------------------------

    def check_trade(
        self,
        model_prob: float,
        market_price: float,
        side: str,
        proposed_size: float,
    ) -> tuple[bool, str]:
        """
        Returns (approved: bool, reason: str).
        Call this before every order.
        """
        self._reset_daily_if_needed()

        if self.is_halted:
            return False, f"Trading halted: {self._state['halt_reason']}"

        bankroll = self._state["bankroll"]
        if bankroll <= 0:
            return False, "Bankroll depleted"

        # Edge check
        edge = abs(model_prob - market_price)
        if edge < self.min_edge:
            return False, f"Edge {edge:.3f} below minimum {self.min_edge:.3f}"

        # Position size check
        max_size = bankroll * self.max_position_pct
        if proposed_size > max_size:
            return False, f"Size ${proposed_size:.2f} exceeds max ${max_size:.2f}"

        # Open positions check
        if len(self._state["open_positions"]) >= self.max_open_positions:
            return False, f"Max open positions ({self.max_open_positions}) reached"

        # Daily loss limit
        daily_loss_pct = (
            (self._state["daily_start_bankroll"] - bankroll)
            / self._state["daily_start_bankroll"]
            if self._state["daily_start_bankroll"] > 0
            else 0.0
        )
        if daily_loss_pct >= self.max_daily_loss_pct:
            self.halt(f"Daily loss limit {self.max_daily_loss_pct*100:.0f}% breached")
            return False, "Daily loss limit reached — trading halted"

        # Consecutive losses kill switch
        if self._state["consecutive_losses"] >= self.max_consecutive_losses:
            self.halt(
                f"{self.max_consecutive_losses} consecutive losses detected"
            )
            return False, "Kill switch: too many consecutive losses"

        return True, "approved"

    # -----------------------------------------------------------------------
    # Post-trade update
    # -----------------------------------------------------------------------

    def record_trade_open(self, order_id: str, token_id: str, side: str, size: float):
        self._state["open_positions"].append({
            "order_id": order_id,
            "token_id": token_id,
            "side":     side,
            "size":     size,
            "opened":   datetime.now(timezone.utc).isoformat(),
        })
        self._state["trade_count"] += 1
        self._save_state()

    def record_trade_close(self, order_id: str, pnl: float):
        self._state["open_positions"] = [
            p for p in self._state["open_positions"]
            if p.get("order_id") != order_id
        ]
        self._state["bankroll"]   += pnl
        self._state["daily_pnl"]  += pnl
        self._state["total_pnl"]  += pnl

        if pnl < 0:
            self._state["consecutive_losses"] += 1
        else:
            self._state["consecutive_losses"] = 0

        self._save_state()

    # -----------------------------------------------------------------------
    # Queries
    # -----------------------------------------------------------------------

    @property
    def bankroll(self) -> float:
        return float(self._state.get("bankroll", self.starting_bankroll))

    @property
    def daily_pnl(self) -> float:
        self._reset_daily_if_needed()
        return float(self._state.get("daily_pnl", 0.0))

    def status_dict(self) -> dict:
        self._reset_daily_if_needed()
        br = self._state["bankroll"]
        return {
            "bankroll":           round(br, 2),
            "starting_bankroll":  round(self.starting_bankroll, 2),
            "total_pnl":          round(self._state["total_pnl"], 2),
            "daily_pnl":          round(self._state["daily_pnl"], 2),
            "open_positions":     len(self._state["open_positions"]),
            "trade_count":        self._state["trade_count"],
            "consecutive_losses": self._state["consecutive_losses"],
            "halted":             self._state["halted"],
            "halt_reason":        self._state.get("halt_reason", ""),
        }


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")
