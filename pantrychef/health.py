"""
PantryChef health: per-plan outcome log + recipe-yield distribution.

PantryChef is usage-based: each active subscriber in pc_subscribers.json
gets a weekly meal plan built from pc_users/<user_id>.json (pantry +
preferences). Four silent failure modes:

  1. Subscriber's user_id has no profile → build_plan returns
     {"error": "no_user_profile"}; fulfill_cycle silently continues;
     paid customer never gets a plan
  2. Profile exists but pantry has <5 items → {"error":
     "pantry_too_small"}; same silent skip
  3. Mailer fails (Gmail bounce, malformed address) → plan files were
     written but no log entry; cron retries forever
  4. Allergies/dislikes filter every recipe → recipes_count near 0;
     plan still emitted but customer gets an empty calendar

This module tracks per-subscriber outcomes in a rolling log and a
recipe-yield distribution so the diagnose check can flag pantries that
keep yielding too few recipes (cause #4) and stuck mail_failed orders
(cause #3, same shape as the careerforge fix).

State files:
  data/pc_plan_log.json    — rolling per-plan outcome log
  data/pc_yield_log.json   — rolling per-plan recipe yield + shopping size

Env:
  PC_PLAN_LOG_MAX        default 300 — cap on rolling outcome history
  PC_MIN_RECIPES         default 7   — below this, plan is "thin" (P1)
  PC_PANTRY_MIN          default 5   — matches the tools.py gate
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

DATA_DIR    = Path(__file__).parent.parent / "data"
USERS_DIR   = DATA_DIR / "pc_users"
PLANS_DIR   = DATA_DIR / "pc_plans"
PLAN_LOG    = DATA_DIR / "pc_plan_log.json"
YIELD_LOG   = DATA_DIR / "pc_yield_log.json"

PLAN_LOG_MAX = int(os.environ.get("PC_PLAN_LOG_MAX", "300"))
MIN_RECIPES  = int(os.environ.get("PC_MIN_RECIPES", "7"))
PANTRY_MIN   = int(os.environ.get("PC_PANTRY_MIN", "5"))

VALID_OUTCOMES = {"success", "no_user_profile", "pantry_too_small",
                  "no_email", "mail_failed"}


def _now() -> str:
    return datetime.now().isoformat()


def _load(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return default


def _save(path: Path, data) -> None:
    path.parent.mkdir(exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except Exception:
        try: os.unlink(tmp)
        except OSError: pass
        raise


def _month_key(ts: str = "") -> str:
    return ts[:7] if ts else datetime.now().strftime("%Y-%m")


# ─────────────────────────── Per-plan outcomes ───────────────────────────

def record_plan(user_id: str, outcome: str, detail: str = "") -> None:
    """outcome ∈ {success, no_user_profile, pantry_too_small, no_email, mail_failed}."""
    if not user_id:
        return
    log = _load(PLAN_LOG, [])
    if not isinstance(log, list):
        log = []
    log.append({"ts": _now(), "user_id": user_id,
                "outcome": outcome, "detail": detail or ""})
    if len(log) > PLAN_LOG_MAX:
        log = log[-PLAN_LOG_MAX:]
    _save(PLAN_LOG, log)


def record_yield(user_id: str, recipes_count: int, shopping_items: int) -> None:
    if not user_id:
        return
    log = _load(YIELD_LOG, [])
    if not isinstance(log, list):
        log = []
    log.append({"ts": _now(), "user_id": user_id,
                "recipes_count": int(recipes_count),
                "shopping_items": int(shopping_items)})
    if len(log) > PLAN_LOG_MAX:
        log = log[-PLAN_LOG_MAX:]
    _save(YIELD_LOG, log)


def recent_plans(limit: int = 50) -> list[dict]:
    log = _load(PLAN_LOG, [])
    if not isinstance(log, list):
        return []
    return log[-limit:][::-1]


def plan_outcome_summary() -> dict:
    log = _load(PLAN_LOG, [])
    if not isinstance(log, list) or not log:
        return {"total": 0, **{oc: 0 for oc in VALID_OUTCOMES}}
    counts = {oc: 0 for oc in VALID_OUTCOMES}
    for r in log:
        oc = r.get("outcome", "")
        if oc in counts:
            counts[oc] += 1
    return {"total": len(log), **counts}


def stuck_mail_failed(min_attempts: int = 3) -> list[dict]:
    log = _load(PLAN_LOG, [])
    if not isinstance(log, list):
        return []
    by_user: dict[str, dict] = {}
    for r in log:
        if r.get("outcome") != "mail_failed":
            continue
        u = r.get("user_id", "")
        if not u:
            continue
        rec = by_user.setdefault(u, {"attempts": 0, "last_ts": "", "last_detail": ""})
        rec["attempts"] += 1
        rec["last_ts"] = r.get("ts", "")
        rec["last_detail"] = r.get("detail", "")
    return sorted(
        [{"user_id": u, **rec} for u, rec in by_user.items() if rec["attempts"] >= min_attempts],
        key=lambda r: -r["attempts"],
    )


def yield_summary() -> dict:
    log = _load(YIELD_LOG, [])
    if not isinstance(log, list) or not log:
        return {"total": 0, "avg_recipes": None, "avg_shopping": None,
                "thin_plans": 0}
    n = len(log)
    sum_r = sum(r.get("recipes_count", 0) for r in log)
    sum_s = sum(r.get("shopping_items", 0) for r in log)
    thin = sum(1 for r in log if r.get("recipes_count", 0) < MIN_RECIPES)
    return {"total": n,
            "avg_recipes":  round(sum_r / n, 1),
            "avg_shopping": round(sum_s / n, 1),
            "thin_plans":   thin}


def users_with_thin_plans(window: int = 4) -> list[dict]:
    """For each user, their recent N yields — flag if ANY are < MIN_RECIPES."""
    log = _load(YIELD_LOG, [])
    if not isinstance(log, list):
        return []
    by_user: dict[str, list] = {}
    for r in log:
        u = r.get("user_id", "")
        if not u:
            continue
        by_user.setdefault(u, []).append(r)
    out = []
    for u, rows in by_user.items():
        recent = rows[-window:]
        thin = [r for r in recent if r.get("recipes_count", 0) < MIN_RECIPES]
        if thin:
            out.append({"user_id": u, "thin_in_window": len(thin),
                        "window": len(recent),
                        "last_recipes": recent[-1].get("recipes_count", 0)})
    return sorted(out, key=lambda r: -r["thin_in_window"])


def monthly_usage_per_user(month: str = "") -> dict[str, int]:
    month = month or _month_key()
    log = _load(PLAN_LOG, [])
    if not isinstance(log, list):
        return {}
    counts: dict[str, int] = {}
    for r in log:
        if r.get("outcome") != "success":
            continue
        if _month_key(r.get("ts", "")) != month:
            continue
        u = r.get("user_id", "")
        if not u:
            continue
        counts[u] = counts.get(u, 0) + 1
    return counts


# ─────────────────────────── Input probe ───────────────────────────

def probe_inputs() -> dict:
    """Triangulate profiles + subscribers + pantry depth.

    Returns {
      "ok":  bool,
      "profiles": N,
      "subscribers_total": N,
      "subscribers_active": N,
      "subs_missing_profile": [user_id, ...],
      "thin_pantries":         [{"user_id": u, "items": N}, ...],
    }
    """
    profile_files = []
    if USERS_DIR.exists():
        profile_files = [f for f in USERS_DIR.glob("*.json") if f.is_file()]
    profile_map: dict[str, dict] = {}
    for f in profile_files:
        try:
            profile_map[f.stem] = json.loads(f.read_text())
        except (OSError, json.JSONDecodeError):
            profile_map[f.stem] = {}

    subs = _load(DATA_DIR / "pc_subscribers.json", [])
    if not isinstance(subs, list):
        subs = []
    active = [s for s in subs if s.get("status") == "active"]
    sub_ids = {s.get("user_id", "") for s in active if s.get("user_id")}

    missing = sorted(uid for uid in sub_ids if uid and uid not in profile_map)

    # Thin pantries — count items in each active subscriber's profile
    thin: list[dict] = []
    for s in active:
        uid = s.get("user_id", "")
        if uid not in profile_map:
            continue
        pantry = profile_map[uid].get("pantry", {})
        n = 0
        for _cat, ings in pantry.items() if isinstance(pantry, dict) else []:
            if isinstance(ings, list):
                n += len(ings)
            elif isinstance(ings, str):
                n += 1
        if n < PANTRY_MIN:
            thin.append({"user_id": uid, "items": n})

    return {
        "ok":                   len(profile_files) > 0 or len(subs) > 0,
        "profiles":             len(profile_files),
        "subscribers_total":    len(subs),
        "subscribers_active":   len(active),
        "subs_missing_profile": missing,
        "thin_pantries":        thin,
        "pantry_min":           PANTRY_MIN,
    }


def _cli():
    import argparse
    p = argparse.ArgumentParser(description="PantryChef health + probes")
    p.add_argument("--probe", action="store_true",
                   help="Triangulate profiles + subscribers + pantry depth")
    p.add_argument("--plans", type=int, default=0,
                   help="Show last N per-plan outcomes")
    p.add_argument("--yield", dest="show_yield", action="store_true",
                   help="Recipe-yield distribution")
    p.add_argument("--usage", action="store_true",
                   help="Per-user plans delivered this month")
    args = p.parse_args()
    if args.probe:
        print(json.dumps(probe_inputs(), indent=2))
        return
    if args.plans:
        for r in recent_plans(args.plans):
            print(f"  {r['ts'][:19]}  {r['outcome']:<18s}  "
                  f"{r['user_id']:<20s}  {r.get('detail','')[:50]}")
        s = plan_outcome_summary()
        print(f"\n  log_total={s['total']}  success={s['success']}  "
              f"no_user_profile={s['no_user_profile']}  "
              f"pantry_too_small={s['pantry_too_small']}  "
              f"mail_failed={s['mail_failed']}")
        return
    if args.show_yield:
        s = yield_summary()
        print(f"  total_plans={s['total']}  avg_recipes={s['avg_recipes']}  "
              f"avg_shopping={s['avg_shopping']}  "
              f"thin_plans(<{MIN_RECIPES})={s['thin_plans']}")
        thin_users = users_with_thin_plans()
        if thin_users:
            print("  thin-plan users:")
            for u in thin_users[:10]:
                print(f"    {u['user_id']:<24s}  {u['thin_in_window']}/{u['window']}  "
                      f"last_recipes={u['last_recipes']}")
        return
    if args.usage:
        usage = monthly_usage_per_user()
        if not usage:
            print("(no successful plans recorded this month)")
        else:
            for u, n in sorted(usage.items(), key=lambda kv: -kv[1]):
                print(f"  {u:<24s}  {n}")
        return
    # default: full summary
    s = plan_outcome_summary()
    print(f"  plan log:  total={s['total']}  success={s['success']}  "
          f"no_user_profile={s['no_user_profile']}  "
          f"pantry_too_small={s['pantry_too_small']}  "
          f"mail_failed={s['mail_failed']}")
    y = yield_summary()
    if y["total"]:
        print(f"  yield log: total={y['total']}  avg_recipes={y['avg_recipes']}  "
              f"thin_plans={y['thin_plans']}")


if __name__ == "__main__":
    _cli()
