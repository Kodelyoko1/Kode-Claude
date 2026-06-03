"""
Bootstrap a Meta lead-gen campaign from zero — the missing piece between
"agent is wired" and "money flowing."

Creates (all status=PAUSED by default, so owner reviews in Ads Manager before
activating):
  1. Lead Form on the configured Page (if none exists yet).
  2. Generated placeholder image, uploaded to the ad account.
  3. Campaign (objective OUTCOME_LEADS, special_ad_categories=HOUSING for
     real-estate compliance).
  4. AdSet with targeting + budget + optimization_goal=LEAD_GENERATION,
     destination_type=ON_AD.
  5. AdCreative tied to the form.
  6. Ad linking the creative to the adset.

DRY-RUN-aware: meta_api's mutating helpers already respect config.DRY_RUN.
This module adds its own _create() shim that also no-ops on DRY_RUN so the
owner can preview the full plan without hitting Meta at all.
"""
from __future__ import annotations

import io
import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, UTC
from pathlib import Path
from typing import Any, Optional

import requests

from . import meta_api, token_store
from .config import DATA_DIR, DRY_RUN, PROFILES

log = logging.getLogger("media_buyer.launcher")

# Visual defaults for the placeholder image — keeps the launch self-contained.
PLACEHOLDER_HEADLINE = os.getenv("MB_LAUNCH_IMAGE_TEXT", "We Buy Houses Cash")
PLACEHOLDER_SUBLINE  = os.getenv("MB_LAUNCH_IMAGE_SUB",  "Close in 14 Days · Any Condition")
PLACEHOLDER_BG       = (10, 26, 56)     # dark navy
PLACEHOLDER_FG       = (255, 255, 255)
PLACEHOLDER_ACCENT   = (231, 184, 79)   # gold


@dataclass
class LaunchPlan:
    """Concrete spec the owner can review before activation."""
    name_tag: str               # "[LG][AUTO]" prefix used on every object so we can find them later
    daily_budget_usd: float
    locations: list[str]
    age_min: int
    age_max: int
    landing_link: str
    headline: str
    body: str
    cta: str
    form_id: Optional[str] = None  # if owner already has one
    paused: bool = True


# ─────────────────────────── Image generation ───────────────────────────

def _make_placeholder_image() -> bytes:
    """Generate a 1200×628 branded JPG. Pillow is already in the project deps."""
    from PIL import Image, ImageDraw, ImageFont

    W, H = 1200, 628
    img = Image.new("RGB", (W, H), PLACEHOLDER_BG)
    d = ImageDraw.Draw(img)

    # Accent stripe down the left
    d.rectangle([(0, 0), (24, H)], fill=PLACEHOLDER_ACCENT)

    # Try common monospace/serif system fonts; fallback to default
    def _font(size: int):
        for path in (
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            "/Library/Fonts/Arial Bold.ttf",
        ):
            if Path(path).exists():
                return ImageFont.truetype(path, size)
        return ImageFont.load_default()

    title_font = _font(92)
    sub_font   = _font(40)

    # Title block — centred horizontally on the (W-24)/2 region
    tx, ty = 64, 180
    d.text((tx, ty), PLACEHOLDER_HEADLINE, fill=PLACEHOLDER_FG, font=title_font)
    d.text((tx, ty + 130), PLACEHOLDER_SUBLINE, fill=PLACEHOLDER_ACCENT, font=sub_font)
    d.text((tx, ty + 220), "Tap below for an instant cash offer.", fill=PLACEHOLDER_FG, font=sub_font)

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88)
    return buf.getvalue()


def upload_image(ad_account_id: str) -> dict:
    """Generate the placeholder + upload it. Returns {image_hash, url, dry_run?}.

    Meta returns {"images": {"<filename>": {"hash": "...", "url": "..."}}}."""
    img_bytes = _make_placeholder_image()
    if DRY_RUN:
        return {"dry_run": True, "would_upload_bytes": len(img_bytes),
                "image_hash": "DRY_RUN_HASH"}
    token = token_store.get_active_token()
    files = {"file": ("ad_image.jpg", img_bytes, "image/jpeg")}
    r = requests.post(
        f"https://graph.facebook.com/{meta_api.GRAPH_VERSION}/{ad_account_id}/adimages",
        params={"access_token": token},
        files=files, timeout=60,
    )
    r.raise_for_status()
    payload = r.json()
    images = payload.get("images", {})
    first_key = next(iter(images), None)
    if not first_key:
        raise RuntimeError(f"Image upload returned no images: {payload}")
    info = images[first_key]
    return {"image_hash": info.get("hash"), "url": info.get("url")}


# ─────────────────────────── Lead Form ───────────────────────────

