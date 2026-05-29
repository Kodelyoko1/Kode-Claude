"""LinkedIn organic poster — person or org page."""
import os
import datetime
import requests
from .base import PlatformAdapter


class LinkedInAdapter(PlatformAdapter):
    name = "linkedin"
    kind = "organic"
    # Get an access token via LinkedIn OAuth with `w_member_social` scope.
    # For org-page posts you also need `w_organization_social` (partner approval).
    env_vars = ["LINKEDIN_ACCESS_TOKEN", "LINKEDIN_ACTOR_URN"]

    def post(self, formatted: dict, dry_run: bool = False) -> dict:
        text = formatted["text"]
        if dry_run:
            return {"status": "dry_run", "platform": "linkedin", "text": text}

        ok, missing = self.credentials_ok()
        if not ok:
            return {"status": "skipped", "platform": "linkedin",
                    "reason": f"missing env: {', '.join(missing)} "
                              f"(needs LinkedIn dev app + OAuth)"}

        try:
            token = os.environ["LINKEDIN_ACCESS_TOKEN"]
            actor = os.environ["LINKEDIN_ACTOR_URN"]  # e.g. "urn:li:person:XXXX"
            resp = requests.post(
                "https://api.linkedin.com/v2/ugcPosts",
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-Restli-Protocol-Version": "2.0.0",
                    "Content-Type": "application/json",
                },
                json={
                    "author": actor,
                    "lifecycleState": "PUBLISHED",
                    "specificContent": {
                        "com.linkedin.ugc.ShareContent": {
                            "shareCommentary": {"text": text},
                            "shareMediaCategory": "NONE",
                        }
                    },
                    "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"},
                },
                timeout=15,
            )
            if resp.status_code in (200, 201):
                return {"status": "posted", "platform": "linkedin",
                        "id": resp.headers.get("X-RestLi-Id"),
                        "posted_at": datetime.datetime.now().isoformat()}
            return {"status": "failed", "platform": "linkedin",
                    "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}
        except Exception as e:
            return {"status": "failed", "platform": "linkedin", "error": str(e)}
