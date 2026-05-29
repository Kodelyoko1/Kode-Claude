"""
Central metrics registry for all autonomous agents.
Every agent writes performance numbers here; the dashboard reads them.
"""
import json
from datetime import datetime
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
METRICS_FILE = DATA_DIR / "agent_metrics.json"


def _load() -> dict:
    if METRICS_FILE.exists():
        try:
            with open(METRICS_FILE) as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _save(d: dict):
    DATA_DIR.mkdir(exist_ok=True)
    with open(METRICS_FILE, "w") as f:
        json.dump(d, f, indent=2)


def record(agent_key: str, **fields):
    """
    Record one cycle's metrics for an agent.
    Standard fields agents should publish:
      prospects_added, outreach_sent, conversions, active_subs, mrr,
      fulfillment_sent, fulfillment_failed, revenue_today, errors
    """
    data = _load()
    agent = data.setdefault(agent_key, {})
    agent["last_run"] = datetime.now().isoformat()

    history = agent.setdefault("history", [])
    history.append({"ts": datetime.now().isoformat(), **fields})
    if len(history) > 90:
        agent["history"] = history[-90:]

    totals = agent.setdefault("totals", {})
    for k, v in fields.items():
        if isinstance(v, (int, float)):
            totals[k] = totals.get(k, 0) + v
        else:
            agent[k] = v

    for k, v in fields.items():
        if not isinstance(v, (int, float)):
            agent[k] = v

    agent["latest"] = fields
    _save(data)


def get_all() -> dict:
    return _load()


def get(agent_key: str) -> dict:
    return _load().get(agent_key, {})


def reset(agent_key: str = ""):
    if not agent_key:
        _save({})
        return
    d = _load()
    d.pop(agent_key, None)
    _save(d)
