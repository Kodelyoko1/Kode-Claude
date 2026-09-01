# AI Asset Manager Agent

Autonomous portfolio monitoring, analysis, and (paper or live) trade
execution across crypto, equities, or a custom asset universe.

**Paper trading is ON by default.** Nothing in this package can place a
real order unless you explicitly set `AM_PAPER_TRADING=0` in `.env` *and*
select a live-capable connector — see [Going live](#going-live) below.

---

## Architecture

```
asset_manager/
  config/
    settings.py              # Pydantic Settings (env vars) + YAML loaders
    risk_limits.yaml          # hard risk guardrails (safe to commit, no secrets)
    target_allocations.yaml   # target portfolio weights for the Rebalance strategy
  connectors/
    base.py                   # BaseConnector abstract interface
    paper_connector.py         # zero-dependency synthetic market data (the default)
    ccxt_connector.py          # crypto exchanges via ccxt (optional dependency)
    yfinance_connector.py      # equities via yfinance + optional Alpaca execution
  strategies/
    base.py                   # BaseStrategy abstract interface
    rebalance.py               # target-weight portfolio rebalancing (default)
    sma_crossover.py            # SMA fast/slow crossover trend-following
    momentum.py                 # trailing-return ranked momentum
  risk/
    manager.py                 # pre-trade RiskManager — the one choke point every order passes through
  execution/
    base.py                    # BaseExecutionEngine abstract interface
    paper_engine.py              # dry-run fill simulation (the default)
    live_engine.py                # real order routing via a live connector
  schemas.py                    # Pydantic data contracts (Signal, Order, PortfolioState, ...)
  storage.py                    # SQLite persistence (data/asset_manager.db)
  logging_config.py              # structured logging setup
  agent.py                       # main orchestration loop
  tools.py                       # run_full_cycle() — the repo's standard agent entry shape
  tests/                          # pytest unit tests (strategies, risk, schemas)

run_asset_manager_auto.py         # top-level CLI entry point
requirements-asset-manager.txt    # this agent's Python dependencies
```

Data flows one way through the pipeline: **connector → strategy → risk
manager → execution engine → storage**. Every stage speaks the Pydantic
models in `schemas.py`, so a malformed signal or order is rejected at
construction time instead of surfacing as a `KeyError` three modules
downstream.

## Safety mechanics

- **Paper trading by default** (`AM_PAPER_TRADING=1`). The execution-engine
  factory (`execution.get_execution_engine`) only ever returns
  `LiveExecutionEngine` when `paper_trading` is explicitly `False` **and**
  the selected connector reports `is_paper_only = False` — both
  independently, so there's no single flag that accidentally arms live
  trading.
- **Hard pre-trade risk guardrails** (`risk/manager.py`, tuned in
  `config/risk_limits.yaml`):
  - Max allocation % per single asset (default 25% of equity) — trims
    oversized buys, rejects if already at/over cap.
  - Max daily portfolio loss threshold (default 5%) — halts new `BUY`
    orders for the rest of the day; `SELL` orders (reducing risk) still go
    through.
  - Max slippage tolerance (default 1%) — rejects an order if the fill
    price has drifted too far from the reference price.
  - Optional absolute position notional cap, a circuit breaker on orders
    per cycle, and a minimum order notional (dust filter).
- **State persistence** — every order, every signal (with the risk
  manager's verdict attached), and every portfolio snapshot is written to
  a local SQLite database at `data/asset_manager.db`.
- **Structured logging** — every module logs through
  `logging_config.get_logger()` to stdout and `data/am_logs/agent.log`,
  including each signal's rationale and each order's execution receipt.

## Quick start (zero dependencies, zero API keys)

```bash
pip install -r requirements-asset-manager.txt   # or just: pydantic pydantic-settings pyyaml
python3 run_asset_manager_auto.py               # runs one cycle against the built-in paper connector
python3 run_asset_manager_auto.py --status       # portfolio snapshot
python3 run_asset_manager_auto.py --history      # recent order history
python3 run_asset_manager_auto.py --loop         # run continuously (Ctrl-C to stop)
```

With `AM_CONNECTOR=paper` (the default), the agent generates a
deterministic synthetic price series per symbol — no exchange, broker, or
API key required — so the whole pipeline (strategy → risk → execution →
storage) can be exercised and demoed immediately.

## Configuration

Two layers, on purpose:

1. **`.env`** (`AM_*` variables, see `.env.example` at the repo root) —
   process-level config: credentials, which connector/strategy to use,
   paper vs. live mode. Never commit real credentials here.
2. **`config/risk_limits.yaml`** and **`config/target_allocations.yaml`** —
   the "business" config an owner tunes by hand. Both are safe to commit
   (no secrets) and are reloaded fresh every cycle, so editing the YAML
   takes effect on the next run with no restart.

Key `.env` variables (all optional — every one has a safe default):

| Variable | Default | Meaning |
|---|---|---|
| `AM_PAPER_TRADING` | `1` | `0` to arm live trading (also needs a live connector) |
| `AM_CONNECTOR` | `paper` | `paper` \| `ccxt` \| `yfinance` |
| `AM_STRATEGY` | `rebalance` | `rebalance` \| `sma_crossover` \| `momentum` |
| `AM_SYMBOLS` | `BTC/USDT,ETH/USDT,SOL/USDT` | comma-separated universe |
| `AM_STARTING_CASH` | `10000` | starting paper-trading cash balance |
| `AM_CYCLE_INTERVAL_SECONDS` | `300` | interval for `--loop` |
| `AM_DB_PATH` | `data/asset_manager.db` | SQLite path |
| `AM_EXCHANGE_ID` / `AM_EXCHANGE_API_KEY` / `AM_EXCHANGE_API_SECRET` | — | ccxt exchange credentials |
| `AM_ALPACA_API_KEY` / `AM_ALPACA_API_SECRET` | — | Alpaca credentials for live equities execution |

See the full list, with comments, in the repo root `.env.example`.

## Strategies

- **`rebalance`** (default) — compares each symbol's current portfolio
  weight against `config/target_allocations.yaml`; emits `BUY`/`SELL` once
  drift exceeds `AM_REBALANCE_DRIFT_THRESHOLD` (default 5%).
- **`sma_crossover`** — golden cross (`AM_SMA_FAST_PERIOD` over
  `AM_SMA_SLOW_PERIOD`) → `BUY`; death cross → `SELL` (only if a position
  is held; this agent never shorts).
- **`momentum`** — ranks the universe by trailing return over
  `AM_MOMENTUM_LOOKBACK` bars; the top `AM_MOMENTUM_TOP_N` positive
  performers get a `BUY` at `AM_MOMENTUM_ALLOC_WEIGHT` each; anything held
  that falls out of the top N gets a `SELL`.

Adding a new strategy: subclass `strategies.base.BaseStrategy`, implement
`generate_signals(market_data, portfolio) -> list[Signal]`, and register it
in `strategies/__init__.py::get_strategy()`.

## Going live

Live trading is deliberately a two-key turn:

```bash
# 1. In .env:
AM_PAPER_TRADING=0
AM_CONNECTOR=ccxt              # or yfinance (equities, via Alpaca)
AM_EXCHANGE_ID=binance
AM_EXCHANGE_API_KEY=...
AM_EXCHANGE_API_SECRET=...
AM_EXCHANGE_SANDBOX=0          # only once you're ready for real orders

# 2. Run:
python3 run_asset_manager_auto.py --live
```

`--live` and `AM_PAPER_TRADING=0` are equivalent; `--live` is there so you
never have to permanently flip the safety switch in `.env` just to test a
single live cycle.

Trading real money carries real risk of loss. Nothing in this package is
financial advice.

## Testing

```bash
pip install -r requirements-asset-manager.txt
python3 -m pytest asset_manager/tests -v
```

Tests run entirely offline against synthetic `Candle`/`PortfolioState`
fixtures (`tests/conftest.py`) — no network access, no real database file.

## Scheduling

`run_asset_manager_auto.py --loop` uses [APScheduler](https://apscheduler.readthedocs.io/)
if installed (falls back to a plain `time.sleep` loop otherwise) to run a
cycle every `AM_CYCLE_INTERVAL_SECONDS`. For cron-style scheduling instead,
add a line like:

```
*/5 * * * * cd /path/to/repo && python3 run_asset_manager_auto.py >> data/am_logs/cron.log 2>&1
```

Every run is wrapped in `autonomous.self_healing.run_with_healing()`, so a
transient network error or a truncated JSON/SQLite hiccup gets retried
with backoff rather than taking the agent offline, and the owner is
emailed after `HEAL_ESCALATE_AFTER` (default 3) consecutive failed cycles.
