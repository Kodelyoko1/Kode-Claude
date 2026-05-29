"""Reddit organic poster (PRAW)."""
import os
import datetime
from .base import PlatformAdapter

# Subreddits where wholesale/seller/buyer content is on-topic.
# Reddit is strict about spam — keep posts conversational, not sales-y.
DEFAULT_SUBREDDITS = {
    "sellers":     ["RealEstate", "FirstTimeHomeBuyer"],
    "buyers":      ["realestateinvesting", "RealEstateAdvice"],
    "wholesalers": ["realestateinvesting", "wholesale", "RealEstateTechnology"],
}


class RedditAdapter(PlatformAdapter):
    name = "reddit"
    kind = "organic"
    env_vars = ["REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET",
                "REDDIT_USERNAME", "REDDIT_PASSWORD"]

    def __init__(self):
        self._client = None

    def _get_client(self):
        if self._client is None:
            import praw
            self._client = praw.Reddit(
                client_id     = os.environ["REDDIT_CLIENT_ID"],
                client_secret = os.environ["REDDIT_CLIENT_SECRET"],
                username      = os.environ["REDDIT_USERNAME"],
                password      = os.environ["REDDIT_PASSWORD"],
                user_agent    = "wholesaleomniverse-social/1.0",
            )
        return self._client

    def post(self, formatted: dict, dry_run: bool = False) -> dict:
        audience = formatted.get("audience", "wholesalers")
        subs = DEFAULT_SUBREDDITS.get(audience, DEFAULT_SUBREDDITS["wholesalers"])
        subreddit = subs[0]
        title = formatted["title"]
        body  = formatted["body"]

        if dry_run:
            return {"status": "dry_run", "platform": "reddit",
                    "would_post_to": f"r/{subreddit}", "title": title, "body": body}

        ok, missing = self.credentials_ok()
        if not ok:
            return {"status": "skipped", "platform": "reddit",
                    "reason": f"missing env: {', '.join(missing)}"}

        try:
            r = self._get_client()
            submission = r.subreddit(subreddit).submit(title=title, selftext=body)
            return {"status": "posted", "platform": "reddit",
                    "subreddit": subreddit, "url": submission.url,
                    "posted_at": datetime.datetime.now().isoformat()}
        except Exception as e:
            return {"status": "failed", "platform": "reddit", "error": str(e)}
