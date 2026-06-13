"""
FBAds Conversions — server-side CAPI event dispatcher.

Closes the attribution loop. The fbads/monitor.py we already shipped
does co-occurrence attribution (joining ad windows with subscriber +
invoicer logs), which works but can't prove a specific lead came from
a specific ad.

This module fires real Meta Conversions API events so Meta can attribute
the conversion server-side to the exact ad the user saw/clicked. The
result: Meta Ads Manager reports actual ROAS per ad, the Insights pull
returns real `purchase` and `lead` action counts, and verdicts get sharper.

Two event flavors we fire:

  · Lead     — when a subscriber transitions from added/pending to active
               (i.e. the user committed to a product, even before paying).
  · Purchase — when invoicer logs a successful live invoice for an
               existing subscriber (real money committed via PayPal).

State:
  data/fbads_capi_sent.json  — set of (event_name, agent, email, ts)
                               keys we've already pushed, so we don't
                               double-fire on re-runs.

Env:
  MB_LEADGEN_PIXEL_ID   — your Meta Pixel ID (Events Manager → Data Sources)
  META_ACCESS_TOKEN     — same token used for ads (must have ads_management)
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

DATA_DIR    = Path(__file__).parent.parent / "data"
SENT_LEDGER = DATA_DIR / "fbads_capi_sent.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load(p: Path, default):
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return default


def _save(p: Path, data) -> None:
    p.parent.mkdir(exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{p.name}.", suffix=".tmp", dir=p.parent)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, p)
    except Exception:
        try: os.unlink(tmp)
        except OSError: pass
        raise


def _event_key(event_name: str, agent: str, email: str, ts: str) -> str:
    return f"{event_name}:{agent}:{(email or '').lower()}:{ts}"


def _ts_to_unix(iso: str) -> int:
    """Best-effort ISO → unix-seconds. Meta requires unix epoch."""
    if not iso:
        return int(datetime.now().timestamp())
    try:
        return int(datetime.fromisoformat(iso.replace("Z", "+00:00").split("+")[0]
                                          ).timestamp())
    except (ValueError, AttributeError):
        return int(datetime.now().timestamp())


def _have_creds() -> tuple[bool, list[str]]:
    missing = []
    for v in ("MB_LEADGEN_PIXEL_ID", "META_ACCESS_TOKEN"):
        if not os.environ.get(v, "").strip():
            missing.append(v)
    return (not missing, missing)


# ─────────────────────────── Event sources ───────────────────────────

# Log prefix (filename) → Python module that owns PLANS. The 2-3 letter prefix
# we read off the log filename doesn't always match the module dir name
# (e.g. spd → salespage_doctor). Extend here when adding new monetized agents.
PREFIX_TO_MODULE = {
    "bm":  "batman",
    "ds":  "dropship_scout",
    "hd":  "hudscout",
    "iz":  "inboxzero",
    "lm":  "link_mender",
    "spd": "salespage_doctor",
    "sa":  "speedaudit",
}

# Meta CAPI rejects events older than ~7 days (silently). Anything past this
# is a no-op send; we skip and report instead of burning the ledger entry.
CAPI_MAX_AGE_DAYS = 7


def _agent_to_value(agent: str, plan_key: str) -> float:
    """Look up the plan's price for the event 'value' field.
    Returns 0.0 if not found (still fires the event)."""
    try:
        import importlib
        mod_name_candidates = []
        mapped = PREFIX_TO_MODULE.get(agent)
        if mapped:
            mod_name_candidates += [f"{mapped}.subscribers", f"{mapped}.clients"]
        mod_name_candidates += [f"{agent}.subscribers", f"{agent}.clients"]
        mod = None
        for cand in mod_name_candidates:
            try:
                mod = importlib.import_module(cand)
                break
            except ImportError:
                continue
        if mod is None:
            return 0.0
        plans = getattr(mod, "PLANS", {})
        info = plans.get(plan_key, {})
        # Prefer one_time for one-time plans, price_mo for recurring
        if info.get("one_time", 0):
            return float(info["one_time"])
        if info.get("price_mo", 0):
            return float(info["price_mo"])
    except Exception:
        pass
    return 0.0


def _event_age_days(ts: str) -> float:
    """How old is this event timestamp, in days? Returns 0.0 if unparseable."""
    if not ts:
        return 0.0
    try:
        clean = ts.replace("Z", "+00:00").split("+")[0]
        dt = datetime.fromisoformat(clean)
        delta = datetime.now() - dt
        return delta.total_seconds() / 86400.0
    except (ValueError, AttributeError):
        return 0.0


def _walk_lead_events() -> list[dict]:
    """Every activate event across the fleet → candidate Lead events."""
    out = []
    for log in (DATA_DIR.glob("*_subscription_log.json")):
        try:
            entries = json.loads(log.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(entries, list):
            continue
        agent = log.stem.replace("_subscription_log", "")
        for e in entries:
            if e.get("event") != "activated":
                continue
            out.append({"agent": agent, "email": e.get("email", ""),
                        "plan": e.get("plan", ""),
                        "ts": e.get("ts", "")})
    for log in (DATA_DIR.glob("*_client_log.json")):
        try:
            entries = json.loads(log.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(entries, list):
            continue
        agent = log.stem.replace("_client_log", "")
        for e in entries:
            if e.get("event") != "activated":
                continue
            out.append({"agent": agent, "email": e.get("email", ""),
                        "plan": e.get("plan", ""),
                        "ts": e.get("ts", "")})
    return out


def _walk_purchase_events() -> list[dict]:
    """Live invoicer successes → Purchase events. We dedupe later."""
    log = _load(DATA_DIR / "invoicer_log.json", [])
    if not isinstance(log, list):
        return []
    return [{"agent": r.get("agent", ""), "email": r.get("email", ""),
             "plan":  r.get("plan", ""),
             "amount": float(r.get("amount", 0) or 0),
             "ts": r.get("ts", "")}
            for r in log if r.get("ok") and r.get("live")]


# ─────────────────────────── Dispatch ───────────────────────────

def push_pending(dry: bool = False) -> dict:
    """Fire all unsent Lead + Purchase events to Meta CAPI.
    Returns {"sent": N, "skipped": N, "errors": [...]}."""
    ready, missing = _have_creds()
    if not ready:
        return {"sent": 0, "skipped": 0,
                "errors": [{"reason": f"missing env: {','.join(missing)}"}]}
    pixel_id = os.environ["MB_LEADGEN_PIXEL_ID"]

    sent_ledger = _load(SENT_LEDGER, [])
    if not isinstance(sent_ledger, list):
        sent_ledger = []
    sent_set = set(sent_ledger)

    leads     = _walk_lead_events()
    purchases = _walk_purchase_events()

    try:
        from media_buyer.meta_api import send_capi_event
    except Exception as e:
        return {"sent": 0, "skipped": 0,
                "errors": [{"reason": f"meta_api import: {e}"}]}

    sent = 0
    skipped = 0
    skipped_too_old = 0
    errors: list[dict] = []

    def _fire(event_name: str, ev: dict, value: float = 0.0, currency: str = "USD"):
        nonlocal sent, skipped, skipped_too_old
        key = _event_key(event_name, ev["agent"], ev["email"], ev["ts"])
        if key in sent_set:
            return
        if not ev.get("email"):
            return
        age_days = _event_age_days(ev["ts"])
        if age_days > CAPI_MAX_AGE_DAYS:
            skipped_too_old += 1
            errors.append({"event": event_name, "agent": ev["agent"],
                           "email": ev["email"][:30],
                           "reason": f"too_old ({age_days:.1f}d > {CAPI_MAX_AGE_DAYS}d) — "
                                     "Meta CAPI rejects events past 7-day window"})
            return
        user_data = {"em": ev["email"]}
        custom_data = {
            "currency":     currency,
            "value":        round(value, 2),
            "content_name": f"{ev['agent']}.{ev.get('plan','')}",
            "content_category": ev["agent"],
        }
        try:
            if dry:
                sent += 1
                return
            r = send_capi_event(
                pixel_id=pixel_id,
                event_name=event_name,
                event_time=_ts_to_unix(ev["ts"]),
                user_data=user_data,
                custom_data=custom_data,
                action_source="system_generated",
            )
            if r.get("dry_run") or r.get("events_received", 0) >= 1 or "id" in r:
                sent_set.add(key)
                sent_ledger.append(key)
                sent += 1
            else:
                errors.append({"event": event_name, "agent": ev["agent"],
                               "email": ev["email"][:30],
                               "reason": str(r)[:160]})
                skipped += 1
        except Exception as e:
            errors.append({"event": event_name, "agent": ev["agent"],
                           "email": ev["email"][:30],
                           "reason": f"{type(e).__name__}: {str(e)[:120]}"})
            skipped += 1

    for ev in leads:
        value = _agent_to_value(ev["agent"], ev.get("plan", ""))
        _fire("Lead", ev, value=value)

    for ev in purchases:
        _fire("Purchase", ev, value=float(ev.get("amount", 0) or 0))

    _save(SENT_LEDGER, sent_ledger)
    return {"sent": sent, "skipped": skipped,
            "skipped_too_old": skipped_too_old,
            "errors": errors, "ledger_size": len(sent_ledger)}


def probe() -> dict:
    """Check creds + ledger state without firing anything."""
    ready, missing = _have_creds()
    leads = _walk_lead_events()
    purchases = _walk_purchase_events()
    sent_ledger = _load(SENT_LEDGER, [])
    sent_count = len(sent_ledger) if isinstance(sent_ledger, list) else 0
    pending_leads = sum(1 for l in leads
                        if _event_key("Lead", l["agent"], l["email"], l["ts"])
                        not in (sent_ledger or []))
    pending_purchases = sum(1 for p in purchases
                            if _event_key("Purchase", p["agent"], p["email"], p["ts"])
                            not in (sent_ledger or []))
    return {
        "ok":               ready,
        "missing_creds":    missing,
        "lead_events":      len(leads),
        "purchase_events":  len(purchases),
        "pending_leads":    pending_leads,
        "pending_purchases": pending_purchases,
        "already_sent":     sent_count,
    }
