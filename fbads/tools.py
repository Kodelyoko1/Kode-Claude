"""
FBAds — Facebook ad pack generator + Meta-importable CSV exporter.

Two outputs:
  1. Ad pack JSON in data/fb_packs/<date>.json  — every ad, copy, image
     hint, targeting block, daily budget per audience.
  2. Meta Ads Manager bulk CSV in data/fb_packs/<date>.csv —
     import-ready file you upload at Meta Ads Manager → Bulk import.

API push path (separate flow):
  When META_ACCESS_TOKEN + META_AD_ACCOUNT_ID + META_PAGE_ID are set,
  run `python3 run_fbads_auto.py --launch` to push the latest pack via
  media_buyer.launcher. Otherwise the pack stays as a paste-into-Meta
  draft and you manually create the campaigns.
"""
from __future__ import annotations

import csv
import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from social_agent.content import ALL_POSTS

DATA_DIR  = Path(__file__).parent.parent / "data"
PACK_DIR  = DATA_DIR / "fb_packs"

AUDIENCE_TARGETING = {
    "sellers": {
        "label":           "Motivated sellers (US distressed-property markets)",
        "locations":       "Detroit, MI; Memphis, TN; Atlanta, GA; Cleveland, OH; Chicago, IL",
        "age_min":         35,
        "age_max":         65,
        "interests":       "Real Estate; Home Selling; Foreclosure; Probate",
        "objective":       "MESSAGES",
        "daily_budget":    7,
        "duration_days":   3,
    },
    "buyers": {
        "label":           "Cash buyers / fix-and-flip investors",
        "locations":       "Detroit, MI; Memphis, TN; Atlanta, GA; Cleveland, OH; Chicago, IL",
        "age_min":         30,
        "age_max":         60,
        "interests":       "Real Estate Investing; BiggerPockets; Fix and Flip; Rental Property",
        "objective":       "MESSAGES",
        "daily_budget":    10,
        "duration_days":   5,
    },
    "wholesalers": {
        "label":           "Real estate wholesalers + new investors",
        "locations":       "United States",
        "age_min":         25,
        "age_max":         55,
        "interests":       "Real Estate Wholesaling; BiggerPockets; Real Estate Investing",
        "objective":       "TRAFFIC",
        "daily_budget":    10,
        "duration_days":   5,
    },
    "creators": {
        "label":           "Gumroad / Payhip / indie SaaS creators",
        "locations":       "United States; Canada; United Kingdom; Australia",
        "age_min":         22,
        "age_max":         50,
        "interests":       "Gumroad; IndieHackers; SaaS; Solopreneur; Online Business",
        "objective":       "MESSAGES",
        "daily_budget":    8,
        "duration_days":   5,
    },
    "jobseekers": {
        "label":           "Active job seekers (tech + white-collar)",
        "locations":       "United States",
        "age_min":         24,
        "age_max":         55,
        "interests":       "LinkedIn; Job Search; Resume Writing; Indeed; Glassdoor",
        "objective":       "MESSAGES",
        "daily_budget":    10,
        "duration_days":   5,
    },
    "podcasters": {
        "label":           "Podcasters + YouTubers + audio content creators",
        "locations":       "United States; Canada; United Kingdom",
        "age_min":         24,
        "age_max":         55,
        "interests":       "Podcasting; Anchor; Spotify for Podcasters; YouTube",
        "objective":       "MESSAGES",
        "daily_budget":    7,
        "duration_days":   5,
    },
    "local_biz": {
        "label":           "Local small business owners (restaurants, services)",
        "locations":       "United States",
        "age_min":         28,
        "age_max":         60,
        "interests":       "Small Business Ownership; Google My Business; Yelp; Local Marketing",
        "objective":       "MESSAGES",
        "daily_budget":    7,
        "duration_days":   5,
    },
}


def _slug(s: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:40]


