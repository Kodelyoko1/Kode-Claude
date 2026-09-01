"""Shared pytest fixtures — pure in-memory data, no network, no real DB file."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from asset_manager.schemas import Candle, PortfolioState, Position


def make_candles(closes: list[float], start: datetime | None = None) -> list[Candle]:
    """Build a simple oldest-first candle series from a list of closes."""
    start = start or datetime(2026, 1, 1, tzinfo=timezone.utc)
    candles = []
    prev = closes[0]
    for i, close in enumerate(closes):
        open_ = prev
        high = max(open_, close) * 1.001
        low = min(open_, close) * 0.999
        candles.append(Candle(
            timestamp=start + timedelta(days=i),
            open=open_, high=high, low=low, close=close, volume=1000.0,
        ))
        prev = close
    return candles


@pytest.fixture
def empty_portfolio() -> PortfolioState:
    return PortfolioState(cash=10_000.0, positions={}, day_start_equity=10_000.0)


@pytest.fixture
def portfolio_with_btc() -> PortfolioState:
    return PortfolioState(
        cash=5_000.0,
        positions={
            "BTC/USDT": Position(symbol="BTC/USDT", quantity=0.1, avg_entry_price=40_000.0, current_price=50_000.0),
        },
        day_start_equity=10_000.0,
    )
