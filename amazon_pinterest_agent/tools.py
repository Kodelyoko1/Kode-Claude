"""
Amazon Influencer <> Pinterest Agent — storefront curation, SEO pin
generation, compliant dispatch, and performance tracking.

Owner workflow:
  1. Maintain data/ap_storefront.json — the curated product manifest (ASIN,
     category, summary, audience, benefit, keywords, image_urls, collection).
     This mirrors the "Idea Lists" / storefront collections you'd build by
     hand in Amazon's Influencer dashboard. `image_urls` is a list of up to
     10 listing photos per product — every re-pin of the same ASIN (after
     its cooldown) rotates to a never-used-yet image before repeating any,
     so the same product can be pinned many times without ever looking like
     a duplicate-image spam pattern. A single legacy `image_url` string is
     still accepted for products with only one photo.
  2. Set AMAZON_ASSOCIATE_TAG (or associate_tag in the manifest) +
     PINTEREST_ACCESS_TOKEN. Optionally AP_BOARD_MAP as a JSON string mapping
     product category -> Pinterest board_id (falls back to PINTEREST_BOARD_ID
     for everything if unset).
  3. Run the cycle. Each run curates a small batch (round-robin, respecting a
     per-product re-pin cooldown), builds a tagged affiliate link, generates
     SEO pin copy, runs it through compliance.check_pin(), and dispatches
     anything that passes.

Data:
  data/ap_storefront.json — owner-maintained product manifest (input)
  data/ap_pins.json       — full dispatch log (every attempt, posted or not)
  data/ap_performance.json — cached Pinterest analytics per pin_id
"""
import json
import os
import time
from datetime import datetime, timedelta

import requests

from autonomous import storage, metrics
from .compliance import check_pin
from .copywriter import generate_pin_copy

AGENT_KEY = "amazon_pinterest"

STOREFRONT_FILE = "ap_storefront.json"
PINS_FILE = "ap_pins.json"
PERFORMANCE_FILE = "ap_performance.json"


# ============================================================================
# CURATION
# ============================================================================

def _load_config() -> dict:
    return storage.load(STOREFRONT_FILE, {"associate_tag": "", "products": []})


def curate_batch(max_pins: int) -> list:
    """Pick up to `max_pins` active products that are outside their re-pin
    cooldown, least-recently-pinned first — this is what keeps the storefront
    collection rotating instead of hammering the same few products."""
    cfg = _load_config()
    products = [p for p in cfg.get("products", []) if p.get("status", "active") == "active" and p.get("asin")]

    pins_log = storage.load(PINS_FILE, [])
    last_pinned = {}
    for entry in pins_log:
        if entry.get("status") == "posted":
            last_pinned[entry.get("asin")] = entry.get("dispatched_at_epoch", 0)

    cooldown_seconds = int(os.environ.get("AP_PRODUCT_REPIN_DAYS", "21")) * 86400
    now = time.time()
    eligible = [p for p in products if now - last_pinned.get(p["asin"], 0) >= cooldown_seconds]
    eligible.sort(key=lambda p: last_pinned.get(p["asin"], 0))
    return eligible[:max_pins]


MAX_IMAGES_PER_PRODUCT = 10


def _pick_image_url(product: dict, pins_log: list) -> str:
    """Pick which of this product's (up to 10) images to use for this pin.

    Prefers an image that's never been posted for this ASIN yet; once every
    image has been used at least once, cycles back to whichever was used
    longest ago. This is what lets the same product get re-pinned many
    times across its lifetime without compliance.check_pin()'s duplicate-
    image throttle ever seeing back-to-back repeats of one photo."""
    images = product.get("image_urls") or []
    if not images and product.get("image_url"):
        images = [product["image_url"]]  # legacy single-image field
    images = images[:MAX_IMAGES_PER_PRODUCT]

    if not images:
        return ""
    if len(images) == 1:
        return images[0]

    last_used = {}
    for entry in pins_log:
        if entry.get("asin") == product.get("asin") and entry.get("image_url"):
            last_used[entry["image_url"]] = max(
                last_used.get(entry["image_url"], 0), entry.get("dispatched_at_epoch", 0)
            )

    # get(url, -1): an image never used sorts before any real timestamp,
    # so unused images are exhausted before anything repeats.
    return min(images, key=lambda url: last_used.get(url, -1))


