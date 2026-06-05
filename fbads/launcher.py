"""
Push an ad pack to Meta Marketing API.

Wraps media_buyer/meta_api.py + media_buyer/launcher.py — those already
know how to authenticate, create campaigns, ad sets, and ads. We just
adapt each pack entry to the existing launcher's input shape.

Requires three env vars (the same media_buyer's diagnose flags):
  META_ACCESS_TOKEN   — System User token from Business Manager
  META_AD_ACCOUNT_ID  — act_<numeric_id> (find at business.facebook.com)
  META_PAGE_ID        — your Page's numeric ID (Page → About → Page ID)

Set FBADS_DRY=1 to run through the launch logic without actually creating
records (useful for testing what would be launched).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))


def _have_creds() -> tuple[bool, list[str]]:
    """Return (ready, missing_var_names)."""
    required = ["META_ACCESS_TOKEN", "META_AD_ACCOUNT_ID", "META_PAGE_ID"]
    missing = [v for v in required if not os.environ.get(v, "").strip()]
    return (not missing, missing)


def launch_pack(pack: dict, dry: bool = False, max_ads: int = 0) -> dict:
    """Push every ad in the pack via media_buyer's launcher.

    Returns {"launched": N, "skipped": N, "errors": [...], "campaigns": [...]}.
    """
    ready, missing = _have_creds()
    if not ready and not dry:
        return {"launched": 0, "skipped": len(pack.get("ads", [])),
                "errors": [{"reason": f"missing env: {','.join(missing)}"}]}

    out = {"launched": 0, "skipped": 0, "errors": [], "campaigns": []}
    ads = pack.get("ads", [])
    if max_ads > 0:
        ads = ads[:max_ads]

    # Lazy-import the media_buyer launcher only when we're actually firing —
    # otherwise just enumerate what WOULD launch
    if dry:
        for a in ads:
            out["campaigns"].append({
                "ad_name":      a["ad_name"],
                "audience":     a["audience"],
                "objective":    a["campaign_objective"],
                "daily_budget": a["daily_budget"],
                "destination":  a["destination"],
                "would_create": "campaign + adset + ad",
            })
            out["launched"] += 1
        return out

    try:
        from media_buyer.meta_api import MetaAPI
        from media_buyer.config import MetaConfig
        cfg = MetaConfig()
        api = MetaAPI(cfg)
    except Exception as e:
        return {"launched": 0, "skipped": len(ads),
                "errors": [{"reason": f"{type(e).__name__}: {e}"}]}

    for a in ads:
        try:
            # 1. Campaign
            camp = api.create_campaign(
                name=f"FBAds-{a['ad_name']}",
                objective=a["campaign_objective"],
                status="PAUSED",  # always PAUSED first — owner reviews then unpause
            )
            campaign_id = camp.get("id", "")
            # 2. Ad set
            adset = api.create_ad_set(
                campaign_id=campaign_id,
                name=f"adset-{a['ad_name']}",
                daily_budget_cents=int(a["daily_budget"] * 100),
                targeting={
                    "geo_locations":  {"countries": ["US"]},
                    "age_min":        a["targeting"]["age_min"],
                    "age_max":        a["targeting"]["age_max"],
                },
                status="PAUSED",
            )
            adset_id = adset.get("id", "")
            # 3. Ad creative + ad
            creative = api.create_creative(
                page_id=os.environ["META_PAGE_ID"],
                title=a["headline"],
                body=a["primary_text"],
                link=a["destination"],
                image_path=a.get("image_hint", ""),
            )
            creative_id = creative.get("id", "")
            ad = api.create_ad(
                adset_id=adset_id,
                creative_id=creative_id,
                name=a["ad_name"],
                status="PAUSED",
            )
            out["campaigns"].append({
                "ad_name":      a["ad_name"],
                "campaign_id":  campaign_id,
                "adset_id":     adset_id,
                "ad_id":        ad.get("id", ""),
                "status":       "PAUSED — review then unpause in Meta Ads Manager",
            })
            out["launched"] += 1
        except Exception as e:
            out["errors"].append({"ad_name": a["ad_name"],
                                  "reason": f"{type(e).__name__}: {str(e)[:200]}"})
            out["skipped"] += 1
    return out
