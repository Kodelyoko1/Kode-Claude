"""
SQLite persistence for the AI Asset Manager Agent — local trading state,
order history, signal history, and portfolio snapshots (per the project
spec's "State Persistence" requirement).

Schema (three tables, all append-only except `portfolio_snapshots`, which
is append-only too — we always INSERT a new snapshot rather than UPDATE,
so `load_latest_portfolio()` reading the most recent row is equivalent to
having history, and nothing here ever loses data on a crash mid-write
because SQLite's own transaction commit is the atomicity boundary):

  orders               — one row per Order, updated on execution
  signals              — one row per Signal a strategy emitted, with the
                          RiskManager's verdict recorded alongside it
  portfolio_snapshots  — one row per PortfolioState observed, so
                          `daily_pnl_pct()` and audit/backtest tooling can
                          reconstruct the equity curve over time
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

from .config.settings import get_settings
from .logging_config import get_logger
from .schemas import Order, PortfolioState, Position, RiskCheckResult, Signal

log = get_logger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    id                TEXT PRIMARY KEY,
    symbol            TEXT NOT NULL,
    side              TEXT NOT NULL,
    order_type        TEXT NOT NULL,
    quantity          REAL NOT NULL,
    limit_price       REAL,
    status            TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    filled_price      REAL,
    filled_quantity   REAL,
    slippage_pct      REAL,
    dry_run           INTEGER NOT NULL,
    reject_reason     TEXT,
    strategy          TEXT,
    receipt_json      TEXT
);

CREATE TABLE IF NOT EXISTS signals (
    rowid_ts          TEXT NOT NULL,
    symbol            TEXT NOT NULL,
    action            TEXT NOT NULL,
    target_weight     REAL,
    size              REAL,
    confidence        REAL NOT NULL,
    rationale         TEXT,
    strategy          TEXT,
    generated_at      TEXT NOT NULL,
    risk_approved     INTEGER,
    risk_reasons_json TEXT
);

CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    cash              REAL NOT NULL,
    positions_json     TEXT NOT NULL,
    timestamp         TEXT NOT NULL,
    day_start_equity  REAL
);

CREATE INDEX IF NOT EXISTS idx_orders_created_at ON orders (created_at);
CREATE INDEX IF NOT EXISTS idx_signals_generated_at ON signals (generated_at);
CREATE INDEX IF NOT EXISTS idx_snapshots_timestamp ON portfolio_snapshots (timestamp);
"""


def _db_path() -> Path:
    return get_settings().db_path


def init_db(path: Optional[Path] = None) -> None:
    path = path or _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _connect(path) as conn:
        conn.executescript(SCHEMA)


@contextmanager
def _connect(path: Optional[Path] = None) -> Iterator[sqlite3.Connection]:
    path = path or _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# =============================================================================
# ORDERS
# =============================================================================

def save_order(order: Order, path: Optional[Path] = None) -> None:
    init_db(path)
    with _connect(path) as conn:
        conn.execute(
            """
            INSERT INTO orders (
                id, symbol, side, order_type, quantity, limit_price, status,
                created_at, filled_price, filled_quantity, slippage_pct,
                dry_run, reject_reason, strategy, receipt_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                status=excluded.status, filled_price=excluded.filled_price,
                filled_quantity=excluded.filled_quantity, slippage_pct=excluded.slippage_pct,
                reject_reason=excluded.reject_reason, receipt_json=excluded.receipt_json
            """,
            (
                order.id, order.symbol, order.side.value, order.order_type.value,
                order.quantity, order.limit_price, order.status.value,
                order.created_at.isoformat(), order.filled_price, order.filled_quantity,
                order.slippage_pct, int(order.dry_run), order.reject_reason,
                order.strategy, json.dumps(order.receipt, default=str),
            ),
        )
    log.debug("saved order %s (%s %s x%.8g) status=%s", order.id, order.side, order.symbol, order.quantity, order.status)


