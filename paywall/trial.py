"""
30-day free trial management for the autonomous agents.

Trial flow:
  1. Customer signs up with name + email
  2. Gets an access key and 30 days of free use
  3. Day 23: reminder email "7 days left"
  4. Day 29: reminder email "trial ends tomorrow — keep going for $29/mo"
  5. Day 30: trial expires; agent stops processing their queue
  6. After payment via PayPal, status flips to active subscriber
"""
import json
import os
import uuid
from datetime import datetime, timedelta
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
TRIALS_FILE = DATA_DIR / "trials.json"
TRIAL_DAYS = 30


def _load() -> dict:
    if TRIALS_FILE.exists():
        with open(TRIALS_FILE) as f:
            return json.load(f)
    return {}


def _save(d: dict):
    DATA_DIR.mkdir(exist_ok=True)
    with open(TRIALS_FILE, "w") as f:
        json.dump(d, f, indent=2)


def start_trial(agent_key: str, name: str, email: str, source: str = "website") -> dict:
    """Create a new 30-day trial. Returns access key + dashboard URL."""
    trials = _load()
    # Prevent duplicate trials per email per agent
    for k, t in trials.items():
        if t.get("email") == email and t.get("agent") == agent_key:
            return {"error": "already_signed_up", "access_key": k,
                    "status": t.get("status"), "expires_at": t.get("expires_at")}

    access_key = f"WO-TRIAL-{uuid.uuid4().hex[:10].upper()}"
    now = datetime.now()
    expires = now + timedelta(days=TRIAL_DAYS)
    record = {
        "access_key":  access_key,
        "agent":       agent_key,
        "name":        name,
        "email":       email,
        "source":      source,
        "status":      "trial",
        "started_at":  now.isoformat(),
        "expires_at":  expires.isoformat(),
        "reminder_7d_sent":  False,
        "reminder_1d_sent":  False,
        "converted_at":      "",
    }
    trials[access_key] = record
    _save(trials)
    return record


def check_trial(access_key: str) -> dict:
    """Return current trial state. Auto-expires if past expiry."""
    trials = _load()
    t = trials.get(access_key)
    if not t:
        return {"allowed": False, "reason": "unknown_key"}
    if t.get("status") == "active_paid":
        return {"allowed": True, "status": "active_paid", "trial": t}
    expires = datetime.fromisoformat(t["expires_at"])
    if datetime.now() > expires:
        if t.get("status") != "expired":
            trials[access_key]["status"] = "expired"
            _save(trials)
        return {"allowed": False, "reason": "trial_expired",
                "payment_url": _paypal_link(t["agent"]),
                "trial": trials[access_key]}
    days_left = (expires - datetime.now()).days
    return {"allowed": True, "status": "trial",
            "days_left": days_left, "trial": t}


def _paypal_link(agent_key: str) -> str:
    from paywall.agent_paywall import _price
    username = os.environ.get("PAYPAL_ME_USERNAME", "wholesaleomniverse")
    price = _price(agent_key)
    return f"https://paypal.me/{username}/{price:.0f}"


def convert_to_paid(access_key: str) -> dict:
    trials = _load()
    if access_key not in trials:
        return {"error": "unknown_key"}
    trials[access_key]["status"] = "active_paid"
    trials[access_key]["converted_at"] = datetime.now().isoformat()
    trials[access_key]["expires_at"] = (datetime.now() + timedelta(days=30)).isoformat()
    _save(trials)
    return trials[access_key]


def trials_needing_reminder() -> list:
    """Return trials due for 7-day or 1-day expiry reminders."""
    trials = _load()
    out = []
    now = datetime.now()
    for k, t in trials.items():
        if t.get("status") != "trial":
            continue
        try:
            expires = datetime.fromisoformat(t["expires_at"])
        except Exception:
            continue
        days_left = (expires - now).days
        if days_left <= 7 and not t.get("reminder_7d_sent"):
            out.append({"access_key": k, "kind": "7d", **t})
        elif days_left <= 1 and not t.get("reminder_1d_sent"):
            out.append({"access_key": k, "kind": "1d", **t})
    return out


def mark_reminder_sent(access_key: str, kind: str):
    trials = _load()
    if access_key in trials:
        if kind == "7d":
            trials[access_key]["reminder_7d_sent"] = True
        elif kind == "1d":
            trials[access_key]["reminder_1d_sent"] = True
        _save(trials)


def list_trials(agent_key: str = "") -> list:
    trials = _load()
    items = list(trials.values())
    if agent_key:
        items = [t for t in items if t.get("agent") == agent_key]
    return items


def trial_stats(agent_key: str = "") -> dict:
    items = list_trials(agent_key)
    return {
        "total_trials":  len(items),
        "active_trials": sum(1 for t in items if t.get("status") == "trial"),
        "converted":     sum(1 for t in items if t.get("status") == "active_paid"),
        "expired":       sum(1 for t in items if t.get("status") == "expired"),
        "conversion_rate": round(
            100 * sum(1 for t in items if t.get("status") == "active_paid")
            / max(1, len(items)), 1),
    }
