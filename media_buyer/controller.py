"""
Autonomous Decision & Optimization Engine (the Controller).

The controller takes the day's metrics from monitor.py, evaluates three pure
rule functions, and emits Actions. In DRY_RUN mode (the default) actions are
logged to the audit trail only; with MB_LIVE=1 they are pushed to Meta.

Rules are intentionally pure (state in, decision out) so they can be unit-tested
against synthetic Metrics without an HTTP layer.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Iterable

from . import meta_api, monitor
from .config import (
    DATA_DIR, DRY_RUN, MAX_DAILY_BUDGET_INCREASE_PCT, CampaignProfile, ProfileKind, profile_for,
)
from .monitor import Metrics

log = logging.getLogger("media_buyer.controller")

AUDIT_FILE = DATA_DIR / "controller_audit.jsonl"


# ─────────────────────────── Action types ───────────────────────────
@dataclass
class Action:
    kind: str             # "scale" | "kill" | "creative_refresh_alert"
    level: str            # "campaign" | "adset" | "ad"
    object_id: str
    object_name: str
    reason: str
    payload: dict         # rule-specific (new budget, etc.)


# ─────────────────────────── Pure rule functions ───────────────────────────
def scale_rule(m: Metrics, profile: CampaignProfile,
               history_3d_avg_roas: float | None = None) -> Action | None:
    """SCALE: increase daily budget if performance beats target.

    - Lead Gen: scale +15% when CPL is below target_cpl_usd.
    - E-Com:    scale +20% when 3-day MA ROAS > break_even_roas.

    Returns None when no scale is warranted, or when the object hasn't spent
    enough yet for a stable read (spend < 1x target_cpl for lead gen, < 1x AOV for ecom).
    """
    if profile.kind == "lead_gen":
        if m.spend < profile.target_cpl_usd:
            return None  # not enough signal
        if m.cpl is None or m.cpl >= profile.target_cpl_usd:
            return None
        pct = min(profile.scale_pct_lead_gen, MAX_DAILY_BUDGET_INCREASE_PCT)
        return Action(
            kind="scale", level=m.level, object_id=m.object_id, object_name=m.object_name,
            reason=f"CPL ${m.cpl:.2f} < target ${profile.target_cpl_usd:.2f}",
            payload={"scale_pct": pct},
        )

    # ecom
    roas_for_decision = history_3d_avg_roas if history_3d_avg_roas is not None else m.roas
    if m.spend < profile.target_aov_usd:
        return None
    if roas_for_decision is None or roas_for_decision <= profile.break_even_roas:
        return None
    pct = min(profile.scale_pct_ecom, MAX_DAILY_BUDGET_INCREASE_PCT)
    return Action(
        kind="scale", level=m.level, object_id=m.object_id, object_name=m.object_name,
        reason=f"3d MA ROAS {roas_for_decision:.2f} > break-even {profile.break_even_roas:.2f}",
        payload={"scale_pct": pct},
    )


def kill_rule(m: Metrics, profile: CampaignProfile) -> Action | None:
    """KILL: pause an under-performer.

    - Lead Gen: spend >= 2x target_cpl AND 0 leads.
    - E-Com:    spend >= 1x target_aov AND 0 purchases.
    """
    if profile.kind == "lead_gen":
        threshold = profile.target_cpl_usd * profile.kill_cpl_multiplier
        if m.spend >= threshold and m.leads == 0:
            return Action(
                kind="kill", level=m.level, object_id=m.object_id, object_name=m.object_name,
                reason=f"${m.spend:.2f} spent (>{profile.kill_cpl_multiplier}x target CPL) with 0 leads",
                payload={},
            )
        return None

    threshold = profile.target_aov_usd * profile.kill_aov_multiplier
    if m.spend >= threshold and m.purchases == 0:
        return Action(
            kind="kill", level=m.level, object_id=m.object_id, object_name=m.object_name,
            reason=f"${m.spend:.2f} spent (>{profile.kill_aov_multiplier}x AOV) with 0 purchases",
            payload={},
        )
    return None


def refresh_trigger(m: Metrics, profile: CampaignProfile,
                    flag_days: int) -> Action | None:
    """CREATIVE REFRESH ALERT: frequency too high AND under-performing for N days.

    `flag_days` is the count of days (per monitor history) the object has been
    flagged as under-performing. Callers compute it from the snapshot history.
    """
    if m.frequency < profile.refresh_frequency_threshold:
        return None
    if flag_days < profile.refresh_min_flag_days:
        return None
    return Action(
        kind="creative_refresh_alert",
        level=m.level, object_id=m.object_id, object_name=m.object_name,
        reason=(f"frequency {m.frequency:.2f} ≥ {profile.refresh_frequency_threshold} "
                f"and under-performing {flag_days}d"),
        payload={"frequency": m.frequency, "flag_days": flag_days},
    )


# ─────────────────────────── Apply ───────────────────────────
def _apply_scale(action: Action, current_daily_budget_cents: int) -> dict:
    pct = action.payload["scale_pct"] / 100.0
    new_budget = int(current_daily_budget_cents * (1.0 + pct))
    return meta_api.update_adset_daily_budget(action.object_id, new_budget)


def _apply_kill(action: Action) -> dict:
    return meta_api.pause_object(action.object_id, action.level)


def execute(actions: Iterable[Action], *, adset_budgets_by_id: dict[str, int] | None = None) -> list[dict]:
    """Apply a batch of actions. Every step is appended to the audit trail.

    `adset_budgets_by_id` is required when scaling adsets (we need the current
    daily_budget in cents to compute the new value). Pass {} for kill-only batches.
    """
    adset_budgets_by_id = adset_budgets_by_id or {}
    results: list[dict] = []
    for a in actions:
        try:
            if a.kind == "scale":
                cur = adset_budgets_by_id.get(a.object_id)
                if cur is None:
                    raise RuntimeError(f"missing current daily_budget for {a.object_id}")
                result = _apply_scale(a, cur)
            elif a.kind == "kill":
                result = _apply_kill(a)
            elif a.kind == "creative_refresh_alert":
                result = {"alert": True, "dry_run": DRY_RUN}
            else:
                result = {"error": f"unknown action.kind {a.kind!r}"}
        except Exception as e:
            log.exception("Action %s failed: %s", a, e)
            result = {"error": str(e)}
        results.append({"action": asdict(a), "result": result})
        _audit(a, result)
    return results


def _audit(action: Action, result: dict) -> None:
    AUDIT_FILE.parent.mkdir(parents=True, exist_ok=True)
    rec = {
        "ts": datetime.utcnow().isoformat() + "Z",
        "dry_run": DRY_RUN,
        "action": asdict(action),
        "result": result,
    }
    with AUDIT_FILE.open("a") as f:
        f.write(json.dumps(rec) + "\n")


# ─────────────────────────── Top-level: one full evaluation pass ───────────────────────────
def evaluate_and_apply(kind: ProfileKind) -> dict:
    """Pull today's insights for one profile, evaluate all rules, apply actions."""
    profile = profile_for(kind=kind)
    sweep = monitor.daily_sweep(kind)

    # adsets carry the budget — index by id so the scale apply step can look it up
    adsets_meta = {a["id"]: a for a in meta_api.list_adsets_for_account(profile.ad_account_id)}
    adset_budgets: dict[str, int] = {
        aid: int(meta.get("daily_budget") or 0)
        for aid, meta in adsets_meta.items()
    }

    actions: list[Action] = []
    # Apply rules to adsets (where budget lives) and ads (where creative lives).
    for m in sweep["adsets"]:
        history = monitor.history_for(m.object_id, days=7)
        ma_roas = monitor.moving_avg(history, "roas", n=3)
        for rule in (
            lambda: scale_rule(m, profile, history_3d_avg_roas=ma_roas),
            lambda: kill_rule(m, profile),
        ):
            a = rule()
            if a:
                actions.append(a)

    for m in sweep["ads"]:
        history = monitor.history_for(m.object_id, days=7)
        flag_days = sum(1 for h in history
                        if (h.get("cpl") is not None and h["cpl"] > profile.target_cpl_usd)
                        or (h.get("roas") is not None and h["roas"] < profile.break_even_roas))
        a = refresh_trigger(m, profile, flag_days)
        if a:
            actions.append(a)

    results = execute(actions, adset_budgets_by_id=adset_budgets)
    return {
        "profile": kind,
        "actions_proposed": len(actions),
        "results": results,
        "dry_run": DRY_RUN,
    }
