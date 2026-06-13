"""
Push an ad pack to Meta Marketing API.

Reuses the generic create_* helpers in media_buyer/meta_api.py — those know
how to authenticate, hit the right endpoints, and honor DRY_RUN. This module
is purely the fbads-side adapter: map each pack ad's `campaign_objective`
(MESSAGES, TRAFFIC) to the right Meta tuple of (objective, optimization_goal,
destination_type, CTA), upload its image once per unique path, then create
the campaign → adset → creative → ad chain. Everything lands PAUSED so the
owner can review in Ads Manager before unleashing budget.

Env required for a live push (--launch --live):
  META_ACCESS_TOKEN   — System User token (or 60-day OAuth via media_buyer.token_store)
  META_AD_ACCOUNT_ID  — act_<numeric_id>
  META_PAGE_ID        — your Page's numeric ID

Dry-run path:
  --launch (no --live) → returns what WOULD post, no API calls fired.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

DATA_DIR        = Path(__file__).parent.parent / "data"
LAUNCH_LEDGER   = DATA_DIR / "fbads_launched.json"


# ─────────────────────────── Dedup ledger ───────────────────────────
#
# Cron may run --launch multiple times per day (and across days as packs
# are rebuilt). The ledger records every successful (pack_date, ad_name)
# launch with the Meta IDs so re-launches skip ads we already pushed.
# Shape: {"<pack_date>": [{"ad_name": ..., "campaign_id": ..., "ad_id": ..., "ts": ...}, ...]}


def _load_ledger() -> dict:
    if not LAUNCH_LEDGER.exists():
        return {}
    try:
        data = json.loads(LAUNCH_LEDGER.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_ledger(data: dict) -> None:
    LAUNCH_LEDGER.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{LAUNCH_LEDGER.name}.",
                               suffix=".tmp", dir=LAUNCH_LEDGER.parent)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, LAUNCH_LEDGER)
    except Exception:
        try: os.unlink(tmp)
        except OSError: pass
        raise


def _already_launched_set(pack_date: str) -> set[str]:
    """Set of ad_names already in the ledger for a given pack date."""
    entries = _load_ledger().get(pack_date, [])
    return {e["ad_name"] for e in entries if isinstance(e, dict) and e.get("ad_name")}


def _record_launch(pack_date: str, entry: dict) -> None:
    ledger = _load_ledger()
    ledger.setdefault(pack_date, []).append({**entry, "ts": datetime.now().isoformat()})
    _save_ledger(ledger)


# ─────────────────────────── Objective mapping ───────────────────────────
#
# fbads packs use the shorthand `MESSAGES` / `TRAFFIC` on each ad. Meta's
# Marketing API needs the full OUTCOME_* tuple plus matched optimization
# goal, destination type, and CTA. Centralizing the mapping here keeps the
# launcher logic dumb — one lookup per ad.

OBJECTIVE_MAP = {
    "MESSAGES": {
        "campaign_objective":  "OUTCOME_ENGAGEMENT",
        "optimization_goal":   "CONVERSATIONS",
        "destination_type":    "MESSENGER",
        "cta":                 "MESSAGE_PAGE",
        "needs_promoted_obj":  True,
    },
    "TRAFFIC": {
        "campaign_objective":  "OUTCOME_TRAFFIC",
        "optimization_goal":   "LINK_CLICKS",
        "destination_type":    "WEBSITE",
        "cta":                 "LEARN_MORE",
        "needs_promoted_obj":  False,
    },
}


def _have_creds() -> tuple[bool, list[str]]:
    required = ["META_ACCESS_TOKEN", "META_AD_ACCOUNT_ID", "META_PAGE_ID"]
    missing = [v for v in required if not os.environ.get(v, "").strip()]
    return (not missing, missing)


def _ad_targeting(ad: dict) -> dict:
    """Convert the pack's free-text targeting into Meta's API shape.

    Pack format (from fbads/tools.py):
      {"locations": "United States" or "Detroit, MI; Memphis, TN; ...",
       "age_min": 25, "age_max": 65, "interests": "..."}

    For v1 we hand off only geo + age. Interests need numeric IDs (Targeting
    Search API), which we don't query at launch time. Owner refines in Ads
    Manager. For multi-city free-text we fall back to US country targeting
    so Meta accepts the payload; the owner narrows in UI.
    """
    t = ad.get("targeting", {}) or {}
    geo: dict = {"countries": ["US"]}
    locations_raw = (t.get("locations") or "").strip()
    if locations_raw and locations_raw.lower() not in ("united states", "us", "usa"):
        # Multi-city free-text — keep US country, owner refines per-city in UI.
        pass
    age_min = int(t.get("age_min") or 18)
    age_max = int(t.get("age_max") or 65)
    return {
        "geo_locations": geo,
        "age_min":       max(13, min(65, age_min)),
        "age_max":       max(age_min, min(65, age_max)),
        "targeting_relaxation_types": {"lookalike": 0, "custom_audience": 0},
        # Meta now requires this flag explicitly. 0 = strict targeting (our
        # age controls hold); 1 = AI-expanded but conflicts with age_min > 25
        # which we set for wholesale audiences (sellers 35+, buyers 30+, etc).
        # Owner can flip to 1 in Ads Manager per adset post-launch if desired.
        "targeting_automation": {"advantage_audience": 0},
    }


def launch_pack(pack: dict, dry: bool = False, max_ads: int = 0,
                use_ledger: bool = True) -> dict:
    """Push every ad in the pack as paused campaign + adset + creative + ad.

    use_ledger=True (default) skips ad_names already recorded in
    data/fbads_launched.json for this pack_date — the cron-safe path.
    Pass False to force-relaunch (e.g. after a manual cleanup in Meta).

    Returns {"launched": N, "skipped": N, "skipped_dedup": N,
             "errors": [...], "campaigns": [...]}.
    On dry, returns what WOULD post (objective tuple + budget + destination).
    """
    ready, missing = _have_creds()
    if not ready and not dry:
        return {"launched": 0, "skipped": len(pack.get("ads", [])),
                "skipped_dedup": 0,
                "errors": [{"reason": f"missing env: {','.join(missing)}"}],
                "campaigns": []}

    pack_date = pack.get("date") or datetime.now().strftime("%Y-%m-%d")
    already = _already_launched_set(pack_date) if use_ledger else set()

    out: dict = {"launched": 0, "skipped": 0, "skipped_dedup": 0,
                 "errors": [], "campaigns": []}
    ads = pack.get("ads", [])
    # Apply dedup first, THEN max — so we don't waste the max budget on ads
    # that are already in the ledger.
    if use_ledger and already:
        before = len(ads)
        ads = [a for a in ads if a["ad_name"] not in already]
        out["skipped_dedup"] = before - len(ads)
    if max_ads > 0:
        ads = ads[:max_ads]

    # Dry-run path: enumerate what would post, no Meta calls.
    if dry:
        for a in ads:
            obj = OBJECTIVE_MAP.get(a["campaign_objective"])
            if not obj:
                out["errors"].append({"ad_name": a["ad_name"],
                                      "reason": f"unmapped objective {a['campaign_objective']}"})
                out["skipped"] += 1
                continue
            out["campaigns"].append({
                "ad_name":      a["ad_name"],
                "audience":     a["audience"],
                "objective":    a["campaign_objective"],
                "meta_obj":     obj["campaign_objective"],
                "opt_goal":     obj["optimization_goal"],
                "dest_type":    obj["destination_type"],
                "cta":          obj["cta"],
                "daily_budget": a["daily_budget"],
                "destination":  a["destination"],
                "would_create": "campaign + adset + creative + ad (PAUSED)",
            })
            out["launched"] += 1
        return out

    # Live path. media_buyer.config.DRY_RUN is read once at import time from
    # MB_LIVE — and meta_api did `from .config import DRY_RUN`, which binds the
    # value into its own module namespace. fbads's `--live` is an explicit
    # owner authorization, so patch BOTH module-level bindings to False before
    # firing. Without this, every create_* below returns its DRY_RUN sentinel
    # and the launcher reports "campaign create returned no id" mysteriously.
    os.environ["MB_LIVE"] = "1"
    import media_buyer.config as _mb_cfg
    import media_buyer.meta_api as _mb_meta
    _mb_cfg.DRY_RUN = False
    _mb_meta.DRY_RUN = False
    from media_buyer.meta_api import (create_campaign_generic, create_adset_generic,
                                      create_link_creative, create_ad_generic,
                                      upload_image_from_path)
    ad_account_id = os.environ["META_AD_ACCOUNT_ID"]
    page_id = os.environ["META_PAGE_ID"]
    image_cache: dict[str, str] = {}

    for a in ads:
        ad_name = a["ad_name"]
        obj = OBJECTIVE_MAP.get(a["campaign_objective"])
        if not obj:
            out["errors"].append({"ad_name": ad_name,
                                  "reason": f"unmapped objective {a['campaign_objective']}"})
            out["skipped"] += 1
            continue

        try:
            # 1. Resolve image_hash (cached per source path)
            img_path = a.get("image_hint") or "data/logo.png"
            if not os.path.exists(img_path):
                raise FileNotFoundError(f"image_hint not found: {img_path}")
            if img_path not in image_cache:
                up = upload_image_from_path(ad_account_id, img_path)
                image_cache[img_path] = up["image_hash"]
            image_hash = image_cache[img_path]

            # 2. Campaign
            camp = create_campaign_generic(
                ad_account_id,
                name=f"FBAds-{ad_name}",
                objective=obj["campaign_objective"],
                status="PAUSED",
            )
            campaign_id = camp.get("id", "")
            if not campaign_id:
                raise RuntimeError(f"campaign create returned no id: {camp}")

            # 3. Ad set
            promoted_object = ({"page_id": page_id}
                               if obj["needs_promoted_obj"] else None)
            adset = create_adset_generic(
                ad_account_id,
                name=f"adset-{ad_name}",
                campaign_id=campaign_id,
                daily_budget_cents=int(a["daily_budget"] * 100),
                optimization_goal=obj["optimization_goal"],
                destination_type=obj["destination_type"],
                targeting=_ad_targeting(a),
                promoted_object=promoted_object,
                status="PAUSED",
            )
            adset_id = adset.get("id", "")
            if not adset_id:
                raise RuntimeError(f"adset create returned no id: {adset}")

            # 4. Creative
            creative = create_link_creative(
                ad_account_id, page_id,
                message=a["primary_text"],
                headline=a["headline_full"],
                link_url=a["destination"],
                image_hash=image_hash,
                description=a.get("description", ""),
                call_to_action=obj["cta"],
            )
            creative_id = creative.get("id", "")
            if not creative_id:
                raise RuntimeError(f"creative create returned no id: {creative}")

            # 5. Ad
            ad = create_ad_generic(
                ad_account_id,
                name=ad_name,
                adset_id=adset_id,
                creative_id=creative_id,
                status="PAUSED",
            )
            ad_id = ad.get("id", "")

            launched_entry = {
                "ad_name":     ad_name,
                "audience":    a["audience"],
                "objective":   a["campaign_objective"],
                "campaign_id": campaign_id,
                "adset_id":    adset_id,
                "creative_id": creative_id,
                "ad_id":       ad_id,
                "status":      "PAUSED — review then unpause in Ads Manager",
            }
            out["campaigns"].append(launched_entry)
            if use_ledger:
                _record_launch(pack_date, launched_entry)
            out["launched"] += 1

        except Exception as e:
            out["errors"].append({"ad_name": ad_name,
                                  "reason": f"{type(e).__name__}: {str(e)[:240]}"})
            out["skipped"] += 1

    return out
