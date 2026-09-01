"""
AssetManagerAgent — the main autonomous orchestration loop.

One cycle does exactly this, in order:
    1. Fetch market data (OHLCV) + current portfolio state from the connector.
    2. Ask the configured strategy for signals.
    3. Size each non-HOLD signal into a proposed order quantity.
    4. Run it through RiskManager.evaluate() — reject, trim, or approve.
    5. Approved orders go to the execution engine (paper by default).
    6. Persist the signal, the order, and the resulting portfolio snapshot.

Every step is logged with enough detail (signal rationale, risk verdict,
execution receipt) to reconstruct *why* a trade did or didn't happen from
the log file alone.

Two run modes:
    run_cycle()               — one pass, returns a summary dict. This is
                                 what `tools.run_full_cycle()` calls, and
                                 what cron / run_asset_manager_auto.py uses.
    run_forever(interval_sec) — blocking loop calling run_cycle() on a
                                 fixed interval (falls back to a plain
                                 sleep loop if APScheduler isn't installed).
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Optional

from . import storage
from .config.settings import Settings, get_settings, load_risk_limits
from .connectors import get_connector
from .connectors.base import BaseConnector, ConnectorError
from .execution import get_execution_engine
from .execution.base import ExecutionError
from .logging_config import configure_logging, get_logger
from .risk import RiskManager
from .schemas import ActionType, Order, OrderStatus, PortfolioState, Signal
from .strategies import get_strategy

log = get_logger("agent")


class AssetManagerAgent:
    def __init__(
        self,
        settings: Optional[Settings] = None,
        connector: Optional[BaseConnector] = None,
        strategy=None,
        risk_manager: Optional[RiskManager] = None,
    ):
        configure_logging()
        self.settings = settings or get_settings()
        self.connector = connector or get_connector(self.settings.connector, self.settings)
        self.strategy = strategy or get_strategy(self.settings.strategy, self.settings)
        self.risk_manager = risk_manager or RiskManager(load_risk_limits())
        self.execution_engine = get_execution_engine(self.connector, self.settings)
        storage.init_db()

        log.info(
            "AssetManagerAgent initialized: connector=%s strategy=%s mode=%s symbols=%s",
            self.connector.name, self.strategy.name,
            "PAPER" if self.execution_engine.is_dry_run else "LIVE",
            ", ".join(self.settings.symbols),
        )

    # -------------------------------------------------------------------
    # one cycle
    # -------------------------------------------------------------------

    def run_cycle(self) -> dict:
        started_at = datetime.now(timezone.utc)
        log.info("=== cycle start %s ===", started_at.isoformat())

        try:
            portfolio = self.connector.fetch_portfolio()
        except ConnectorError as e:
            log.error("fetch_portfolio failed: %s — aborting cycle", e)
            return {"status": "error", "error": str(e), "orders_placed": 0, "signals": 0}

        portfolio = storage.roll_day_start_equity_if_new_day(portfolio)

        market_data: dict[str, list] = {}
        current_prices: dict[str, float] = {}
        for symbol in self.settings.symbols:
            try:
                candles = self.connector.fetch_ohlcv(
                    symbol, timeframe=self.settings.timeframe, limit=self.settings.candle_limit,
                )
                if not candles:
                    log.warning("no candles returned for %s, skipping", symbol)
                    continue
                market_data[symbol] = candles
                current_prices[symbol] = candles[-1].close
            except ConnectorError as e:
                log.error("fetch_ohlcv(%s) failed: %s — skipping symbol this cycle", symbol, e)

        # mark-to-market every held position against fresh prices, including
        # ones not in this cycle's universe, so daily_pnl_pct() stays accurate
        for symbol, pos in portfolio.positions.items():
            if symbol in current_prices:
                pos.current_price = current_prices[symbol]
            elif symbol not in self.settings.symbols:
                try:
                    pos.current_price = self.connector.fetch_price(symbol)
                except ConnectorError:
                    pass  # keep the last known price rather than crash the cycle

        if self.risk_manager.is_halted(portfolio):
            log.warning(
                "TRADING HALTED this cycle: daily loss %.2f%% breaches limit",
                (portfolio.daily_pnl_pct() or 0) * 100,
            )

        if not market_data:
            log.error("no market data available for any symbol — aborting cycle")
            return {"status": "error", "error": "no market data", "orders_placed": 0, "signals": 0}

        signals: list[Signal] = self.strategy.generate_signals(market_data, portfolio)

        orders_placed = 0
        orders_rejected = 0
        results: list[dict] = []

        for signal in signals:
            price = current_prices.get(signal.symbol)
            row: dict = {
                "symbol": signal.symbol, "action": signal.action.value,
                "rationale": signal.rationale, "order": None,
            }

            if signal.action == ActionType.HOLD:
                storage.save_signal(signal)
                results.append(row)
                continue

            if price is None or price <= 0:
                log.warning("skipping %s signal for %s: no valid price", signal.action, signal.symbol)
                storage.save_signal(signal, None)
                results.append(row)
                continue

            proposed_quantity = self._size_signal(signal, portfolio, price)
            risk_result = self.risk_manager.evaluate(
                signal, portfolio, current_price=price,
                proposed_quantity=proposed_quantity, reference_price=price,
                orders_already_this_cycle=orders_placed,
            )
            storage.save_signal(signal, risk_result)

            log.info(
                "%s %s: risk=%s reasons=%s",
                signal.action, signal.symbol, "APPROVED" if risk_result.approved else "REJECTED",
                "; ".join(risk_result.reasons),
            )

            if not risk_result.approved:
                orders_rejected += 1
                row["risk"] = {"approved": False, "reasons": risk_result.reasons}
                results.append(row)
                continue

            final_quantity = risk_result.adjusted_quantity or proposed_quantity
            order = Order(
                symbol=signal.symbol, side=signal.action, quantity=final_quantity,
                strategy=signal.strategy, dry_run=self.execution_engine.is_dry_run,
            )

            try:
                if self.execution_engine.is_dry_run:
                    order = self.execution_engine.execute(order, current_price=price)
                else:
                    order = self.execution_engine.execute(order)
            except ExecutionError as e:
                order.status = OrderStatus.ERROR
                order.reject_reason = str(e)
                log.error("execution error on order %s: %s", order.id, e)

            storage.save_order(order)

            if order.status in (OrderStatus.FILLED, OrderStatus.SIMULATED, OrderStatus.PARTIALLY_FILLED):
                orders_placed += 1
                portfolio = storage.apply_fill(order, portfolio, current_prices)

            row["risk"] = {"approved": True, "reasons": risk_result.reasons}
            row["order"] = {
                "id": order.id, "status": order.status.value,
                "filled_price": order.filled_price, "filled_quantity": order.filled_quantity,
            }
            results.append(row)

        storage.save_portfolio_snapshot(portfolio)

        summary = {
            "status": "ok",
            "started_at": started_at.isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "mode": "paper" if self.execution_engine.is_dry_run else "live",
            "symbols_evaluated": len(market_data),
            "signals": len(signals),
            "orders_placed": orders_placed,
            "orders_rejected": orders_rejected,
            "total_equity": portfolio.total_equity,
            "cash": portfolio.cash,
            "daily_pnl_pct": portfolio.daily_pnl_pct(),
            "halted": self.risk_manager.is_halted(portfolio),
            "results": results,
        }
        log.info(
            "=== cycle complete: %d/%d signals -> orders, equity=$%.2f, daily_pnl=%s ===",
            orders_placed, len(signals), portfolio.total_equity,
            f"{summary['daily_pnl_pct']:+.2%}" if summary["daily_pnl_pct"] is not None else "n/a",
        )
        return summary

    def _size_signal(self, signal: Signal, portfolio: PortfolioState, price: float) -> float:
        """Turn a Signal's target_weight/size into an absolute quantity."""
        if signal.size is not None:
            return signal.size

        if signal.target_weight is not None:
            if signal.action == ActionType.BUY:
                target_value = signal.target_weight * portfolio.total_equity
                existing_value = portfolio.positions.get(signal.symbol).market_value if signal.symbol in portfolio.positions else 0.0
                delta_value = max(0.0, target_value - existing_value)
                return delta_value / price
            else:  # SELL toward (lower) target weight
                existing = portfolio.positions.get(signal.symbol)
                if existing is None:
                    return 0.0
                target_value = signal.target_weight * portfolio.total_equity
                delta_value = max(0.0, existing.market_value - target_value)
                return delta_value / price

        # No explicit size or target_weight (e.g. SMA crossover BUY/SELL):
        # default to a flat allocation the size of one "slot" — this keeps
        # SMA/Momentum strategies usable without also requiring a weight.
        if signal.action == ActionType.SELL:
            existing = portfolio.positions.get(signal.symbol)
            return existing.quantity if existing else 0.0

        default_slot_value = portfolio.total_equity / max(len(self.settings.symbols), 1)
        return default_slot_value / price

    # -------------------------------------------------------------------
    # continuous run
    # -------------------------------------------------------------------

    def run_forever(self, interval_seconds: Optional[int] = None) -> None:
        interval = interval_seconds or self.settings.cycle_interval_seconds
        try:
            from apscheduler.schedulers.blocking import BlockingScheduler
        except ImportError:
            log.warning("APScheduler not installed; falling back to a plain sleep loop")
            self._run_forever_plain(interval)
            return

        scheduler = BlockingScheduler()
        scheduler.add_job(self._safe_cycle, "interval", seconds=interval, next_run_time=datetime.now(timezone.utc))
        log.info("scheduling AssetManagerAgent cycle every %ds via APScheduler", interval)
        try:
            scheduler.start()
        except (KeyboardInterrupt, SystemExit):
            log.info("shutdown requested, stopping scheduler")

    def _run_forever_plain(self, interval_seconds: int) -> None:
        log.info("running plain interval loop every %ds (ctrl-C to stop)", interval_seconds)
        try:
            while True:
                self._safe_cycle()
                time.sleep(interval_seconds)
        except (KeyboardInterrupt, SystemExit):
            log.info("shutdown requested, stopping loop")

    def _safe_cycle(self) -> None:
        try:
            self.run_cycle()
        except Exception:
            log.exception("unhandled exception in run_cycle — continuing on next scheduled tick")
