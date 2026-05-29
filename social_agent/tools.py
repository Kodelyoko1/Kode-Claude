"""
Social agent orchestrator — dispatch posts to multiple platforms,
track results, and report status of each adapter.
"""
import json
import datetime
from pathlib import Path

from .content import pick_post, format_for_platform
from .platforms.reddit import RedditAdapter
from .platforms.x import XAdapter
from .platforms.linkedin import LinkedInAdapter
from .platforms.pinterest import PinterestAdapter
from .platforms.meta_ads import MetaAdsAdapter
from .platforms.google_ads import GoogleAdsAdapter
from .platforms.tiktok_ads import TikTokAdsAdapter

DATA_DIR  = Path(__file__).parent.parent / "data"
LOG_FILE  = DATA_DIR / "social_posts.json"

PLATFORMS = {
    "reddit":      RedditAdapter,
    "x":           XAdapter,
    "linkedin":    LinkedInAdapter,
    "pinterest":   PinterestAdapter,
    "meta_ads":    MetaAdsAdapter,
    "google_ads":  GoogleAdsAdapter,
    "tiktok_ads":  TikTokAdsAdapter,
}


def _load(path: Path, default):
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return default


def _save(path: Path, data):
    DATA_DIR.mkdir(exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def _now():
    return datetime.datetime.now().isoformat()


def status_all() -> list:
    """Return live/missing status for every platform adapter."""
    out = []
    for key, cls in PLATFORMS.items():
        out.append(cls().status())
    return out


def dispatch(
    audience: str = "",
    platforms: list = None,
    dry_run: bool = False,
) -> dict:
    """
    Pick a post + dispatch to chosen platforms (or all live ones).
    Returns a summary dict of per-platform results.
    """
    if platforms is None:
        platforms = list(PLATFORMS.keys())

    post = pick_post(audience=audience)
    post_audience = post["audience"]

    results = []
    for key in platforms:
        if key not in PLATFORMS:
            results.append({"platform": key, "status": "unknown_platform"})
            continue
        adapter = PLATFORMS[key]()
        formatted = format_for_platform(post, key)
        formatted["audience"] = post_audience
        res = adapter.post(formatted, dry_run=dry_run)
        results.append(res)

    log = _load(LOG_FILE, [])
    log.append({
        "dispatched_at": _now(),
        "audience": post_audience,
        "title": post["title"],
        "dry_run": dry_run,
        "results": results,
    })
    _save(LOG_FILE, log)

    posted   = sum(1 for r in results if r.get("status") == "posted")
    dry      = sum(1 for r in results if r.get("status") == "dry_run")
    skipped  = sum(1 for r in results if r.get("status") == "skipped")
    failed   = sum(1 for r in results if r.get("status") == "failed")

    return {
        "post": {"title": post["title"], "audience": post_audience},
        "posted": posted,
        "dry_run": dry,
        "skipped": skipped,
        "failed": failed,
        "results": results,
    }


def history(limit: int = 20) -> list:
    log = _load(LOG_FILE, [])
    return log[-limit:][::-1]
