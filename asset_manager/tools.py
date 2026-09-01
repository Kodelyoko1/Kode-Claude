"""
tools.py — the repo's standard per-agent entry point (`run_full_cycle()`),
so `run_asset_manager_auto.py` can wrap it with
`autonomous.self_healing.with_healing()` / `run_with_healing()` exactly
like every other agent in this repo.
"""
from __future__ import annotations

from .agent import AssetManagerAgent


def run_full_cycle() -> dict:
    """Build the agent fresh from Settings + config/*.yaml and run one cycle.

    Building the agent fresh (rather than reusing a module-level singleton)
    means edits to `.env` or `config/*.yaml` between cron runs take effect
    immediately with no restart needed.
    """
    agent = AssetManagerAgent()
    return agent.run_cycle()