def load_recent_orders(limit: int = 50, path: Optional[Path] = None) -> list[dict]:
    init_db(path)
    with _connect(path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM orders ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


# =============================================================================
# SIGNALS
# =============================================================================

def save_signal(signal: Signal, risk_result: Optional[RiskCheckResult] = None, path: Optional[Path] = None) -> None:
    init_db(path)
    with _connect(path) as conn:
        conn.execute(
            """
            INSERT INTO signals (
                rowid_ts, symbol, action, target_weight, size, confidence,
                rationale, strategy, generated_at, risk_approved, risk_reasons_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now(timezone.utc).isoformat(),
                signal.symbol, signal.action.value, signal.target_weight, signal.size,
                signal.confidence, signal.rationale, signal.strategy,
                signal.generated_at.isoformat(),
                None if risk_result is None else int(risk_result.approved),
                None if risk_result is None else json.dumps(risk_result.reasons),
            ),
        )


def load_recent_signals(limit: int = 100, path: Optional[Path] = None) -> list[dict]:
    init_db(path)
    with _connect(path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM signals ORDER BY generated_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


# =============================================================================
# PORTFOLIO SNAPSHOTS
# =============================================================================

def save_portfolio_snapshot(portfolio: PortfolioState, path: Optional[Path] = None) -> None:
    init_db(path)
    positions_json = json.dumps({s: p.model_dump(mode="json") for s, p in portfolio.positions.items()})
    with _connect(path) as conn:
        conn.execute(
            "INSERT INTO portfolio_snapshots (cash, positions_json, timestamp, day_start_equity) VALUES (?, ?, ?, ?)",
            (portfolio.cash, positions_json, portfolio.timestamp.isoformat(), portfolio.day_start_equity),
        )


def load_latest_portfolio(path: Optional[Path] = None) -> Optional[PortfolioState]:
    init_db(path)
    with _connect(path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM portfolio_snapshots ORDER BY id DESC LIMIT 1"
        ).fetchone()
    if row is None:
        return None
    positions_raw = json.loads(row["positions_json"])
    positions = {s: Position(**p) for s, p in positions_raw.items()}
    return PortfolioState(
        cash=row["cash"], positions=positions,
        timestamp=row["timestamp"], day_start_equity=row["day_start_equity"],
    )


def roll_day_start_equity_if_new_day(portfolio: PortfolioState, path: Optional[Path] = None) -> PortfolioState:
    """
    Reset `day_start_equity` to the current total_equity the first time a
    snapshot is taken on a new UTC calendar day, so RiskManager's daily-loss
    guardrail measures loss-since-market-open-today, not loss-since-forever.
    """
    today = date.today().isoformat()
    last_reset_marker = _load_marker("day_start_marker", path)
    if last_reset_marker != today or portfolio.day_start_equity is None:
        portfolio.day_start_equity = portfolio.total_equity
        _save_marker("day_start_marker", today, path)
    return portfolio


def _markers_table(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS markers (key TEXT PRIMARY KEY, value TEXT NOT NULL)")


def _load_marker(key: str, path: Optional[Path] = None) -> Optional[str]:
    init_db(path)
    with _connect(path) as conn:
        _markers_table(conn)
        row = conn.execute("SELECT value FROM markers WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def _save_marker(key: str, value: str, path: Optional[Path] = None) -> None:
    init_db(path)
    with _connect(path) as conn:
        _markers_table(conn)
        conn.execute(
            "INSERT INTO markers (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


# =============================================================================
# FILL APPLICATION
# =============================================================================

def apply_fill(order: Order, portfolio: PortfolioState, current_prices: Optional[dict[str, float]] = None) -> PortfolioState:
    """
    Given a filled/simulated Order, return a new PortfolioState with cash
    and the affected position updated, mark-to-market every other held
    position against `current_prices` if supplied, and persist the result
    as the new latest snapshot. Does not mutate `portfolio` in place.
    """
    if order.status.value not in ("filled", "simulated", "partially_filled"):
        raise ValueError(f"apply_fill() called on an order that wasn't filled: status={order.status}")

    fill_qty = order.filled_quantity or 0.0
    fill_price = order.filled_price or 0.0
    notional = fill_qty * fill_price

    positions = dict(portfolio.positions)
    cash = portfolio.cash

    if order.side.value == "BUY":
        cash -= notional
        existing = positions.get(order.symbol)
        if existing and existing.quantity > 0:
            total_qty = existing.quantity + fill_qty
            avg_price = (existing.cost_basis + notional) / total_qty if total_qty else fill_price
            positions[order.symbol] = Position(
                symbol=order.symbol, quantity=total_qty,
                avg_entry_price=avg_price, current_price=fill_price,
            )
        else:
            positions[order.symbol] = Position(
                symbol=order.symbol, quantity=fill_qty,
                avg_entry_price=fill_price, current_price=fill_price,
            )
    else:  # SELL
        cash += notional
        existing = positions.get(order.symbol)
        remaining = max(0.0, (existing.quantity if existing else 0.0) - fill_qty)
        if remaining <= 1e-12:
            positions.pop(order.symbol, None)
        else:
            positions[order.symbol] = Position(
                symbol=order.symbol, quantity=remaining,
                avg_entry_price=existing.avg_entry_price, current_price=fill_price,
            )

    if current_prices:
        for symbol, price in current_prices.items():
            if symbol in positions:
                positions[symbol] = positions[symbol].model_copy(update={"current_price": price})

    new_portfolio = PortfolioState(
        cash=cash, positions=positions,
        day_start_equity=portfolio.day_start_equity,
    )
    save_portfolio_snapshot(new_portfolio)
    return new_portfolio