# ============================================================================
# AFFILIATE LINKS
# ============================================================================

def build_affiliate_link(asin: str, tag: str) -> str:
    """Direct, tagged amazon.com link — never a third-party cloaked
    redirector. Amazon's Associates Operating Agreement requires the tag be
    visible and the destination be amazon's own domain."""
    return f"https://www.amazon.com/dp/{asin}/?tag={tag}"


# ============================================================================
# DISPATCH
# ============================================================================

def _resolve_board(category: str) -> str:
    raw = os.environ.get("AP_BOARD_MAP", "")
    if raw:
        try:
            board_map = json.loads(raw)
            if category in board_map:
                return board_map[category]
            if "default" in board_map:
                return board_map["default"]
        except json.JSONDecodeError:
            pass
    return os.environ.get("PINTEREST_BOARD_ID", "")


def _post_pin(pin: dict, dry_run: bool, board_override: str = None) -> dict:
    if dry_run:
        return {"status": "dry_run"}

    token = os.environ.get("PINTEREST_ACCESS_TOKEN")
    if not token:
        return {"status": "skipped", "error": "missing PINTEREST_ACCESS_TOKEN"}

    board_id = board_override or _resolve_board(pin.get("board_category", "default"))
    if not board_id:
        return {"status": "skipped",
                "error": f"no Pinterest board mapped for category '{pin.get('board_category')}' "
                         f"(set AP_BOARD_MAP or PINTEREST_BOARD_ID)"}
    if not pin.get("image_url"):
        return {"status": "skipped", "error": "product has no image_url"}

    try:
        resp = requests.post(
            "https://api.pinterest.com/v5/pins",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={
                "board_id": board_id,
                "title": pin["title"],
                "description": pin["description"],
                "link": pin["link"],
                "media_source": {"source_type": "image_url", "url": pin["image_url"]},
            },
            timeout=15,
        )
        if resp.status_code in (200, 201):
            return {"status": "posted", "pin_id": resp.json().get("id")}
        return {"status": "failed", "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}
    except Exception as e:
        return {"status": "failed", "error": str(e)}


# ============================================================================
# FULL CYCLE
# ============================================================================

def run_full_cycle(max_pins: int = None, board_override: str = None, dry_run: bool = False) -> dict:
    cfg = _load_config()
    tag = os.environ.get("AMAZON_ASSOCIATE_TAG") or cfg.get("associate_tag", "")
    if not tag:
        return {"status": "skipped",
                "reason": "missing AMAZON_ASSOCIATE_TAG (env var or associate_tag in "
                          f"data/{STOREFRONT_FILE})",
                "batch_size": 0, "posted": 0, "blocked": 0, "failed": 0, "results": []}

    max_pins = max_pins or int(os.environ.get("AP_MAX_PINS_PER_RUN", "5"))
    batch = curate_batch(max_pins)
    pins_log = storage.load(PINS_FILE, [])

    results = []
    for product in batch:
        link = build_affiliate_link(product["asin"], tag)
        copy = generate_pin_copy(product)
        pin = {
            "asin": product["asin"],
            "title": copy["title"],
            "description": copy["description"],
            "link": link,
            "image_url": _pick_image_url(product, pins_log),
            "board_category": product.get("category", "default"),
        }

        check = check_pin(pin, pins_log)
        if not check["ok"]:
            results.append({"asin": product["asin"], "status": "blocked",
                             "violations": check["violations"]})
            continue

        post_result = _post_pin(pin, dry_run=dry_run, board_override=board_override)
        record = {
            **pin,
            "image_hash": check["image_hash"],
            "status": post_result.get("status"),
            "pin_id": post_result.get("pin_id"),
            "error": post_result.get("error"),
            "dispatched_at": datetime.now().isoformat(),
            "dispatched_at_epoch": time.time(),
        }
        pins_log.append(record)
        # Save after every pin, not just at the end — the cadence guard in
        # compliance.check_pin() reads this file, and a batch of 5 posted in
        # one process needs each prior pin visible to the next check.
        storage.save(PINS_FILE, pins_log)

        results.append({"asin": product["asin"], "status": post_result.get("status"),
                         "pin_id": post_result.get("pin_id"), "error": post_result.get("error")})

    posted = sum(1 for r in results if r["status"] == "posted")
    blocked = sum(1 for r in results if r["status"] == "blocked")
    failed = sum(1 for r in results if r["status"] == "failed")

    metrics.record(AGENT_KEY, pins_posted=posted, pins_blocked=blocked, pins_failed=failed,
                    batch_size=len(batch))

    return {"batch_size": len(batch), "posted": posted, "blocked": blocked,
            "failed": failed, "results": results}


# ============================================================================
# PERFORMANCE TRACKING
# ============================================================================

def fetch_performance(days: int = 7) -> dict:
    """Pull Pinterest analytics for recently-posted pins and cache them.
    NOTE: Pinterest's analytics response shape has drifted before — if this
    starts returning all-zero metrics, check api.pinterest.com/v5 docs for
    the current metric_types/response schema before assuming the account has
    no traffic."""
    token = os.environ.get("PINTEREST_ACCESS_TOKEN")
    if not token:
        return {"status": "skipped", "reason": "missing PINTEREST_ACCESS_TOKEN"}

    pins_log = storage.load(PINS_FILE, [])
    posted = [p for p in pins_log if p.get("status") == "posted" and p.get("pin_id")]
    end = datetime.now().date()
    start = end - timedelta(days=days)

    perf = storage.load(PERFORMANCE_FILE, {})
    checked = 0
    for p in posted[-50:]:  # cap API calls per run
        pin_id = p["pin_id"]
        try:
            resp = requests.get(
                f"https://api.pinterest.com/v5/pins/{pin_id}/analytics",
                headers={"Authorization": f"Bearer {token}"},
                params={
                    "start_date": start.isoformat(),
                    "end_date": end.isoformat(),
                    "metric_types": "IMPRESSION,SAVE,PIN_CLICK,OUTBOUND_CLICK",
                },
                timeout=15,
            )
            checked += 1
            if resp.status_code == 200:
                perf[pin_id] = {"asin": p.get("asin"), "fetched_at": datetime.now().isoformat(),
                                 "data": resp.json()}
            else:
                perf.setdefault(pin_id, {})["error"] = f"HTTP {resp.status_code}"
        except Exception as e:
            perf.setdefault(pin_id, {})["error"] = str(e)

    storage.save(PERFORMANCE_FILE, perf)
    return {"status": "ok", "pins_checked": checked}


def _outbound_clicks(entry: dict) -> int:
    try:
        return entry["data"]["all"]["summary_metrics"].get("OUTBOUND_CLICK", 0)
    except Exception:
        return 0


def top_performers(n: int = 5) -> list:
    """Rank cached pins by outbound (affiliate) clicks — use this to decide
    which storefront collections to expand and which ASINs to retire."""
    perf = storage.load(PERFORMANCE_FILE, {})
    ranked = sorted(perf.items(), key=lambda kv: _outbound_clicks(kv[1]), reverse=True)
    return [{"pin_id": k, "asin": v.get("asin"), "outbound_clicks": _outbound_clicks(v)}
            for k, v in ranked[:n]]


def status() -> dict:
    cfg = _load_config()
    tag = os.environ.get("AMAZON_ASSOCIATE_TAG") or cfg.get("associate_tag", "")
    return {
        "associate_tag_set": bool(tag),
        "pinterest_token_set": bool(os.environ.get("PINTEREST_ACCESS_TOKEN")),
        "board_mapping_set": bool(os.environ.get("AP_BOARD_MAP") or os.environ.get("PINTEREST_BOARD_ID")),
        "products_in_storefront": len(cfg.get("products", [])),
        "active_products": len([p for p in cfg.get("products", []) if p.get("status", "active") == "active"]),
    }


def history(limit: int = 20) -> list:
    log = storage.load(PINS_FILE, [])
    return log[-limit:][::-1]
