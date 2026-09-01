"""
PaperConnector — a zero-dependency, zero-API-key market data source.

This is the DEFAULT connector (AM_CONNECTOR=paper). It generates a
deterministic-per-symbol synthetic random walk for OHLCV data so the whole
agent — strategies, risk manager, execution engine, storage — can be
exercised end-to-end (and unit-tested) with no network access and no
exchange/broker account. Swap in `CCXTConnector` or `YFinanceConnector` for
real market data; nothing else in the pipeline needs to change.

Portfolio state for paper trading is owned by `asset_manager.storage`
(the SQLite-backed snapshot table), not by this connector — that keeps a
single source of truth between what `fetch_portfolio()` reports and what
`execution.paper_engine` updates after a simulated fill.
"""
from __future__ import annotations

import hashlib
import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..config.settings import DATA_DIR
from ..schemas import Candle, PortfolioState
from .base import BaseConnector

PRICE_STATE_PATH = DATA_DIR / "am_paper_prices.json"


def _seed_for(symbol: str) -> int:
    """Deterministic per-symbol seed so repeated runs (and tests) are stable
    unless the persisted price-state file already has a walk in progress."""
    return int(hashlib.sha256(symbol.encode()).hexdigest()[:8], 16)


def _base_price_for(symbol: str) -> float:
    """A plausible, deterministic starting price so BTC doesn't start at $3."""
    rng = random.Random(_seed_for(symbol))
    return round(rng.uniform(10, 50_000), 2)


class PaperConnector(BaseConnector):
    name = "paper"
    is_paper_only = True

    def __init__(self, starting_cash: float = 10_000.0):
        self.starting_cash = starting_cash
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._price_state = self._load_price_state()

    # -------------------------------------------------------------------
    # persisted synthetic-price state
    # -------------------------------------------------------------------

    def _load_price_state(self) -> dict:
        if PRICE_STATE_PATH.exists():
            try:
                return json.loads(PRICE_STATE_PATH.read_text())
            except (OSError, json.JSONDecodeError):
                pass
        return {}

    def _save_price_state(self) -> None:
        try:
            PRICE_STATE_PATH.write_text(json.dumps(self._price_state, indent=2))
        except OSError:
            pass

    def _current_price(self, symbol: str) -> float:
        entry = self._price_state.get(symbol)
        if entry is not None:
            return entry["price"]
        price = _base_price_for(symbol)
        self._price_state[symbol] = {"price": price, "seed_offset": 0}
        return price

    # -------------------------------------------------------------------
    # BaseConnector
    # -------------------------------------------------------------------

    def fetch_ohlcv(self, symbol: str, timeframe: str = "1d", limit: int = 200) -> list[Candle]:
        rng = random.Random(_seed_for(symbol) + self._price_state.get(symbol, {}).get("seed_offset", 0))
        price = self._current_price(symbol)
        step = _timeframe_to_timedelta(timeframe)
        now = datetime.now(timezone.utc)

        candles: list[Candle] = []
        # Walk BACKWARD from the current price so the last candle's close
        # equals the persisted "current" price, then present oldest-first.
        walk = [price]
        for _ in range(limit - 1):
            drift = rng.uniform(-0.02, 0.021)  # slight upward bias, like most markets over time
            walk.append(max(0.01, walk[-1] / (1 + drift)))
        walk.reverse()

        for i, close in enumerate(walk):
            open_ = walk[i - 1] if i > 0 else close * (1 - rng.uniform(-0.005, 0.005))
            high = max(open_, close) * (1 + abs(rng.uniform(0, 0.008)))
            low = min(open_, close) * (1 - abs(rng.uniform(0, 0.008)))
            ts = now - step * (len(walk) - 1 - i)
            candles.append(Candle(
                timestamp=ts, open=round(open_, 6), high=round(high, 6),
                low=round(low, 6), close=round(close, 6),
                volume=round(rng.uniform(100, 10_000), 2),
            ))

        # advance the persisted walk by one step for the *next* fetch, so
        # consecutive agent cycles see prices continuing to move rather than
        # replaying the same window.
        new_price = max(0.01, price * (1 + rng.uniform(-0.02, 0.021)))
        self._price_state[symbol] = {
            "price": round(new_price, 6),
            "seed_offset": self._price_state.get(symbol, {}).get("seed_offset", 0) + 1,
        }
        self._save_price_state()
        return candles

    def fetch_price(self, symbol: str) -> float:
        return self._current_price(symbol)

    def fetch_portfolio(self) -> PortfolioState:
        from .. import storage
        snapshot = storage.load_latest_portfolio()
        if snapshot is not None:
            return snapshot
        return PortfolioState(cash=self.starting_cash, positions={}, day_start_equity=self.starting_cash)


def _timeframe_to_timedelta(timeframe: str) -> timedelta:
    unit = timeframe[-1]
    try:
        n = int(timeframe[:-1])
    except ValueError:
        n = 1
    return {
        "m": timedelta(minutes=n),
        "h": timedelta(hours=n),
        "d": timedelta(days=n),
        "w": timedelta(weeks=n),
    }.get(unit, timedelta(days=1))
