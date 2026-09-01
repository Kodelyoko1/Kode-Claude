"""
Pre-trade Risk Manager — the single choke point every Signal must pass
through before it can become an Order. No strategy, connector, or
execution engine may bypass this: `agent.py` always routes
strategy-output through `RiskManager.evaluate()` first.

Hard guardrails enforced (per config/risk_limits.yaml):
    1. Max allocation % per single asset (post-trade).
    2. Max daily portfolio loss threshold (kill switch — halts new BUYs).
    3. Max slippage tolerance (rejects if a fill would slip too far from
       the reference price).
    4. Optional absolute position notional cap.
    5. Max orders per cycle (circuit breaker on quantity of orders, not
       just their individual size).
    6. Minimum order notional (skip dust trades).

A rejection is not a crash: it is a normal, logged outcome that keeps a
misbehaving strategy or a stale price feed from doing damage.
"""
from __future__ import annotations

from ..config.settings import RiskLimitsConfig
from ..schemas import ActionType, PortfolioState, RiskCheckResult, Signal


class RiskManager:
    def __init__(self, limits: RiskLimitsConfig):
        self.limits = limits

    def evaluate(
        self,
        signal: Signal,
        portfolio: PortfolioState,
        current_price: float,
        *,
        proposed_quantity: float | None = None,
        reference_price: float | None = None,
        orders_already_this_cycle: int = 0,
    ) -> RiskCheckResult:
        """
        Evaluate one signal against the portfolio's post-trade state.

        `proposed_quantity` is the quantity the caller intends to submit
        (already sized from the signal's target_weight/size by the
        orchestrator). `reference_price` is the price the signal/quote was
        generated at; if the current fill price has slipped past
        `max_slippage_pct` from it, the order is rejected.
        """
        reasons: list[str] = []

        if signal.action == ActionType.HOLD:
            return RiskCheckResult(approved=False, reasons=["HOLD signals never become orders"])

        if current_price <= 0:
            return RiskCheckResult(approved=False, reasons=[f"invalid current_price: {current_price}"])

        # --- circuit breaker: orders per cycle -----------------------------
        if orders_already_this_cycle >= self.limits.max_orders_per_cycle:
            return RiskCheckResult(approved=False, reasons=[
                f"max_orders_per_cycle ({self.limits.max_orders_per_cycle}) already reached this cycle"
            ])

        # --- daily loss kill switch -----------------------------------------
        # SELL orders are always allowed through the loss guardrail (reducing
        # risk during a bad day is exactly what you want to still permit);
        # only new BUYs are blocked once the daily loss cap is breached.
        daily_pnl_pct = portfolio.daily_pnl_pct()
        halted = daily_pnl_pct is not None and daily_pnl_pct <= -self.limits.max_daily_loss_pct
        if halted and signal.action == ActionType.BUY:
            return RiskCheckResult(approved=False, reasons=[
                f"daily loss {daily_pnl_pct:.2%} breaches max_daily_loss_pct "
                f"({-self.limits.max_daily_loss_pct:.2%}) — new BUYs halted for the day"
            ])

        quantity = proposed_quantity if proposed_quantity is not None else (signal.size or 0.0)
        if quantity <= 0:
            return RiskCheckResult(approved=False, reasons=[f"non-positive quantity: {quantity}"])

        notional = quantity * current_price

        # --- minimum notional (dust filter) --------------------------------
        if notional < self.limits.min_order_notional:
            return RiskCheckResult(approved=False, reasons=[
                f"order notional ${notional:.2f} below min_order_notional "
                f"(${self.limits.min_order_notional:.2f})"
            ])

        # --- slippage tolerance ----------------------------------------------
        if reference_price and reference_price > 0:
            slippage_pct = abs(current_price - reference_price) / reference_price
            if slippage_pct > self.limits.max_slippage_pct:
                return RiskCheckResult(approved=False, reasons=[
                    f"slippage {slippage_pct:.2%} exceeds max_slippage_pct "
                    f"({self.limits.max_slippage_pct:.2%}); reference={reference_price}, current={current_price}"
                ])

        adjusted_quantity: float | None = None

        if signal.action == ActionType.BUY:
            # --- max allocation % per asset (post-trade) --------------------
            equity = portfolio.total_equity
            existing_value = portfolio.positions.get(signal.symbol).market_value if signal.symbol in portfolio.positions else 0.0
            post_trade_value = existing_value + notional
            post_trade_equity = max(equity, 1e-9)  # cash decreases, equity roughly unchanged pre/post a same-asset swap
            post_trade_weight = post_trade_value / post_trade_equity

            if post_trade_weight > self.limits.max_allocation_pct:
                max_value = self.limits.max_allocation_pct * post_trade_equity - existing_value
                if max_value <= 0:
                    return RiskCheckResult(approved=False, reasons=[
                        f"{signal.symbol} already at/over max_allocation_pct "
                        f"({self.limits.max_allocation_pct:.1%} of equity)"
                    ])
                trimmed_quantity = max_value / current_price
                trimmed_notional = trimmed_quantity * current_price
                if trimmed_notional < self.limits.min_order_notional:
                    return RiskCheckResult(approved=False, reasons=[
                        f"trimming {signal.symbol} to stay under max_allocation_pct "
                        f"({self.limits.max_allocation_pct:.1%}) would leave only "
                        f"${trimmed_notional:.2f} notional, below min_order_notional"
                    ])
                reasons.append(
                    f"trimmed quantity from {quantity:.8g} to {trimmed_quantity:.8g} to respect "
                    f"max_allocation_pct ({self.limits.max_allocation_pct:.1%})"
                )
                adjusted_quantity = trimmed_quantity
                quantity = trimmed_quantity
                notional = trimmed_notional

            # --- absolute position notional cap ------------------------------
            if self.limits.max_position_notional is not None:
                if existing_value + notional > self.limits.max_position_notional:
                    max_additional = self.limits.max_position_notional - existing_value
                    if max_additional <= 0:
                        return RiskCheckResult(approved=False, reasons=[
                            f"{signal.symbol} already at/over max_position_notional "
                            f"(${self.limits.max_position_notional:.2f})"
                        ])
                    trimmed_quantity = max_additional / current_price
                    if trimmed_quantity * current_price < self.limits.min_order_notional:
                        return RiskCheckResult(approved=False, reasons=[
                            f"trimming {signal.symbol} to stay under max_position_notional "
                            f"would fall below min_order_notional"
                        ])
                    reasons.append(
                        f"trimmed quantity to {trimmed_quantity:.8g} to respect "
                        f"max_position_notional (${self.limits.max_position_notional:.2f})"
                    )
                    adjusted_quantity = trimmed_quantity
                    quantity = trimmed_quantity

            # --- can we actually afford it? --------------------------------
            if notional > portfolio.cash + 1e-6:
                affordable_quantity = portfolio.cash / current_price
                if affordable_quantity * current_price < self.limits.min_order_notional:
                    return RiskCheckResult(approved=False, reasons=[
                        f"insufficient cash: need ${notional:.2f}, have ${portfolio.cash:.2f}"
                    ])
                reasons.append(f"trimmed quantity to {affordable_quantity:.8g} — insufficient cash for full size")
                adjusted_quantity = affordable_quantity

        elif signal.action == ActionType.SELL:
            # never sell more than is actually held
            held = portfolio.positions.get(signal.symbol)
            held_qty = held.quantity if held else 0.0
            if held_qty <= 0:
                return RiskCheckResult(approved=False, reasons=[f"no position in {signal.symbol} to sell"])
            if quantity > held_qty:
                reasons.append(f"trimmed sell quantity from {quantity:.8g} to held quantity {held_qty:.8g}")
                adjusted_quantity = held_qty

        reasons.append("passed all pre-trade risk checks" if not reasons else "approved with adjustment")
        return RiskCheckResult(approved=True, reasons=reasons, adjusted_quantity=adjusted_quantity)

    def is_halted(self, portfolio: PortfolioState) -> bool:
        pct = portfolio.daily_pnl_pct()
        return pct is not None and pct <= -self.limits.max_daily_loss_pct
