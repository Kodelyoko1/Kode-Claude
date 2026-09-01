import pytest

from asset_manager.config.settings import RiskLimitsConfig
from asset_manager.risk.manager import RiskManager
from asset_manager.schemas import ActionType, PortfolioState, Position, Signal

DEFAULT_LIMITS = RiskLimitsConfig(
    max_allocation_pct=0.25,
    max_daily_loss_pct=0.05,
    max_slippage_pct=0.01,
    max_position_notional=None,
    max_orders_per_cycle=5,
    min_order_notional=10.0,
)


def buy_signal(symbol="BTC/USDT") -> Signal:
    return Signal(symbol=symbol, action=ActionType.BUY, strategy="test")


def sell_signal(symbol="BTC/USDT") -> Signal:
    return Signal(symbol=symbol, action=ActionType.SELL, strategy="test")


def test_hold_signal_never_approved():
    rm = RiskManager(DEFAULT_LIMITS)
    hold = Signal(symbol="BTC/USDT", action=ActionType.HOLD)
    result = rm.evaluate(hold, PortfolioState(cash=10_000.0), current_price=100.0, proposed_quantity=1.0)
    assert result.approved is False
    assert "HOLD" in result.reasons[0]


def test_max_allocation_pct_trims_oversized_buy(empty_portfolio):
    rm = RiskManager(DEFAULT_LIMITS)
    # 1.0 BTC @ $5,000 = $5,000 notional against $10,000 equity = 50%, over the 25% cap.
    result = rm.evaluate(
        buy_signal(), empty_portfolio, current_price=5_000.0,
        proposed_quantity=1.0, reference_price=5_000.0,
    )
    assert result.approved is True
    assert result.adjusted_quantity == pytest.approx(0.5)  # trimmed to 25% of $10,000 / $5,000


def test_rejects_when_already_at_allocation_cap():
    portfolio = PortfolioState(
        cash=0.0,
        positions={"BTC/USDT": Position(symbol="BTC/USDT", quantity=1.0, avg_entry_price=5_000, current_price=5_000)},
        day_start_equity=5_000.0,
    )
    rm = RiskManager(DEFAULT_LIMITS)
    result = rm.evaluate(buy_signal(), portfolio, current_price=5_000.0, proposed_quantity=0.1, reference_price=5_000.0)
    assert result.approved is False
    assert "max_allocation_pct" in result.reasons[0]


def test_daily_loss_halts_new_buys():
    portfolio = PortfolioState(cash=9_400.0, positions={}, day_start_equity=10_000.0)  # -6% today
    rm = RiskManager(DEFAULT_LIMITS)
    result = rm.evaluate(buy_signal(), portfolio, current_price=100.0, proposed_quantity=1.0, reference_price=100.0)
    assert result.approved is False
    assert "daily loss" in result.reasons[0]


def test_daily_loss_still_allows_sells_to_reduce_risk():
    portfolio = PortfolioState(
        cash=0.0,
        positions={"BTC/USDT": Position(symbol="BTC/USDT", quantity=1.0, avg_entry_price=9_000, current_price=9_400)},
        day_start_equity=10_000.0,  # -6% today
    )
    rm = RiskManager(DEFAULT_LIMITS)
    result = rm.evaluate(sell_signal(), portfolio, current_price=9_400.0, proposed_quantity=1.0, reference_price=9_400.0)
    assert result.approved is True


def test_min_order_notional_rejects_dust(empty_portfolio):
    rm = RiskManager(DEFAULT_LIMITS)
    result = rm.evaluate(buy_signal(), empty_portfolio, current_price=100.0, proposed_quantity=0.001, reference_price=100.0)
    assert result.approved is False
    assert "min_order_notional" in result.reasons[0]


def test_max_slippage_pct_rejects(empty_portfolio):
    rm = RiskManager(DEFAULT_LIMITS)
    # 2% slippage vs. a 1% tolerance
    result = rm.evaluate(buy_signal(), empty_portfolio, current_price=102.0, proposed_quantity=1.0, reference_price=100.0)
    assert result.approved is False
    assert "slippage" in result.reasons[0]


def test_insufficient_cash_trims_buy():
    portfolio = PortfolioState(
        cash=1_000.0,
        positions={"ETH/USDT": Position(symbol="ETH/USDT", quantity=1.0, avg_entry_price=5_000, current_price=5_000)},
        day_start_equity=6_000.0,
    )
    limits = DEFAULT_LIMITS.model_copy(update={"max_allocation_pct": 1.0})
    rm = RiskManager(limits)
    result = rm.evaluate(buy_signal(), portfolio, current_price=3_000.0, proposed_quantity=1.0, reference_price=3_000.0)
    assert result.approved is True
    assert result.adjusted_quantity == pytest.approx(1_000.0 / 3_000.0)


def test_sell_trimmed_to_held_quantity():
    portfolio = PortfolioState(
        cash=0.0,
        positions={"BTC/USDT": Position(symbol="BTC/USDT", quantity=0.5, avg_entry_price=9_000, current_price=9_400)},
        day_start_equity=10_000.0,
    )
    rm = RiskManager(DEFAULT_LIMITS)
    result = rm.evaluate(sell_signal(), portfolio, current_price=9_400.0, proposed_quantity=2.0, reference_price=9_400.0)
    assert result.approved is True
    assert result.adjusted_quantity == pytest.approx(0.5)


def test_sell_rejected_when_no_position(empty_portfolio):
    rm = RiskManager(DEFAULT_LIMITS)
    result = rm.evaluate(sell_signal(), empty_portfolio, current_price=100.0, proposed_quantity=1.0, reference_price=100.0)
    assert result.approved is False
    assert "no position" in result.reasons[0]


def test_max_orders_per_cycle_circuit_breaker(empty_portfolio):
    rm = RiskManager(DEFAULT_LIMITS)
    result = rm.evaluate(
        buy_signal(), empty_portfolio, current_price=100.0, proposed_quantity=1.0,
        reference_price=100.0, orders_already_this_cycle=5,
    )
    assert result.approved is False
    assert "max_orders_per_cycle" in result.reasons[0]


def test_is_halted():
    rm = RiskManager(DEFAULT_LIMITS)
    ok = PortfolioState(cash=9_800.0, positions={}, day_start_equity=10_000.0)   # -2%
    bad = PortfolioState(cash=9_400.0, positions={}, day_start_equity=10_000.0)  # -6%
    assert rm.is_halted(ok) is False
    assert rm.is_halted(bad) is True
