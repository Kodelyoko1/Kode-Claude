"""
FBAds monitor — pull Meta Insights for every ad we launched, compute
per-ad performance, attribute new subscribers/invoices to ad windows,
flag winners + losers.

Two outputs:
  data/fbads_insights.json   — rolling snapshot of Meta Insights, keyed by ad_id
  data/fbads_attribution.json — best-effort attribution of subscriber + invoice
                                events to ads running during the activation window
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

DATA_DIR        = Path(__file__).parent.parent / "data"
INSIGHTS_PATH   = DATA_DIR / "fbads_insights.json"
ATTRIB_PATH     = DATA_DIR / "fbads_attribution.json"


def _now() -> str:
    return datetime.now().isoformat()


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


# ─────────────────────────── Meta Insights pull ───────────────────────────

def pull_insights(date_preset: str = "last_7d") -> dict:
    """Hit Meta Insights API for every ad in our ad account.
    Returns {"ok": bool, "fetched": N, "ads": [...], "error": "..."}."""
    if not os.environ.get("META_ACCESS_TOKEN") or not os.environ.get("META_AD_ACCOUNT_ID"):
        return {"ok": False, "fetched": 0, "ads": [],
                "error": "META_ACCESS_TOKEN or META_AD_ACCOUNT_ID missing"}
    try:
        from media_buyer.meta_api import get_insights
        ad_account = os.environ["META_AD_ACCOUNT_ID"]
        rows = get_insights(level="ad", object_id=ad_account,
                            date_preset=date_preset)
    except Exception as e:
        return {"ok": False, "fetched": 0, "ads": [],
                "error": f"{type(e).__name__}: {str(e)[:200]}"}
    # Normalize each row into our shape
    normalized = []
    for r in rows:
        actions = {a["action_type"]: int(a["value"]) for a in r.get("actions", [])}
        normalized.append({
            "ad_id":         r.get("ad_id", ""),
            "ad_name":       r.get("ad_name", ""),
            "campaign_id":   r.get("campaign_id", ""),
            "campaign_name": r.get("campaign_name", ""),
            "adset_id":      r.get("adset_id", ""),
            "spend":         float(r.get("spend", 0) or 0),
            "impressions":   int(r.get("impressions", 0) or 0),
            "reach":         int(r.get("reach", 0) or 0),
            "clicks":        int(r.get("clicks", 0) or 0),
            "cpm":           float(r.get("cpm", 0) or 0),
            "cpc":           float(r.get("cpc", 0) or 0),
            "ctr":           float(r.get("ctr", 0) or 0),
            "messaging_conversations_started":
                actions.get("onsite_conversion.messaging_conversation_started_7d", 0),
            "leads":         actions.get("lead", 0) + actions.get("offsite_conversion.lead", 0),
            "post_engagements": actions.get("post_engagement", 0),
        })
    # Save snapshot
    snapshot = {"ts": _now(), "date_preset": date_preset,
                "fetched": len(normalized), "ads": normalized}
    _save(INSIGHTS_PATH, snapshot)
    return {"ok": True, **snapshot}


def latest_insights() -> dict:
    return _load(INSIGHTS_PATH, {})


# ─────────────────────────── Attribution ───────────────────────────

def _all_subscriber_events() -> list[dict]:
    """Walk every <agent>_subscription_log.json + <agent>_client_log.json
    to find activation timestamps + emails. These are the 'conversions' we
    cross-reference against ad windows."""
    root = Path(__file__).parent.parent
    events: list[dict] = []
    for log in root.glob("data/*_subscription_log.json"):
        try:
            entries = json.loads(log.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(entries, list):
            continue
        # Find agent by stripping suffix from filename
        agent_key = log.stem.replace("_subscription_log", "")
        for e in entries:
            if e.get("event") in ("activated", "added"):
                events.append({"ts": e.get("ts", ""),
                               "email": e.get("email", ""),
                               "event": e.get("event", ""),
                               "plan": e.get("plan", ""),
                               "agent_key": agent_key})
    for log in root.glob("data/*_client_log.json"):
        try:
            entries = json.loads(log.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(entries, list):
            continue
        agent_key = log.stem.replace("_client_log", "")
        for e in entries:
            if e.get("event") in ("activated", "added"):
                events.append({"ts": e.get("ts", ""),
                               "email": e.get("email", ""),
                               "event": e.get("event", ""),
                               "plan": e.get("plan", ""),
                               "agent_key": agent_key})
    return events


def _all_invoices_paid() -> list[dict]:
    """Successful invoicer outcomes (proxy for paid revenue events)."""
    log = _load(DATA_DIR / "invoicer_log.json", [])
    if not isinstance(log, list):
        return []
    return [r for r in log if r.get("ok") and r.get("live")]


def _ad_audience_to_agent_keys() -> dict[str, set[str]]:
    """Which subscriber agents map to which Facebook ad audience."""
    return {
        "sellers":    {"followup", "deal_analyzer"},
        "buyers":     {"buyer_finder"},
        "wholesalers":{"deal_analyzer", "outreach", "client_prospector", "prospector"},
        "creators":   {"salespage_doctor"},
        "jobseekers": {"careerforge"},
        "podcasters": {"transcribe", "shownotes"},
        "local_biz":  {"reputation_guard"},
    }


def compute_attribution() -> dict:
    """Loose first-touch attribution. For each ad in the latest insights:
    count subscriber activations + invoices to its audience's agents
    in the date_preset window. This is APPROXIMATE — without UTM/Pixel
    we can't prove causation, only co-occurrence."""
    snap = latest_insights()
    if not snap.get("ads"):
        return {"ok": False, "error": "no Meta insights snapshot — run pull_insights first"}
    aud_map = _ad_audience_to_agent_keys()
    # Window: assume last_7d for simplicity
    window_start = (datetime.now() - timedelta(days=7)).isoformat()
    sub_events = [e for e in _all_subscriber_events() if e.get("ts", "") >= window_start]
    invoices   = [r for r in _all_invoices_paid() if r.get("ts", "") >= window_start]
    by_ad: dict[str, dict] = {}
    for ad in snap["ads"]:
        # ad_name shape: <date>-<audience>-<seq>-<slug>
        name = ad.get("ad_name", "")
        parts = name.split("-")
        # find which audience key appears in the name
        audience = next((a for a in aud_map if a in name), "")
        agents = aud_map.get(audience, set())
        attributed_subs = [e for e in sub_events if e.get("agent_key") in agents]
        attributed_inv  = [r for r in invoices if r.get("agent") in agents]
        revenue = sum(float(r.get("amount", 0) or 0) for r in attributed_inv)
        spend = ad["spend"]
        roas  = (revenue / spend) if spend > 0 else None
        by_ad[ad["ad_id"]] = {
            "ad_name":     name,
            "audience":    audience,
            "spend":       spend,
            "impressions": ad["impressions"],
            "clicks":      ad["clicks"],
            "conversations": ad["messaging_conversations_started"],
            "attributed_subscribers": len(attributed_subs),
            "attributed_invoices":    len(attributed_inv),
            "attributed_revenue":     round(revenue, 2),
            "roas":                   round(roas, 2) if roas is not None else None,
        }
    out = {"ts": _now(), "window_days": 7, "by_ad": by_ad,
           "totals": {
               "ads":               len(by_ad),
               "total_spend":       round(sum(a["spend"] for a in by_ad.values()), 2),
               "total_revenue":     round(sum(a["attributed_revenue"] for a in by_ad.values()), 2),
               "blended_roas":      None,
           }}
    if out["totals"]["total_spend"] > 0:
        out["totals"]["blended_roas"] = round(
            out["totals"]["total_revenue"] / out["totals"]["total_spend"], 2)
    _save(ATTRIB_PATH, out)
    return {"ok": True, **out}


