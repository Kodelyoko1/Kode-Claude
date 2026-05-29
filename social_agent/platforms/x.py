"""X (Twitter) organic poster — API v2."""
import os
import datetime
from .base import PlatformAdapter


class XAdapter(PlatformAdapter):
    name = "x"
    kind = "organic"
    env_vars = ["X_BEARER_TOKEN", "X_API_KEY", "X_API_SECRET",
                "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET"]

    def post(self, formatted: dict, dry_run: bool = False) -> dict:
        text = formatted["text"]
        if dry_run:
            return {"status": "dry_run", "platform": "x", "text": text}

        ok, missing = self.credentials_ok()
        if not ok:
            return {"status": "skipped", "platform": "x",
                    "reason": f"missing env: {', '.join(missing)} "
                              f"(X API requires paid Basic tier — $200/mo)"}

        try:
            from requests_oauthlib import OAuth1Session
            oauth = OAuth1Session(
                os.environ["X_API_KEY"], client_secret=os.environ["X_API_SECRET"],
                resource_owner_key=os.environ["X_ACCESS_TOKEN"],
                resource_owner_secret=os.environ["X_ACCESS_TOKEN_SECRET"],
            )
            resp = oauth.post("https://api.twitter.com/2/tweets", json={"text": text})
            if resp.status_code in (200, 201):
                return {"status": "posted", "platform": "x",
                        "id": resp.json().get("data", {}).get("id"),
                        "posted_at": datetime.datetime.now().isoformat()}
            return {"status": "failed", "platform": "x",
                    "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}
        except Exception as e:
            return {"status": "failed", "platform": "x", "error": str(e)}