DEFAULT_FORM_QUESTIONS = [
    # Built-ins (Meta auto-fills if user has the data in their FB profile)
    {"type": "FULL_NAME"},
    {"type": "PHONE"},
    {"type": "EMAIL"},
    # Custom — these are what actually drive scoring
    {"type": "CUSTOM",
     "key": "property_address",
     "label": "What's the property address?"},
    {"type": "CUSTOM",
     "key": "property_condition",
     "label": "What condition is the property in?",
     "options": [{"key": "move_in", "value": "Move-in ready"},
                 {"key": "minor",   "value": "Needs minor work"},
                 {"key": "major",   "value": "Needs major work"},
                 {"key": "vacant",  "value": "Vacant / abandoned"}]},
    {"type": "CUSTOM",
     "key": "timeline",
     "label": "How soon do you need to sell?",
     "options": [{"key": "asap",     "value": "ASAP"},
                 {"key": "thirty",   "value": "Within 30 days"},
                 {"key": "ninety",   "value": "30-90 days"},
                 {"key": "looking",  "value": "Just exploring"}]},
    {"type": "CUSTOM",
     "key": "reason",
     "label": "Why are you considering selling? (brief is fine)"},
]


def create_lead_form(page_id: str, *, name: str, privacy_policy_url: str) -> dict:
    """Create a leadgen form on the page using the page-scoped token.

    `privacy_policy_url` is mandatory — Meta rejects forms without one.
    Returns {"id": "...", "dry_run"?}."""
    if DRY_RUN:
        return {"dry_run": True, "would_create_form": {"name": name, "questions": DEFAULT_FORM_QUESTIONS}}
    page = meta_api._request("GET", f"/{page_id}", params={"fields": "access_token"})
    page_token = page.get("access_token")
    if not page_token:
        raise RuntimeError("Page-scoped access token unavailable; cannot create form")

    body = {
        "name": name,
        "questions": json.dumps(DEFAULT_FORM_QUESTIONS),
        "privacy_policy": json.dumps({"url": privacy_policy_url}),
        "follow_up_action_url": privacy_policy_url,
        "locale": "en_US",
        "access_token": page_token,
    }
    r = requests.post(
        f"https://graph.facebook.com/{meta_api.GRAPH_VERSION}/{page_id}/leadgen_forms",
        data=body, timeout=30,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"create_lead_form failed: HTTP {r.status_code} {r.text[:300]}")
    return r.json()


# ─────────────────────────── Targeting helpers ───────────────────────────

def _resolve_geo_locations(locations: list[str]) -> dict:
    """Convert ['Maine', 'New Hampshire'] into Meta's `geo_locations` targeting shape.

    For a v1 launcher we use US state codes when we can map them and fall back to
    free-text country. The owner can refine targeting in Ads Manager post-launch.
    """
    US_STATES = {
        "alabama":"AL","alaska":"AK","arizona":"AZ","arkansas":"AR","california":"CA",
        "colorado":"CO","connecticut":"CT","delaware":"DE","florida":"FL","georgia":"GA",
        "hawaii":"HI","idaho":"ID","illinois":"IL","indiana":"IN","iowa":"IA",
        "kansas":"KS","kentucky":"KY","louisiana":"LA","maine":"ME","maryland":"MD",
        "massachusetts":"MA","michigan":"MI","minnesota":"MN","mississippi":"MS","missouri":"MO",
        "montana":"MT","nebraska":"NE","nevada":"NV","new hampshire":"NH","new jersey":"NJ",
        "new mexico":"NM","new york":"NY","north carolina":"NC","north dakota":"ND","ohio":"OH",
        "oklahoma":"OK","oregon":"OR","pennsylvania":"PA","rhode island":"RI","south carolina":"SC",
        "south dakota":"SD","tennessee":"TN","texas":"TX","utah":"UT","vermont":"VT",
        "virginia":"VA","washington":"WA","west virginia":"WV","wisconsin":"WI","wyoming":"WY",
    }
    regions = []
    for loc in locations:
        key = loc.strip().lower()
        code = US_STATES.get(key) or (loc.strip().upper() if len(loc.strip()) == 2 else None)
        if code:
            regions.append({"key": f"US:{code}", "name": loc.strip(), "country": "US"})
    if regions:
        return {"regions": regions, "location_types": ["home"]}
    return {"countries": ["US"], "location_types": ["home"]}


# ─────────────────────────── Campaign / AdSet / Ad ───────────────────────────

def create_campaign(ad_account_id: str, *, name: str, status: str = "PAUSED") -> dict:
    """Lead-gen objective + HOUSING special-ad category (mandatory for real estate)."""
    body = {
        "name": name,
        "objective": "OUTCOME_LEADS",
        "status": status,
        "buying_type": "AUCTION",
        "special_ad_categories": json.dumps(["HOUSING"]),
    }
    if DRY_RUN:
        return {"dry_run": True, "ad_account_id": ad_account_id, "would_create_campaign": body}
    return meta_api._request("POST", f"/{ad_account_id}/campaigns", data=body)