def latest_attribution() -> dict:
    return _load(ATTRIB_PATH, {})


# ─────────────────────────── Verdicts ───────────────────────────

def verdicts(min_spend_usd: float = 25.0, win_roas: float = 2.0,
             lose_roas: float = 0.3) -> dict:
    """Categorize ads as winner / loser / unknown.

    - winner:  spend >= min_spend AND roas >= win_roas
    - loser:   spend >= min_spend AND roas < lose_roas (and not None)
    - unknown: not enough spend yet OR roas can't be computed
    """
    att = latest_attribution()
    if not att.get("by_ad"):
        return {"winners": [], "losers": [], "unknowns": [], "error": "no attribution data"}
    winners, losers, unknowns = [], [], []
    for ad_id, m in att["by_ad"].items():
        spend = m["spend"]
        roas  = m.get("roas")
        if spend < min_spend_usd:
            unknowns.append({"ad_id": ad_id, **m, "reason": f"spend ${spend:.2f} < min ${min_spend_usd:.0f}"})
        elif roas is None:
            unknowns.append({"ad_id": ad_id, **m, "reason": "no revenue attributed yet"})
        elif roas >= win_roas:
            winners.append({"ad_id": ad_id, **m})
        elif roas < lose_roas:
            losers.append({"ad_id": ad_id, **m})
        else:
            unknowns.append({"ad_id": ad_id, **m, "reason": f"roas {roas:.2f} in undecided range"})
    return {"winners": winners, "losers": losers, "unknowns": unknowns,
            "thresholds": {"min_spend": min_spend_usd,
                           "win_roas":  win_roas, "lose_roas": lose_roas}}
