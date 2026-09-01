"""
Structured logging for the AI Asset Manager Agent.

Uses the standard library `logging` module (no extra dependency) with a
formatter that always includes a timestamp, level, module, and message —
every log line here is meant to answer "what did the agent decide, and
why" on its own, without cross-referencing other lines. Signal rationale
and order receipts (see strategies/*, risk/manager.py, execution/*) are
logged verbatim so a human can audit a trading decision after the fact.

Configure once via `configure_logging()` (called from agent.py /
run_asset_manager_auto.py); every module calls `get_logger(__name__)`.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

from .config.settings import DATA_DIR

LOG_DIR = DATA_DIR / "am_logs"
LOG_FORMAT = "%(asctime)s.%(msecs)03dZ [%(levelname)-7s] %(name)s: %(message)s"
DATE_FORMAT = "%Y-%m-%dT%H:%M:%S"

_configured = False


def configure_logging(level: int = logging.INFO, to_file: bool = True) -> None:
    """Idempotent — safe to call from every entry point without duplicating handlers."""
    global _configured
    if _configured:
        return

    root = logging.getLogger("asset_manager")
    root.setLevel(level)
    root.propagate = False

    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    root.addHandler(stream_handler)

    if to_file:
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            file_handler = logging.FileHandler(LOG_DIR / "agent.log")
            file_handler.setFormatter(formatter)
            root.addHandler(file_handler)
        except OSError:
            root.warning("could not open log file under %s; logging to stdout only", LOG_DIR)

    _configured = True


def get_logger(name: str) -> logging.Logger:
    if not _configured:
        configure_logging()
    # Callers pass __name__, which is already "asset_manager.xyz" for every
    # module inside this package (and a bare "agent"/"storage" for the few
    # that pass a short name directly) — avoid double-prefixing either way.
    qualified = name if name == "asset_manager" or name.startswith("asset_manager.") else f"asset_manager.{name}"
    return logging.getLogger(qualified)
