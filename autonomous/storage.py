"""
JSON storage helper with safe load/save and per-agent file convention.
"""
import json
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"


def path(name: str) -> Path:
    DATA_DIR.mkdir(exist_ok=True)
    return DATA_DIR / name


def load(name: str, default=None):
    p = path(name)
    if not p.exists():
        return default if default is not None else {}
    try:
        with open(p) as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}


def save(name: str, data):
    p = path(name)
    p.parent.mkdir(exist_ok=True)
    with open(p, "w") as f:
        json.dump(data, f, indent=2, default=str)


def append(name: str, item):
    data = load(name, [])
    if not isinstance(data, list):
        data = []
    data.append(item)
    save(name, data)
    return data
