"""
Per-state grid-health tracker for HUDScout.

HUDScout sweeps the states in HD_STATES on every cycle. Two failure modes that
the existing run_full_cycle() doesn't surface:

  1. Token-bootstrap fails (HUD redesigns the search page → no
     request-verification-token in the HTML). harvest_all_states() returns []
     and the daily digest is empty; nothing alerts.
  2. A single state stops returning rows (HUD adjusts state-name normalization,
     dataset for that state goes empty, etc.). The other states keep working
     so the cycle still looks healthy in aggregate.

This module tracks per-state run history so the diagnose check can flag silent
degradation as P1, plus a probe_session() helper the runner uses to test the
antiforgery handshake without consuming a full cycle.

State file: data/hd_state_health.json — dict keyed by state code:
  {
    "ME": {
      "last_run":           "2026-06-03T03:21:00",
      "last_run_count":     12,
      "last_nonzero_at":    "2026-06-03T03:21:00",
      "last_nonzero_count": 12,
      "consecutive_zeros":  0,
      "total_runs":         42,
      "total_found":        503,
      "last_error":         ""
    }, ...
  }

Env:
  HD_ALERT_AFTER_ZEROS    default 3   — consecutive-zero threshold for P1 warn
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

DATA_DIR     = Path(__file__).parent.parent / "data"
HEALTH_FILE  = DATA_DIR / "hd_state_health.json"

ALERT_AFTER_ZEROS = int(os.environ.get("HD_ALERT_AFTER_ZEROS", "3"))


def _now() -> str:
    return datetime.now().isoformat()


def _load() -> dict:
    if not HEALTH_FILE.exists():
        return {}
    try:
        d = json.loads(HEALTH_FILE.read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save(data: dict) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{HEALTH_FILE.name}.", suffix=".tmp", dir=DATA_DIR)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, HEALTH_FILE)
    except Exception:
        try: os.unlink(tmp)
        except OSError: pass
        raise


def record_state(state: str, count: int, error: str = "") -> None:
    """Update the per-state record after a HUD query."""
    state = (state or "").upper().strip()
    if not state:
        return
    health = _load()
    rec = health.get(state, {
        "last_run":           "",
        "last_run_count":     0,
        "last_nonzero_at":    "",
        "last_nonzero_count": 0,
        "consecutive_zeros":  0,
        "total_runs":         0,
        "total_found":        0,
        "last_error":         "",
    })
    rec["last_run"]       = _now()
    rec["last_run_count"] = count
    rec["total_runs"]    += 1
    rec["total_found"]   += max(count, 0)
    rec["last_error"]     = error or ""
    if count > 0:
        rec["last_nonzero_at"]    = _now()
        rec["last_nonzero_count"] = count
        rec["consecutive_zeros"]  = 0
    else:
        rec["consecutive_zeros"] = rec.get("consecutive_zeros", 0) + 1
    health[state] = rec
    _save(health)


def unhealthy_states(threshold: int = None) -> list[dict]:
    threshold = ALERT_AFTER_ZEROS if threshold is None else threshold
    health = _load()
    out = []
    for st, rec in health.items():
        if rec.get("consecutive_zeros", 0) >= threshold:
            out.append({"state": st, **rec})
    return sorted(out, key=lambda r: -r.get("consecutive_zeros", 0))


def summary() -> dict:
    health = _load()
    n = len(health)
    if not n:
        return {"states": 0, "healthy": 0, "warning": 0, "total_found_all_time": 0,
                "alert_threshold": ALERT_AFTER_ZEROS}
    healthy = sum(1 for r in health.values() if r.get("consecutive_zeros", 0) < ALERT_AFTER_ZEROS)
    return {
        "states":               n,
        "healthy":              healthy,
        "warning":              n - healthy,
        "total_found_all_time": sum(r.get("total_found", 0) for r in health.values()),
        "alert_threshold":      ALERT_AFTER_ZEROS,
    }


def report_lines() -> list[str]:
    health = _load()
    if not health:
        return ["(no states tracked yet — run a cycle first)"]
    lines = [f"{'ST':<4s}  {'LAST RUN':<19s}  {'FOUND':>5s}  {'STREAK':>6s}  {'TOTAL':>6s}"]
    for st, r in sorted(health.items()):
        cz = r.get("consecutive_zeros", 0)
        streak = f"-{cz}" if cz else "ok"
        lines.append(
            f"{st:<4s}  {(r.get('last_run') or '')[:19]:<19s}  "
            f"{r.get('last_run_count',0):>5d}  {streak:>6s}  {r.get('total_found',0):>6d}"
            + (f"  err: {r.get('last_error','')[:40]}" if r.get("last_error") else "")
        )
    return lines


def probe_session() -> dict:
    """Test the HUD antiforgery handshake without consuming a full cycle.
    Imports lazily so diagnose can call this even if the network is down."""
    try:
        from hudscout.tools import _open_session  # local import → no requests cost when not used
        session, token = _open_session()
        return {
            "ok":      True,
            "token_len": len(token),
            "cookies": len(session.cookies),
        }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:160]}"}


def _cli():
    import argparse
    p = argparse.ArgumentParser(description="HUDScout per-state grid-health")
    p.add_argument("--probe", action="store_true",
                    help="Probe HUD session/token only — no data scrape")
    p.add_argument("--summary-json", action="store_true",
                    help="Emit machine-readable summary")
    args = p.parse_args()
    if args.probe:
        print(json.dumps(probe_session(), indent=2))
        return
    if args.summary_json:
        print(json.dumps({"summary": summary(), "unhealthy": unhealthy_states()}, indent=2))
        return
    for line in report_lines():
        print(line)
    s = summary()
    if s["states"]:
        print()
        print(f"  {s['healthy']} healthy / {s['warning']} warning  "
              f"(threshold ≥{s['alert_threshold']} consecutive zeros)  "
              f"all-time found: {s['total_found_all_time']}")


if __name__ == "__main__":
    _cli()
