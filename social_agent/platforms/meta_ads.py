"""Meta (Facebook + Instagram) Ads — paid. Stub until credentials configured."""
import os
import datetime
import requests
from .base import PlatformAdapter


class MetaAdsAdapter(PlatformAdapter):
    name = "meta_ads"
    kind = "organic"  # organic Page feed posts; boost via .boost_post() needs META_AD_ACCOUNT_ID
    env_vars = ["META_ACCESS_TOKEN", "META_PAGE_ID"]

    def post(self, formatted: dict, dry_run: bool = False) -> dict:
        text = formatted["text"]
        if dry_run:
            return {"status": "dry_run", "platform": "meta_ads",
                    "text": text, "note": "Would create an organic page post; "
                                            "boost requires explicit budget call."}

        ok, missing = self.credentials_ok()
        if not ok:
            return {"status": "skipped", "platform": "meta_ads",
                    "reason": f"missing env: {', '.join(missing)} "
                              f"(needs Meta Business Manager + Marketing API approval)"}

        # Organic page post first — boosting is a separate call with budget.
        try:
            token = os.environ["META_ACCESS_TOKEN"]
            page  = os.environ["META_PAGE_ID"]
            resp = requests.post(
                f"https://graph.facebook.com/v20.0/{page}/feed",
                params={"access_token": token},
                data={"message": text},
                timeout=15,
            )
            if resp.status_code == 200:
                return {"status": "posted", "platform": "meta_ads",
                        "id": resp.json().get("id"),
                        "posted_at": datetime.datetime.now().isoformat(),
                        "note": "Organic post only — to boost, call boost_post() with budget."}
            return {"status": "failed", "platform": "meta_ads",
                    "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}
        except Exception as e:
            return {"status": "failed", "platform": "meta_ads", "error": str(e)}
