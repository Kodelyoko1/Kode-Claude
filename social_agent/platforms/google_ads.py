"""Google Ads — paid. Stub. Requires developer token + OAuth + funded account."""
import os
from .base import PlatformAdapter


class GoogleAdsAdapter(PlatformAdapter):
    name = "google_ads"
    kind = "paid"
    env_vars = ["GOOGLE_ADS_DEVELOPER_TOKEN", "GOOGLE_ADS_CLIENT_ID",
                "GOOGLE_ADS_CLIENT_SECRET", "GOOGLE_ADS_REFRESH_TOKEN",
                "GOOGLE_ADS_CUSTOMER_ID"]

    def post(self, formatted: dict, dry_run: bool = False) -> dict:
        text = formatted["text"]
        if dry_run:
            return {"status": "dry_run", "platform": "google_ads",
                    "headline": text[:30], "description": text[:90],
                    "note": "Would create a responsive search ad — needs budget + targeting config."}

        ok, missing = self.credentials_ok()
        if not ok:
            return {"status": "skipped", "platform": "google_ads",
                    "reason": f"missing env: {', '.join(missing)} "
                              f"(needs google-ads Python SDK + dev token + funded MCC)"}

        return {"status": "failed", "platform": "google_ads",
                "error": "Google Ads adapter requires google-ads SDK setup. "
                         "Run `pip install google-ads` and provide credentials."}
