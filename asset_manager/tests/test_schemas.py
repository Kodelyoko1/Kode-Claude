import pytest
from pydantic import ValidationError

from asset_manager.schemas import ActionType, Order, PortfolioState, Position, Signal


def test_position_market_value_and_pnl():
    pos = Position(symbol="ETH/USDT", quantity=2.0, avg_entry_price=1000.0, current_price=1200.0)
    assert pos.market_value == 2400.0
    assert pos.cost_basis == 2000.0
    assert pos.unrealized_pnl == 400.0
    assert pos.unrealized_pnl_pct == pytest.approx(0.2)


def test_portfolio_total_equity_and_weight_of(portfolio_with_btc):
    assert portfolio_with_btc.total_equity == 10_000.0
    assert portfolio_with_btc.weight_of("BTC/USDT") == pytest.approx(0.5)
    assert portfolio_with_btc.weight_of("ETH/USDT") == 0.0


def test_portfolio_daily_pnl():
    p = PortfolioState(cash=9_000.0, positions={}, day_start_equity=10_000.0)
    assert p.daily_pnl() == -1000.0
    assert p.daily_pnl_pct() == pytest.approx(-0.10)


def test_portfolio_daily_pnl_none_without_day_start():
    p = PortfolioState(cash=9_000.0, positions={})
    assert p.daily_pnl() is None
    assert p.daily_pnl_pct() is None


def test_signal_rejects_out_of_range_weight():
    with pytest.raises(ValidationError):
        Signal(symbol="BTC/USDT", action=ActionType.BUY, target_weight=1.5)


def test_signal_rejects_negative_size():
    with pytest.raises(ValidationError):
        Signal(symbol="BTC/USDT", action=ActionType.BUY, size=-1.0)


def test_signal_hold_is_valid_with_no_size():
    s = Signal(symbol="BTC/USDT", action=ActionType.HOLD)
    assert s.action == ActionType.HOLD
    assert s.size is None


def test_order_rejects_non_positive_quantity():
    with pytest.raises(ValidationError):
        Order(symbol="BTC/USDT", side=ActionType.BUY, quantity=0)


def test_order_rejects_hold_side():
    with pytest.raises(ValidationError):
        Order(symbol="BTC/USDT", side=ActionType.HOLD, quantity=1.0)


def test_candle_rejects_high_below_low():
    from datetime import datetime, timezone
    from pydantic import ValidationError as VE
    from asset_manager.schemas import Candle
    with pytest.raises(VE):
        Candle(timestamp=datetime.now(timezone.utc), open=10, high=5, low=8, close=6)
