"""
Universal self-healing wrapper for every agent's cycle function.

Each `run_*_auto.py` opts in by replacing its `cycle()` call with
`run_with_healing("agent_key", cycle)`. The wrapper:

    1. Classifies any exception the cycle raises.
    2. For recoverable errors (truncated JSON, transient network), attempts
       repair + retries with exponential backoff.
    3. For code-bug errors (KeyError, ImportError, AttributeError), logs and
       bails immediately — retrying won't fix a typo.
    4. Tracks per-agent state in data/.healing/<agent>.json
       (consecutive_failures, last_success, last_error).
    5. After 3 consecutive failed cycles, escalates by emailing the owner.

Goal: keep the fleet running through transient breakage without the cron
script having to crash. Persistent breakage still propagates so cron's
`|| log "X failed"` line still fires and Batman can pick it up.
"""
from __future__ import annotations

import functools
import json
import os
import re
import shutil
import sys
import tempfile
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

ROOT          = Path(__file__).parent.parent
DATA_DIR      = ROOT / "data"
HEALING_DIR   = DATA_DIR / ".healing"
QUARANTINE    = DATA_DIR / ".healing_quarantine"

ESCALATION_THRESHOLD = int(os.environ.get("HEAL_ESCALATE_AFTER", "3"))
MAX_RETRIES          = int(os.environ.get("HEAL_MAX_RETRIES", "2"))


# ============================================================================
# STATE
# ============================================================================

def _state_path(agent_key: str) -> Path:
    HEALING_DIR.mkdir(parents=True, exist_ok=True)
    return HEALING_DIR / f"{agent_key}.json"


def _load_state(agent_key: str) -> dict:
    p = _state_path(agent_key)
    if not p.exists():
        return {"consecutive_failures": 0}
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return {"consecutive_failures": 0}


def _save_state(agent_key: str, state: dict) -> None:
    p = _state_path(agent_key)
    fd, tmp = tempfile.mkstemp(prefix=f".{p.name}.", suffix=".tmp", dir=p.parent)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(state, f, indent=2, default=str)
        os.replace(tmp, p)
    except Exception:
        try: os.unlink(tmp)
        except OSError: pass


# ============================================================================
# CLASSIFIER
# ============================================================================

_TRANSIENT_NAMES = {
    "ConnectionError", "ConnectionResetError", "ConnectionAbortedError",
    "ConnectionRefusedError", "Timeout", "TimeoutError", "ReadTimeout",
    "ConnectTimeout", "ChunkedEncodingError", "SSLError", "RemoteDisconnected",
    "IncompleteRead", "ProtocolError", "TooManyRedirects",
}

_CODE_BUG_NAMES = {
    "ImportError", "ModuleNotFoundError", "AttributeError",
    "TypeError", "NameError", "SyntaxError", "IndentationError",
}


def _classify(exc: BaseException) -> dict:
    """Return {action, retryable, reason}. Actions:
        recover_json — partial-recover then retry
        backoff_retry — sleep + retry
        log_and_bail — surface to caller, no retry
    """
    name = type(exc).__name__

    if name == "JSONDecodeError" or "JSONDecodeError" in name:
        return {"action": "recover_json", "retryable": True,
                "reason": "truncated/corrupt JSON — try partial recovery"}

    if name in _TRANSIENT_NAMES:
        return {"action": "backoff_retry", "retryable": True,
                "reason": "transient network/IO — retry with backoff"}

    if name == "HTTPError":
        code = 0
        resp = getattr(exc, "response", None)
        if resp is not None:
            code = getattr(resp, "status_code", 0)
        if code in (408, 429, 500, 502, 503, 504):
            return {"action": "backoff_retry", "retryable": True,
                    "reason": f"HTTP {code} — retryable server-side error"}
        return {"action": "log_and_bail", "retryable": False,
                "reason": f"HTTP {code} — not retryable"}

    if name == "PermissionError":
        return {"action": "log_and_bail", "retryable": False,
                "reason": "permission denied — needs owner attention"}

    if name == "OSError":
        if "No space left" in str(exc):
            return {"action": "log_and_bail", "retryable": False,
                    "reason": "disk full — needs owner attention"}
        return {"action": "backoff_retry", "retryable": True,
                "reason": "OS error — retry once"}

    if name in _CODE_BUG_NAMES:
        return {"action": "log_and_bail", "retryable": False,
                "reason": f"{name} — code bug, retry won't fix"}

    if name == "KeyError" or name == "ValueError":
        return {"action": "log_and_bail", "retryable": False,
                "reason": f"{name} — likely a data-shape mismatch"}

    # Unknown — retry once cautiously
    return {"action": "backoff_retry", "retryable": True,
            "reason": f"{name} — unknown class, single retry"}


# ============================================================================
# PARTIAL JSON RECOVERY
# ============================================================================

