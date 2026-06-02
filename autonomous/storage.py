"""
JSON storage helper with safe load/save and per-agent file convention.
"""
import json
import os
import tempfile
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
    """Atomic JSON write: serialize to a tmp file in the same directory, then
    os.replace() onto the target. If the process dies mid-write, the original
    file is left untouched — readers never see a half-written blob."""
    p = path(name)
    p.parent.mkdir(exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=f".{p.name}.", suffix=".tmp", dir=p.parent)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2, default=str)
        os.replace(tmp_path, p)
    except Exception:
        try: os.unlink(tmp_path)
        except OSError: pass
        raise


def append(name: str, item):
    data = load(name, [])
    if not isinstance(data, list):
        data = []
    data.append(item)
    save(name, data)
    return data
