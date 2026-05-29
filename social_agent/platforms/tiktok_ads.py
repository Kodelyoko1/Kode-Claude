"""TikTok Ads — paid. Stub. Requires Marketing API partner approval."""
import os
from .base import PlatformAdapter


class TikTokAdsAdapter(PlatformAdapter):
    name = "tiktok_ads"
    kind = "paid"
    env_vars = ["TIKTOK_ACCESS_TOKEN", "TIKTOK_ADVERTISER_ID"]

    def post(self, formatted: dict, dry_run: bool = False) -> dict:
        text = formatted["text"]
        if dry_run:
            return {"status": "dry_run", "platform": "tiktok_ads",
                    "ad_text": text,
                    "note": "Would create a Spark Ad — needs video creative + budget config."}

        ok, missing = self.credentials_ok()
        if not ok:
            return {"status": "skipped", "platform": "tiktok_ads",
                    "reason": f"missing env: {', '.join(missing)} "
                              f"(needs TikTok for Business + Marketing API approval)"}

        return {"status": "failed", "platform": "tiktok_ads",
                "error": "TikTok Ads adapter needs video creative pipeline. "
                         "Use Engine 8 (Faceless Video Pipeline) to generate video, then post here."}