_FILE_RE = re.compile(r'(data/[A-Za-z0-9_.\-/]+\.json)')


def _files_from_traceback(tb_text: str) -> list[Path]:
    """Pull every `data/*.json` path that appears anywhere in the traceback
    so we know which file is most likely the culprit."""
    seen = []
    for m in _FILE_RE.finditer(tb_text):
        rel = m.group(1)
        p = ROOT / rel
        if p not in seen and p.exists():
            seen.append(p)
    return seen


def _top_level_boundaries(text: str) -> list[int]:
    """Walk JSON-ish text. Return positions of top-level commas (depth==1,
    outside strings) — viable truncation points for partial recovery."""
    boundaries = []
    depth, in_string, escape = 0, False, False
    for i, ch in enumerate(text):
        if escape:
            escape = False; continue
        if in_string:
            if ch == "\\":   escape = True
            elif ch == '"':  in_string = False
            continue
        if ch == '"':       in_string = True
        elif ch in "{[":    depth += 1
        elif ch in "}]":    depth -= 1
        elif ch == "," and depth == 1:
            boundaries.append(i)
    return boundaries


def attempt_partial_recovery(path: Path) -> dict:
    """Salvage the valid prefix of a truncated JSON file. Returns
    {ok, items_kept, bytes_kept, method}."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return {"ok": False, "method": f"read-failed: {e}"}

    stripped = text.lstrip()
    if not stripped:
        return {"ok": False, "method": "empty-file"}

    outer = stripped[0]
    if outer not in "{[":
        return {"ok": False, "method": "not-a-container"}

    closer = "}" if outer == "{" else "]"
    boundaries = _top_level_boundaries(text)
    if not boundaries:
        return {"ok": False, "method": "no-top-level-boundaries"}

    # Try latest boundary first — that keeps the most data
    for pos in reversed(boundaries):
        candidate = text[:pos] + closer
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        kept = len(parsed) if isinstance(parsed, (list, dict)) else 0
        return {
            "ok": True,
            "items_kept": kept,
            "bytes_kept": len(candidate),
            "method": f"truncate-at-comma-{pos}",
            "recovered_text": candidate,
        }
    return {"ok": False, "method": "all-candidates-failed"}


def _atomic_write(path: Path, content: str) -> bool:
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
        os.replace(tmp, path)
        return True
    except Exception:
        try: os.unlink(tmp)
        except OSError: pass
        return False


def _quarantine_copy(path: Path, agent_key: str) -> Optional[Path]:
    QUARANTINE.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = QUARANTINE / f"{ts}-{agent_key}-{path.name}"
    try:
        shutil.copy2(path, dst)
        return dst
    except Exception:
        return None


# ============================================================================
# REPAIR HANDLERS
# ============================================================================

def _repair_json_corruption(agent_key: str, exc: BaseException, tb_text: str) -> dict:
    """Identify the broken JSON file(s) and attempt partial recovery."""
    candidates = _files_from_traceback(tb_text)
    if not candidates:
        # Fall back to scanning all data/*.json — find the one that won't parse
        for p in sorted(DATA_DIR.glob("*.json")):
            try:
                json.loads(p.read_text())
            except (OSError, json.JSONDecodeError):
                candidates.append(p)

    if not candidates:
        return {"ok": False, "reason": "no broken file identified"}

    results = []
    for path in candidates:
        # Skip if file now parses (already fixed)
        try:
            json.loads(path.read_text())
            results.append({"file": str(path.relative_to(ROOT)),
                            "action": "already-clean"})
            continue
        except (OSError, json.JSONDecodeError):
            pass

        quar = _quarantine_copy(path, agent_key)
        rec = attempt_partial_recovery(path)
        if not rec["ok"]:
            results.append({"file": str(path.relative_to(ROOT)),
                            "action": "recovery-failed",
                            "method": rec["method"],
                            "quarantine": str(quar) if quar else None})
            continue
        ok = _atomic_write(path, rec["recovered_text"])
        results.append({
            "file": str(path.relative_to(ROOT)),
            "action": "recovered" if ok else "recovery-write-failed",
            "items_kept": rec["items_kept"],
            "bytes_kept": rec["bytes_kept"],
            "method": rec["method"],
            "quarantine": str(quar) if quar else None,
        })

    repaired_any = any(r["action"] == "recovered" for r in results)
    return {"ok": repaired_any, "results": results}


def _backoff_seconds(attempt: int) -> float:
    base = float(os.environ.get("HEAL_BACKOFF_BASE", "3.0"))
    return base * (3 ** (attempt - 1))   # 3s, 9s, 27s


# ============================================================================
# ESCALATION (email owner after N consecutive failures)
# ============================================================================

def _escalate(agent_key: str, state: dict, exc: BaseException, tb_text: str) -> None:
    if state.get("consecutive_failures", 0) < ESCALATION_THRESHOLD:
        return
    if state.get("escalated_at_count") == state["consecutive_failures"]:
        return  # already escalated for this streak

    try:
        from autonomous import mailer
    except Exception:
        return
    owner = os.environ.get("HEAL_OWNER_EMAIL", os.environ.get("SMTP_USER", ""))
    if not owner:
        return

    subject = f"[self-heal] {agent_key} failed {state['consecutive_failures']}x"
    body = (
        f"Self-heal escalation for {agent_key}.\n\n"
        f"Consecutive failures: {state['consecutive_failures']}\n"
        f"Last error: {type(exc).__name__}: {str(exc)[:300]}\n"
        f"Last success: {state.get('last_success', 'never')}\n\n"
        f"Tail of last traceback:\n{tb_text[-1500:]}\n"
    )
    try:
        mailer.send(agent_key, owner, subject, body, purpose="self_heal_escalation")
        state["escalated_at_count"] = state["consecutive_failures"]
        state["last_escalation"] = datetime.now().isoformat()
    except Exception:
        pass


# ============================================================================
# PUBLIC ENTRY POINT
# ============================================================================

def run_with_healing(agent_key: str,
                     cycle_fn: Callable[[], Any],
                     max_retries: int = MAX_RETRIES) -> Any:
    """Run a cycle function with classification-based self-healing.

    Returns whatever cycle_fn returns on success (possibly after recovery+retry).
    On unrecoverable failure, prints diagnostic, updates state, and re-raises
    so cron / the parent script still sees a nonzero exit."""
    state = _load_state(agent_key)
    state.setdefault("consecutive_failures", 0)
    state["last_attempt"] = datetime.now().isoformat()

    attempt = 0
    last_exc: Optional[BaseException] = None
    last_tb = ""

    while attempt <= max_retries:
        attempt += 1
        try:
            result = cycle_fn()
        except BaseException as e:
            last_exc = e
            last_tb = traceback.format_exc()
            cls = _classify(e)
            print(f"[self-heal] {agent_key}: attempt {attempt}/{max_retries+1} "
                  f"raised {type(e).__name__} → {cls['action']} ({cls['reason']})",
                  file=sys.stderr)

            if cls["action"] == "recover_json":
                repair = _repair_json_corruption(agent_key, e, last_tb)
                print(f"[self-heal] {agent_key}: JSON repair → "
                      f"ok={repair.get('ok')} results={repair.get('results')}",
                      file=sys.stderr)
                if not repair.get("ok"):
                    break  # can't repair → no point retrying

            elif cls["action"] == "backoff_retry":
                if attempt > max_retries:
                    break
                delay = _backoff_seconds(attempt)
                print(f"[self-heal] {agent_key}: sleeping {delay:.1f}s before retry",
                      file=sys.stderr)
                time.sleep(delay)

            else:  # log_and_bail
                break

            if attempt > max_retries:
                break
            continue

        # Success path
        state["consecutive_failures"] = 0
        state["last_success"] = datetime.now().isoformat()
        state["last_error"] = None
        state["healing_attempts_used"] = attempt - 1
        _save_state(agent_key, state)
        return result

    # All attempts failed
    state["consecutive_failures"] = state.get("consecutive_failures", 0) + 1
    state["last_failure"] = datetime.now().isoformat()
    if last_exc is not None:
        state["last_error"] = f"{type(last_exc).__name__}: {str(last_exc)[:300]}"
    _save_state(agent_key, state)
    _escalate(agent_key, state, last_exc, last_tb)

    # Re-raise so the calling script's cron line still fires.
    if last_exc is not None:
        raise last_exc
    return None


# ============================================================================
# DECORATOR FORM
# ============================================================================

def with_healing(agent_key: str, max_retries: int = MAX_RETRIES):
    """Decorator that wraps a cycle function in run_with_healing. Usage:

        @with_healing("propscout")
        def cycle():
            ...
    """
    def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            return run_with_healing(agent_key,
                                     lambda: fn(*args, **kwargs),
                                     max_retries)
        return wrapper
    return deco


# ============================================================================
# HEALTH SUMMARY (for batman / dashboard to consume)
# ============================================================================

def fleet_health() -> dict:
    """Read all per-agent healing state files. Used by Batman / dashboards."""
    if not HEALING_DIR.exists():
        return {"agents": {}, "in_trouble": []}
    agents = {}
    in_trouble = []
    for p in sorted(HEALING_DIR.glob("*.json")):
        try:
            data = json.loads(p.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        agents[p.stem] = data
        if data.get("consecutive_failures", 0) >= ESCALATION_THRESHOLD:
            in_trouble.append({
                "agent": p.stem,
                "consecutive_failures": data["consecutive_failures"],
                "last_error": data.get("last_error"),
            })
    return {"agents": agents, "in_trouble": in_trouble}
