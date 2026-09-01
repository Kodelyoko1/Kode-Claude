"""
AI Asset Manager Agent
=======================
Autonomous portfolio monitoring, analysis, and (paper or live) trade
execution across crypto, equities, and custom asset universes.

Package layout:
    config/      — Pydantic settings + YAML risk-limit / target-allocation configs
    connectors/  — BaseConnector + concrete data/broker adapters (ccxt, yfinance, paper)
    strategies/  — BaseStrategy + concrete tactics (rebalance, SMA crossover, momentum)
    risk/        — Pre-trade RiskManager (hard guardrails)
    execution/   — BaseExecutionEngine + paper/live order routing
    schemas.py   — Pydantic data contracts shared across every module
    storage.py   — SQLite persistence for orders / snapshots / signals
    agent.py     — Orchestration loop (single cycle or scheduled/continuous)
    tools.py     — run_full_cycle() entry point (repo's standard agent shape)

Paper trading is ON by default everywhere in this package. Nothing here can
place a real order unless AM_PAPER_TRADING=0 is explicitly set AND a live
connector is explicitly selected.
"""

__version__ = "0.1.0"
