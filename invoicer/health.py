"""Invoicer health: invoice attempt log + state, PayPal probes."""
from __future__ import annotations
import json, os, tempfile
from datetime import datetime
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
LOG      = DATA_DIR / "invoicer_log.json"
STATE    = DATA_DIR / "invoicer_state.json"


def _load(p, d):
    if not p.exists(): return d
    try: return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError): return d


def recent_invoices(limit=50):
    log = _load(LOG, [])
    return log[-limit:][::-1] if isinstance(log, list) else []


def invoice_outcome_summary():
    log = _load(LOG, [])
    if not isinstance(log, list) or not log:
        return {"total": 0, "ok": 0, "failed": 0, "dry_run": 0, "live": 0,
                "total_collected": 0.0}
    ok = sum(1 for r in log if r.get("ok"))
    failed = sum(1 for r in log if not r.get("ok"))
    dry = sum(1 for r in log if r.get("dry_run"))
    live = sum(1 for r in log if r.get("live"))
    collected = sum(float(r.get("amount", 0) or 0) for r in log
                    if r.get("ok") and not r.get("dry_run"))
    return {"total": len(log), "ok": ok, "failed": failed,
            "dry_run": dry, "live": live, "total_collected": round(collected, 2)}


def state_summary():
    state = _load(STATE, {})
    if not isinstance(state, dict):
        return {"keys": 0, "agents": {}}
    by_agent = {}
    for k in state:
        agent = k.split(":", 1)[0]
        by_agent[agent] = by_agent.get(agent, 0) + 1
    return {"keys": len(state), "agents": by_agent}


def probe_paypal_invoicing():
    """Check PayPal OAuth + Invoicing feature in one shot."""
    import requests
    try:
        from paywall.paypal import _get_token
        token = _get_token()
    except Exception as e:
        return {"ok": False, "stage": "oauth",
                "error": f"{type(e).__name__}: {str(e)[:160]}"}
    r = requests.post(
        f"{'https://api-m.paypal.com' if os.environ.get('PAYPAL_MODE','live')=='live' else 'https://api-m.sandbox.paypal.com'}/v2/invoicing/invoices",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"detail": {"invoice_number": "PROBE-XXX", "currency_code": "USD"}},
        timeout=12,
    )
    if r.status_code in (200, 201):
        # we just created a real draft — clean it up to avoid clutter
        try:
            iid = r.json().get("id", "")
            if iid:
                requests.delete(
                    f"https://api-m.paypal.com/v2/invoicing/invoices/{iid}",
                    headers={"Authorization": f"Bearer {token}"}, timeout=10)
        except Exception:
            pass
        return {"ok": True, "stage": "invoicing", "status_code": r.status_code,
                "detail": "OAuth + Invoicing both work"}
    body = {}
    try: body = r.json()
    except Exception: pass
    return {"ok": False, "stage": "invoicing", "status_code": r.status_code,
            "error": body.get("name", "") or r.text[:120],
            "message": body.get("message", "")}


def stuck_failures(min_attempts=3):
    """Per (agent,email,plan) keys with ≥N consecutive failures."""
    log = _load(LOG, [])
    if not isinstance(log, list): return []
    by_key = {}
    for r in log:
        if r.get("ok"):
            by_key.pop(f"{r.get('agent','')}:{r.get('email','')}:{r.get('plan','')}", None)
            continue
        key = f"{r.get('agent','')}:{r.get('email','')}:{r.get('plan','')}"
        rec = by_key.setdefault(key, {"attempts": 0, "last_ts": "", "last_error": ""})
        rec["attempts"] += 1
        rec["last_ts"] = r.get("ts", "")
        rec["last_error"] = r.get("error", "")
    return [{"key": k, **rec} for k, rec in by_key.items() if rec["attempts"] >= min_attempts]
