"""Pinterest organic poster — API v5."""
import os
import datetime
import requests
from .base import PlatformAdapter


class PinterestAdapter(PlatformAdapter):
    name = "pinterest"
    kind = "organic"
    # Get an access token via Pinterest OAuth — scopes: pins:write, boards:read
    env_vars = ["PINTEREST_ACCESS_TOKEN", "PINTEREST_BOARD_ID"]

    def post(self, formatted: dict, dry_run: bool = False) -> dict:
        title = formatted["title"]
        description = formatted["description"]
        if dry_run:
            return {"status": "dry_run", "platform": "pinterest",
                    "title": title, "description": description}

        ok, missing = self.credentials_ok()
        if not ok:
            return {"status": "skipped", "platform": "pinterest",
                    "reason": f"missing env: {', '.join(missing)}"}

        try:
            token = os.environ["PINTEREST_ACCESS_TOKEN"]
            board = os.environ["PINTEREST_BOARD_ID"]
            # Pinterest requires a media source (image). Use the logo as fallback.
            media_url = os.environ.get(
                "PINTEREST_DEFAULT_IMAGE_URL",
                "https://files.catbox.moe/u534iv.png",
            )
            resp = requests.post(
                "https://api.pinterest.com/v5/pins",
                headers={"Authorization": f"Bearer {token}",
                         "Content-Type": "application/json"},
                json={
                    "board_id": board,
                    "title": title,
                    "description": description,
                    "media_source": {"source_type": "image_url", "url": media_url},
                },
                timeout=15,
            )
            if resp.status_code in (200, 201):
                return {"status": "posted", "platform": "pinterest",
                        "id": resp.json().get("id"),
                        "posted_at": datetime.datetime.now().isoformat()}
            return {"status": "failed", "platform": "pinterest",
                    "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}
        except Exception as e:
            return {"status": "failed", "platform": "pinterest", "error": str(e)}