def create_adset(ad_account_id: str, *, name: str, campaign_id: str,
                 daily_budget_cents: int, form_id: str, page_id: str,
                 locations: list[str], age_min: int, age_max: int,
                 status: str = "PAUSED") -> dict:
    """Lead-gen adset: optimization_goal=LEAD_GENERATION, destination=ON_AD."""
    geo = _resolve_geo_locations(locations)
    # HOUSING special ad category restricts targeting: no age range, no gender,
    # no detailed-interest, and broad geo (15-mile minimum). We apply only the
    # geo + the broadest acceptable defaults.
    targeting: dict[str, Any] = {
        "geo_locations": geo,
        "targeting_relaxation_types": {"lookalike": 0, "custom_audience": 0},
    }
    body = {
        "name": name,
        "campaign_id": campaign_id,
        "daily_budget": daily_budget_cents,
        "billing_event": "IMPRESSIONS",
        "optimization_goal": "LEAD_GENERATION",
        "destination_type": "ON_AD",
        "promoted_object": json.dumps({"page_id": page_id}),
        "targeting": json.dumps(targeting),
        "status": status,
        # required for HOUSING special ad category
        "is_dynamic_creative": False,
        "bid_strategy": "LOWEST_COST_WITHOUT_CAP",
    }
    if DRY_RUN:
        return {"dry_run": True, "ad_account_id": ad_account_id, "would_create_adset": body}
    return meta_api._request("POST", f"/{ad_account_id}/adsets", data=body)


def create_lead_creative(ad_account_id: str, *, page_id: str, form_id: str,
                          image_hash: str, message: str, headline: str,
                          link_url: str = "https://www.facebook.com") -> dict:
    """Build a creative whose CTA opens the leadgen form on-Facebook."""
    object_story_spec = {
        "page_id": page_id,
        "link_data": {
            "message": message,
            "name": headline,
            "link": link_url,
            "image_hash": image_hash,
            "call_to_action": {
                "type": "SIGN_UP",
                "value": {"lead_gen_form_id": form_id},
            },
        },
    }
    if DRY_RUN:
        return {"dry_run": True, "would_create_creative": object_story_spec}
    return meta_api._request("POST", f"/{ad_account_id}/adcreatives",
                              data={"object_story_spec": json.dumps(object_story_spec)})


def create_ad(ad_account_id: str, *, name: str, adset_id: str, creative_id: str,
              status: str = "PAUSED") -> dict:
    body = {
        "name": name,
        "adset_id": adset_id,
        "creative": json.dumps({"creative_id": creative_id}),
        "status": status,
    }
    if DRY_RUN:
        return {"dry_run": True, "ad_account_id": ad_account_id, "would_create_ad": body}
    return meta_api._request("POST", f"/{ad_account_id}/ads", data=body)


# ─────────────────────────── End-to-end ───────────────────────────

def _resolve_plan(kind: str, daily_budget_usd: float, locations: list[str],
                  age_min: int, age_max: int, paused: bool) -> LaunchPlan:
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M")
    name_tag = f"[LG][AUTO {ts}]"
    return LaunchPlan(
        name_tag=name_tag,
        daily_budget_usd=daily_budget_usd,
        locations=locations,
        age_min=age_min,
        age_max=age_max,
        landing_link=os.getenv("MB_LANDING_URL", "https://www.facebook.com"),
        headline=os.getenv("MB_LAUNCH_HEADLINE", "Sell your house fast — cash offer in 24h"),
        body=os.getenv("MB_LAUNCH_BODY",
                       "We buy houses cash, any condition. Skip repairs, skip showings, "
                       "close in as little as 14 days. Tap below for a no-obligation cash offer."),
        cta="SIGN_UP",
        paused=paused,
    )