def build_pack(audiences: list[str] = None, ads_per_audience: int = 3) -> dict:
    """Assemble a full ad pack — every audience, N posts each.
    Pack date is today; ad_name encodes the audience and a slug of the title."""
    if audiences is None:
        audiences = list(AUDIENCE_TARGETING.keys())
    today = datetime.now().strftime("%Y-%m-%d")
    ads = []
    for aud in audiences:
        candidates = [p for p in ALL_POSTS if p.get("audience") == aud]
        # Take up to ads_per_audience, deterministically sorted by title
        candidates.sort(key=lambda p: p["title"])
        chosen = candidates[:ads_per_audience]
        targeting = AUDIENCE_TARGETING.get(aud, {})
        for i, post in enumerate(chosen, 1):
            primary_text = f"{post['body']}\n\n{post['cta']}"
            ads.append({
                "ad_name":      f"{today}-{aud}-{i:02d}-{_slug(post['title'])}",
                "audience":     aud,
                "audience_label": targeting.get("label", ""),
                "campaign_objective": targeting.get("objective", "MESSAGES"),
                "headline":     post["title"][:40],
                "headline_full": post["title"],
                "primary_text": primary_text,
                "description":  " ".join(post["hashtags"]),
                "destination":  ("https://m.me/Wholesale.Omniverse"
                                 if targeting.get("objective") == "MESSAGES"
                                 else "https://wholesaleomniverse.com"),
                "image_hint":   "data/logo.png",
                "targeting": {
                    "locations": targeting.get("locations", ""),
                    "age_min":   targeting.get("age_min", 25),
                    "age_max":   targeting.get("age_max", 65),
                    "interests": targeting.get("interests", ""),
                },
                "daily_budget":  targeting.get("daily_budget", 7),
                "duration_days": targeting.get("duration_days", 5),
            })
    return {"date": today, "ads": ads, "total": len(ads),
            "audiences": audiences,
            "potential_daily_spend": sum(a["daily_budget"] for a in ads)}


def save_pack_json(pack: dict) -> Path:
    PACK_DIR.mkdir(parents=True, exist_ok=True)
    path = PACK_DIR / f"{pack['date']}.json"
    path.write_text(json.dumps(pack, indent=2))
    return path


def save_pack_csv(pack: dict) -> Path:
    """Meta Ads Manager Bulk Import format. Columns map to what shows up
    in Meta's CSV importer (Ads → ⋯ → More tools → Bulk import).
    NOTE: Meta's exact column names drift; for a guaranteed import build
    one ad in the UI, click Export, and replace this header with what
    Meta gives you. The data columns below are the safe baseline."""
    PACK_DIR.mkdir(parents=True, exist_ok=True)
    path = PACK_DIR / f"{pack['date']}.csv"
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "Campaign Name", "Campaign Objective",
            "Ad Set Name", "Daily Budget",
            "Targeting — Locations", "Targeting — Age Min", "Targeting — Age Max",
            "Targeting — Interests",
            "Ad Name", "Title", "Body", "Description",
            "Link / Destination URL", "Image",
        ])
        for ad in pack["ads"]:
            t = ad["targeting"]
            campaign_name = f"{pack['date']} · {ad['audience']}"
            adset_name = f"{ad['audience']} adset"
            w.writerow([
                campaign_name, ad["campaign_objective"],
                adset_name, ad["daily_budget"],
                t["locations"], t["age_min"], t["age_max"], t["interests"],
                ad["ad_name"], ad["headline"], ad["primary_text"], ad["description"],
                ad["destination"], ad["image_hint"],
            ])
    return path


def latest_pack() -> dict:
    """Return the most recent saved pack (for --launch).
    Excludes *_higgsfield.json sidecars."""
    if not PACK_DIR.exists():
        return {}
    jsons = sorted(p for p in PACK_DIR.glob("*.json")
                   if not p.stem.endswith("_higgsfield"))
    if not jsons:
        return {}
    return json.loads(jsons[-1].read_text())


def render_summary(pack: dict) -> str:
    lines = [
        f"Ad pack {pack['date']} — {pack['total']} ads across {len(pack['audiences'])} audiences",
        f"Potential daily spend if ALL launched: ${pack['potential_daily_spend']:.0f}",
        "",
        f"{'AUDIENCE':<14s}  {'COUNT':>5s}  {'$/day':>6s}  AUDIENCE LABEL",
    ]
    by_audience: dict[str, dict] = {}
    for a in pack["ads"]:
        d = by_audience.setdefault(a["audience"],
                                   {"count": 0, "spend": 0, "label": a["audience_label"]})
        d["count"] += 1
        d["spend"] += a["daily_budget"]
    for aud, d in sorted(by_audience.items()):
        lines.append(f"{aud:<14s}  {d['count']:>5d}  ${d['spend']:>5.0f}  {d['label']}")
    return "\n".join(lines)
