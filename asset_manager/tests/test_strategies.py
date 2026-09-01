import pytest

from asset_manager.schemas import ActionType, Position, PortfolioState
from asset_manager.strategies.momentum import MomentumStrategy
from asset_manager.strategies.rebalance import RebalanceStrategy
from asset_manager.strategies.sma_crossover import SMACrossoverStrategy

from .conftest import make_candles


# =============================================================================
# RebalanceStrategy
# =============================================================================

def test_rebalance_holds_when_within_threshold(portfolio_with_btc):
    strat = RebalanceStrategy(target_weights={"BTC/USDT": 0.5}, drift_threshold=0.05)
    market_data = {"BTC/USDT": make_candles([50_000.0])}
    signals = strat.generate_signals(market_data, portfolio_with_btc)
    assert len(signals) == 1
    assert signals[0].action == ActionType.HOLD


def test_rebalance_buys_when_underweight(empty_portfolio):
    strat = RebalanceStrategy(target_weights={"BTC/USDT": 0.5}, drift_threshold=0.05)
    market_data = {"BTC/USDT": make_candles([50_000.0])}
    signals = strat.generate_signals(market_data, empty_portfolio)
    assert signals[0].action == ActionType.BUY
    assert signals[0].target_weight == 0.5


def test_rebalance_sells_when_overweight():
    portfolio = PortfolioState(
        cash=0.0,
        positions={"BTC/USDT": Position(symbol="BTC/USDT", quantity=1.0, avg_entry_price=10_000, current_price=10_000)},
        day_start_equity=10_000.0,
    )
    strat = RebalanceStrategy(target_weights={"BTC/USDT": 0.2}, drift_threshold=0.05)
    market_data = {"BTC/USDT": make_candles([10_000.0])}
    signals = strat.generate_signals(market_data, portfolio)
    assert signals[0].action == ActionType.SELL


def test_rebalance_rejects_bad_weight():
    with pytest.raises(ValueError):
        RebalanceStrategy(target_weights={"BTC/USDT": 1.5})


def test_rebalance_one_signal_per_symbol(empty_portfolio):
    strat = RebalanceStrategy(target_weights={"BTC/USDT": 0.5, "ETH/USDT": 0.3})
    market_data = {"BTC/USDT": make_candles([50_000.0]), "ETH/USDT": make_candles([3_000.0])}
    signals = strat.generate_signals(market_data, empty_portfolio)
    assert {s.symbol for s in signals} == {"BTC/USDT", "ETH/USDT"}


# =============================================================================
# SMACrossoverStrategy
# =============================================================================

def test_sma_golden_cross_triggers_buy(empty_portfolio):
    strat = SMACrossoverStrategy(fast_period=2, slow_period=3)
    market_data = {"BTC/USDT": make_candles([10.0, 10.0, 10.0, 13.0])}
    signals = strat.generate_signals(market_data, empty_portfolio)
    assert signals[0].action == ActionType.BUY
    assert "golden cross" in signals[0].rationale


def test_sma_death_cross_triggers_sell_only_when_holding(portfolio_with_btc):
    strat = SMACrossoverStrategy(fast_period=2, slow_period=3)
    market_data = {"BTC/USDT": make_candles([13.0, 13.0, 13.0, 10.0])}
    signals = strat.generate_signals(market_data, portfolio_with_btc)
    assert signals[0].action == ActionType.SELL


def test_sma_death_cross_without_position_holds(empty_portfolio):
    strat = SMACrossoverStrategy(fast_period=2, slow_period=3)
    market_data = {"BTC/USDT": make_candles([13.0, 13.0, 13.0, 10.0])}
    signals = strat.generate_signals(market_data, empty_portfolio)
    assert signals[0].action == ActionType.HOLD


def test_sma_insufficient_history_holds(empty_portfolio):
    strat = SMACrossoverStrategy(fast_period=5, slow_period=10)
    market_data = {"BTC/USDT": make_candles([10.0, 11.0, 12.0])}
    signals = strat.generate_signals(market_data, empty_portfolio)
    assert signals[0].action == ActionType.HOLD
    assert "insufficient history" in signals[0].rationale


def test_sma_rejects_fast_not_less_than_slow():
    with pytest.raises(ValueError):
        SMACrossoverStrategy(fast_period=50, slow_period=20)


# =============================================================================
# MomentumStrategy
# =============================================================================

def test_momentum_buys_top_performer(empty_portfolio):
    strat = MomentumStrategy(lookback=3, top_n=1, alloc_weight=0.2)
    market_data = {
        "WIN/USDT": make_candles([100.0, 110.0, 121.0]),   # +21%
        "LOSE/USDT": make_candles([100.0, 95.0, 90.0]),     # -10%
    }
    signals = {s.symbol: s for s in strat.generate_signals(market_data, empty_portfolio)}
    assert signals["WIN/USDT"].action == ActionType.BUY
    assert signals["WIN/USDT"].target_weight == 0.2
    assert signals["LOSE/USDT"].action == ActionType.HOLD


def test_momentum_sells_held_position_that_fell_out_of_top_n():
    portfolio = PortfolioState(
        cash=5_000.0,
        positions={"LOSE/USDT": Position(symbol="LOSE/USDT", quantity=10.0, avg_entry_price=100.0, current_price=90.0)},
        day_start_equity=10_000.0,
    )
    strat = MomentumStrategy(lookback=3, top_n=1, alloc_weight=0.2)
    market_data = {
        "WIN/USDT": make_candles([100.0, 110.0, 121.0]),
        "LOSE/USDT": make_candles([100.0, 95.0, 90.0]),
    }
    signals = {s.symbol: s for s in strat.generate_signals(market_data, portfolio)}
    assert signals["LOSE/USDT"].action == ActionType.SELL


def test_momentum_insufficient_history_holds(empty_portfolio):
    strat = MomentumStrategy(lookback=10, top_n=1)
    market_data = {"BTC/USDT": make_candles([100.0, 101.0])}
    signals = strat.generate_signals(market_data, empty_portfolio)
    assert signals[0].action == ActionType.HOLD


def test_momentum_rejects_bad_alloc_weight():
    with pytest.raises(ValueError):
        MomentumStrategy(alloc_weight=1.5)
