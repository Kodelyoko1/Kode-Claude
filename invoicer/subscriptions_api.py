"""
PayPal Subscriptions API wrapper.

Uses the v1/catalogs/products + v1/billing/plans + v1/billing/subscriptions
endpoints — which ARE granted to our live app (subscriptions scope, unlike
the invoicing scope which is not).

Flow for a recurring subscriber:
  1. ensure_product(agent, plan_key)  → product_id (created once, cached)
  2. ensure_plan(product_id, price_mo) → plan_id (created once per price, cached)
  3. create_subscription(plan_id, customer_email, customer_name)
     → returns (subscription_id, approval_url)
  4. We email the approval_url to the customer
  5. Customer clicks → approves in PayPal → PayPal starts auto-billing
     and POSTs webhooks (BILLING.SUBSCRIPTION.ACTIVATED, then
     PAYMENT.SALE.COMPLETED each cycle)

State (the PayPal-side catalog cache):
  data/invoicer_paypal_catalog.json
    {
      "products": {"<agent>:<plan_key>": "PROD-XXXX", ...},
      "plans":    {"<agent>:<plan_key>:<price>": "P-XXXX", ...}
    }

Why we cache:
  PayPal charges no fees for creating products/plans, but each takes
  a round-trip and POST. Caching keeps `run_cycle()` fast and avoids
  rate-limit risk.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

import requests

from paywall.paypal import _get_token

DATA_DIR    = Path(__file__).parent.parent / "data"
CATALOG     = DATA_DIR / "invoicer_paypal_catalog.json"

API_BASE_LIVE    = "https://api-m.paypal.com"
API_BASE_SANDBOX = "https://api-m.sandbox.paypal.com"

# PayPal's standard product categories — we use SOFTWARE for all SKUs.
PRODUCT_CATEGORY = "SOFTWARE"
PRODUCT_TYPE     = "SERVICE"


def _api_base() -> str:
    return (API_BASE_LIVE if os.environ.get("PAYPAL_MODE", "live") == "live"
            else API_BASE_SANDBOX)


def _headers(idempotency: str = "") -> dict:
    h = {"Authorization": f"Bearer {_get_token()}",
         "Content-Type":  "application/json",
         "Prefer":        "return=representation"}
    if idempotency:
        h["PayPal-Request-Id"] = idempotency
    return h


def _load_catalog() -> dict:
    if not CATALOG.exists():
        return {"products": {}, "plans": {}}
    try:
        d = json.loads(CATALOG.read_text())
        return {"products": d.get("products", {}), "plans": d.get("plans", {})}
    except (OSError, json.JSONDecodeError):
        return {"products": {}, "plans": {}}


def _save_catalog(d: dict) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".invoicer_paypal_catalog.", suffix=".tmp", dir=DATA_DIR)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(d, f, indent=2)
        os.replace(tmp, CATALOG)
    except Exception:
        try: os.unlink(tmp)
        except OSError: pass
        raise


# ─────────────────────────── Products ───────────────────────────

def ensure_product(agent: str, plan_key: str, label: str) -> str:
    """Idempotent: returns the PayPal product_id, creating it if absent."""
    catalog = _load_catalog()
    key = f"{agent}:{plan_key}"
    if key in catalog["products"]:
        return catalog["products"][key]

    body = {
        "name":        f"{agent}.{plan_key}"[:127],
        "description": label[:256],
        "type":        PRODUCT_TYPE,
        "category":    PRODUCT_CATEGORY,
        "image_url":   "https://paypal.me/OmniSales",  # placeholder
        "home_url":    "https://paypal.me/OmniSales",
    }
    r = requests.post(
        f"{_api_base()}/v1/catalogs/products",
        headers=_headers(idempotency=f"product-{key}-{datetime.now():%Y%m%d}"),
        json=body, timeout=15,
    )
    r.raise_for_status()
    product_id = r.json().get("id", "")
    if not product_id:
        raise RuntimeError(f"PayPal create product returned no id: {r.text[:200]}")
    catalog["products"][key] = product_id
    _save_catalog(catalog)
    return product_id


# ─────────────────────────── Plans ───────────────────────────

def ensure_plan(product_id: str, agent: str, plan_key: str,
                price_mo: float, label: str) -> str:
    """Idempotent: returns the PayPal plan_id (a billing plan, NOT a SKU).
    Plans are immutable in PayPal once active — if you need to change the
    price, you create a new plan and migrate subscribers."""
    catalog = _load_catalog()
    key = f"{agent}:{plan_key}:{price_mo:.2f}"
    if key in catalog["plans"]:
        return catalog["plans"][key]

    body = {
        "product_id":   product_id,
        "name":         f"{agent}.{plan_key} ${price_mo:.2f}/mo"[:127],
        "description":  label[:256],
        "billing_cycles": [{
            "frequency":    {"interval_unit": "MONTH", "interval_count": 1},
            "tenure_type":  "REGULAR",
            "sequence":     1,
            "total_cycles": 0,  # 0 = infinite, until cancelled
            "pricing_scheme": {
                "fixed_price": {"value": f"{price_mo:.2f}", "currency_code": "USD"}
            },
        }],
        "payment_preferences": {
            "auto_bill_outstanding":   True,
            "setup_fee":               {"value": "0", "currency_code": "USD"},
            "setup_fee_failure_action": "CONTINUE",
            "payment_failure_threshold": 2,  # PayPal cancels after 2 failed retries
        },
    }
    r = requests.post(
        f"{_api_base()}/v1/billing/plans",
        headers=_headers(idempotency=f"plan-{key}-{datetime.now():%Y%m%d}"),
        json=body, timeout=15,
    )
    r.raise_for_status()
    plan_id = r.json().get("id", "")
    if not plan_id:
        raise RuntimeError(f"PayPal create plan returned no id: {r.text[:200]}")

    # Plans are created in CREATED state. Activate so customers can subscribe.
    a = requests.post(
        f"{_api_base()}/v1/billing/plans/{plan_id}/activate",
        headers=_headers(), timeout=15,
    )
    if a.status_code not in (200, 204):
        # Not fatal — plan exists, just not active. Surface for ops to triage.
        pass

    catalog["plans"][key] = plan_id
    _save_catalog(catalog)
    return plan_id


# ─────────────────────────── Subscriptions ───────────────────────────

def create_subscription(plan_id: str, customer_email: str,
                        customer_name: str = "") -> dict:
    """Create a subscription record. Returns:
        {"id": "I-...", "status": "APPROVAL_PENDING",
         "approval_url": "https://www.paypal.com/webapps/billing/subscriptions?ba_token=..."}

    The customer must visit approval_url, log in to PayPal, and approve
    before PayPal will start billing them. Until they approve, status
    stays APPROVAL_PENDING.
    """
    name_parts = (customer_name or customer_email.split("@")[0]).strip().split(" ", 1)
    first = name_parts[0][:140]
    last  = name_parts[1] if len(name_parts) > 1 else "(customer)"
    body = {
        "plan_id": plan_id,
        "subscriber": {
            "name":          {"given_name": first, "surname": last[:140]},
            "email_address": customer_email,
        },
        "application_context": {
            "brand_name":           "Wholesale Omniverse LLC",
            "locale":               "en-US",
            "shipping_preference":  "NO_SHIPPING",
            "user_action":          "SUBSCRIBE_NOW",
            "payment_method": {
                "payer_selected": "PAYPAL",
                "payee_preferred": "IMMEDIATE_PAYMENT_REQUIRED",
            },
            "return_url": "https://paypal.me/OmniSales",
            "cancel_url": "https://paypal.me/OmniSales",
        },
    }
    r = requests.post(
        f"{_api_base()}/v1/billing/subscriptions",
        headers=_headers(idempotency=f"sub-{customer_email}-{plan_id}-{datetime.now():%Y%m%d%H}"),
        json=body, timeout=15,
    )
    r.raise_for_status()
    j = r.json()
    approval_url = ""
    for link in j.get("links", []):
        if link.get("rel") == "approve":
            approval_url = link.get("href", "")
            break
    return {
        "id":           j.get("id", ""),
        "status":       j.get("status", ""),
        "approval_url": approval_url,
    }


def get_subscription(subscription_id: str) -> dict:
    r = requests.get(
        f"{_api_base()}/v1/billing/subscriptions/{subscription_id}",
        headers=_headers(), timeout=12,
    )
    if r.status_code == 200:
        return r.json()
    return {"error": f"HTTP {r.status_code}: {r.text[:200]}"}


def cancel_subscription(subscription_id: str, reason: str = "Owner cancelled") -> dict:
    r = requests.post(
        f"{_api_base()}/v1/billing/subscriptions/{subscription_id}/cancel",
        headers=_headers(),
        json={"reason": reason[:127]}, timeout=12,
    )
    if r.status_code == 204:
        return {"ok": True}
    return {"ok": False, "error": f"HTTP {r.status_code}: {r.text[:200]}"}


# ─────────────────────────── Catalog inspection ───────────────────────────

def catalog_summary() -> dict:
    d = _load_catalog()
    return {"products_cached": len(d["products"]), "plans_cached": len(d["plans"])}


def list_remote_products(page_size: int = 20) -> list:
    """Read what's actually in PayPal (paginated)."""
    r = requests.get(
        f"{_api_base()}/v1/catalogs/products?page_size={page_size}",
        headers=_headers(), timeout=15,
    )
    if r.status_code != 200:
        return []
    return r.json().get("products", [])


def probe() -> dict:
    """One-call check that we can hit each Subscriptions endpoint."""
    try:
        token = _get_token()
    except Exception as e:
        return {"ok": False, "stage": "oauth", "error": str(e)[:200]}
    h = {"Authorization": f"Bearer {token}"}
    for label, url in [
        ("list_products", f"{_api_base()}/v1/catalogs/products?page_size=1"),
        ("list_plans",    f"{_api_base()}/v1/billing/plans?page_size=1"),
    ]:
        r = requests.get(url, headers=h, timeout=10)
        if r.status_code != 200:
            body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            return {"ok": False, "stage": label, "status_code": r.status_code,
                    "error": body.get("name", "?"), "message": body.get("message", "")}
    return {"ok": True, "detail": "Subscriptions API endpoints all reachable"}