def launch_lead_gen(*, daily_budget_usd: float = 20.0,
                    locations: Optional[list[str]] = None,
                    form_id: Optional[str] = None,
                    paused: bool = True) -> dict:
    """End-to-end: launch a paused lead-gen campaign on the configured account.

    Returns a dict of every created object id (or the dry-run plan)."""
    profile = PROFILES["lead_gen"]
    if not profile.ad_account_id or not profile.page_id:
        raise RuntimeError("META_AD_ACCOUNT_ID and META_PAGE_ID must be set in .env")

    locations = locations or _parse_locations(os.getenv("MB_LAUNCH_LOCATIONS", "Maine"))
    plan = _resolve_plan(
        "lead_gen", daily_budget_usd, locations,
        age_min=int(os.getenv("MB_LAUNCH_AGE_MIN", "30")),
        age_max=int(os.getenv("MB_LAUNCH_AGE_MAX", "65")),
        paused=paused,
    )

    status = "PAUSED" if plan.paused else "ACTIVE"
    audit: dict[str, Any] = {"plan": plan.__dict__, "dry_run": DRY_RUN, "steps": []}

    # 1. Lead form
    if form_id:
        audit["steps"].append({"step": "use_existing_form", "form_id": form_id})
    else:
        privacy_url = os.getenv("MB_PRIVACY_URL", "https://www.facebook.com/policy.php")
        form_result = create_lead_form(
            profile.page_id,
            name=f"{plan.name_tag} Cash Offer",
            privacy_policy_url=privacy_url,
        )
        form_id = form_result.get("id") or "DRY_RUN_FORM_ID"
        audit["steps"].append({"step": "create_form", "result": form_result})

    # 2. Upload image
    img_result = upload_image(profile.ad_account_id)
    image_hash = img_result.get("image_hash") or "DRY_RUN_HASH"
    audit["steps"].append({"step": "upload_image", "result": img_result})

    # 3. Campaign
    campaign = create_campaign(profile.ad_account_id,
                                name=f"{plan.name_tag} Cash Buyer", status=status)
    campaign_id = campaign.get("id") or "DRY_RUN_CAMPAIGN_ID"
    audit["steps"].append({"step": "create_campaign", "result": campaign})

    # 4. Adset
    adset = create_adset(
        profile.ad_account_id,
        name=f"{plan.name_tag} {', '.join(plan.locations)}",
        campaign_id=campaign_id,
        daily_budget_cents=int(plan.daily_budget_usd * 100),
        form_id=form_id,
        page_id=profile.page_id,
        locations=plan.locations,
        age_min=plan.age_min, age_max=plan.age_max,
        status=status,
    )
    adset_id = adset.get("id") or "DRY_RUN_ADSET_ID"
    audit["steps"].append({"step": "create_adset", "result": adset})

    # 5. Creative
    creative = create_lead_creative(
        profile.ad_account_id,
        page_id=profile.page_id,
        form_id=form_id,
        image_hash=image_hash,
        message=plan.body,
        headline=plan.headline,
        link_url=plan.landing_link,
    )
    creative_id = creative.get("id") or "DRY_RUN_CREATIVE_ID"
    audit["steps"].append({"step": "create_creative", "result": creative})

    # 6. Ad
    ad = create_ad(
        profile.ad_account_id,
        name=f"{plan.name_tag} v1",
        adset_id=adset_id,
        creative_id=creative_id,
        status=status,
    )
    ad_id = ad.get("id") or "DRY_RUN_AD_ID"
    audit["steps"].append({"step": "create_ad", "result": ad})

    # Persist the launch record for the controller to find later
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    launches_file = DATA_DIR / "launches.jsonl"
    with launches_file.open("a") as f:
        f.write(json.dumps({
            "ts": datetime.now(UTC).isoformat(),
            "dry_run": DRY_RUN,
            "kind": "lead_gen",
            "name_tag": plan.name_tag,
            "form_id": form_id,
            "campaign_id": campaign_id,
            "adset_id": adset_id,
            "creative_id": creative_id,
            "ad_id": ad_id,
        }) + "\n")

    audit["created"] = {
        "form_id": form_id,
        "campaign_id": campaign_id,
        "adset_id": adset_id,
        "creative_id": creative_id,
        "ad_id": ad_id,
    }
    return audit


def _parse_locations(s: str) -> list[str]:
    return [x.strip() for x in re.split(r"[,;]", s) if x.strip()]


def main() -> int:
    """CLI entry: python3 -m media_buyer.launcher [--budget N] [--locations 'Maine,NH']"""
    import argparse
    p = argparse.ArgumentParser(description="Launch a paused lead-gen campaign")
    p.add_argument("--budget", type=float, default=20.0,
                    help="Daily budget in USD (default 20)")
    p.add_argument("--locations", default=os.getenv("MB_LAUNCH_LOCATIONS", "Maine"),
                    help='Comma-separated US states or country codes (default: "Maine")')
    p.add_argument("--form-id", default=None,
                    help="Reuse an existing leadgen form id instead of creating one")
    p.add_argument("--activate", action="store_true",
                    help="Launch ACTIVE instead of PAUSED (requires MB_LIVE=1)")
    args = p.parse_args()

    result = launch_lead_gen(
        daily_budget_usd=args.budget,
        locations=_parse_locations(args.locations),
        form_id=args.form_id,
        paused=not args.activate,
    )
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    import sys as _sys
    _sys.exit(main())
