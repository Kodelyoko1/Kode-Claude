"""
Campaign profiles + runtime config for the Autonomous Media Buyer.

A "profile" is the per-business-model contract: what we're optimizing for, what
counts as a kill-worthy ad, what the break-even threshold is, etc. The agent's
monitor/controller/generator modules all branch off `profile.kind` so the same
codebase serves both businesses without if-soup elsewhere.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

ProfileKind = Literal["lead_gen", "ecom"]

# ─────────────────────────── Safety flags ───────────────────────────
# MB_LIVE must be explicitly set to "1" / "true" for any mutating call (budget
# changes, ad pauses, ad creation). Anything else defaults to DRY-RUN: the
# controller logs the intended action but never hits Meta with a mutation.
DRY_RUN: bool = os.getenv("MB_LIVE", "").strip().lower() not in ("1", "true", "yes")

# Hard caps — defense against runaway scale loops, on top of the rules' own bounds.
MAX_DAILY_BUDGET_INCREASE_PCT = float(os.getenv("MB_MAX_DAILY_INCREASE_PCT", "30"))
MAX_ABSOLUTE_DAILY_BUDGET_USD = float(os.getenv("MB_MAX_DAILY_BUDGET_USD", "500"))

# Where we persist insights snapshots, decision audit logs, etc.
DATA_DIR = Path(__file__).parent.parent / "data" / "media_buyer"


# ─────────────────────────── Per-business profiles ───────────────────────────
@dataclass
class CampaignProfile:
    """One profile per business model. Carries optimization targets + rules."""
    kind: ProfileKind
    ad_account_id: str              # act_<numeric_id>
    page_id: str                    # default page for created ads
    pixel_id: str                   # Meta Pixel ID (CAPI + dedup)

    # Optimization targets — both rules and monitor reference these.
    target_cpl_usd: float = 25.0    # lead-gen only
    break_even_roas: float = 1.8    # ecom only (revenue / spend)
    target_aov_usd: float = 45.0    # ecom only

    # Scale + kill thresholds (overridable per business).
    scale_pct_lead_gen: int = 15
    scale_pct_ecom: int = 20
    kill_cpl_multiplier: float = 2.0       # pause if spend > 2x target_cpl and 0 leads
    kill_aov_multiplier: float = 1.0       # pause if spend > 1x AOV and 0 purchases
    refresh_frequency_threshold: float = 3.5
    refresh_min_flag_days: int = 3

    # Optional human-in-the-loop notification channel.
    alert_slack_webhook: str | None = None
    alert_email: str | None = None


def _env(name: str, default: str = "") -> str:
    """Strip + default — keeps dataclass init lines short."""
    return (os.getenv(name) or default).strip()


PROFILES: dict[ProfileKind, CampaignProfile] = {
    "lead_gen": CampaignProfile(
        kind="lead_gen",
        ad_account_id=_env("META_AD_ACCOUNT_ID"),
        page_id=_env("META_PAGE_ID"),
        pixel_id=_env("MB_LEADGEN_PIXEL_ID"),
        target_cpl_usd=float(_env("MB_LEADGEN_TARGET_CPL", "25")),
        alert_slack_webhook=_env("MB_LEADGEN_SLACK_WEBHOOK") or None,
        alert_email=_env("MB_LEADGEN_ALERT_EMAIL") or None,
    ),
    "ecom": CampaignProfile(
        kind="ecom",
        ad_account_id=_env("MB_ECOM_AD_ACCOUNT_ID") or _env("META_AD_ACCOUNT_ID"),
        page_id=_env("MB_ECOM_PAGE_ID") or _env("META_PAGE_ID"),
        pixel_id=_env("MB_ECOM_PIXEL_ID"),
        break_even_roas=float(_env("MB_ECOM_BREAKEVEN_ROAS", "1.8")),
        target_aov_usd=float(_env("MB_ECOM_TARGET_AOV", "45")),
        alert_slack_webhook=_env("MB_ECOM_SLACK_WEBHOOK") or None,
        alert_email=_env("MB_ECOM_ALERT_EMAIL") or None,
    ),
}


def profile_for(campaign_id: str | None = None, kind: ProfileKind | None = None) -> CampaignProfile:
    """Resolve which profile this run/campaign belongs to.

    In practice we tag each campaign in Meta's `name` with a prefix (e.g. "[LG]"
    or "[ECOM]") and route on that. For ad-hoc calls, `kind` overrides.
    """
    if kind:
        return PROFILES[kind]
    if campaign_id and "[LG]" in campaign_id.upper():
        return PROFILES["lead_gen"]
    if campaign_id and "[ECOM]" in campaign_id.upper():
        return PROFILES["ecom"]
    # Default to lead_gen — matches the existing wholesale_agent business.
    return PROFILES["lead_gen"]
