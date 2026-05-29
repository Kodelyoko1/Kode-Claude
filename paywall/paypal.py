"""
PayPal REST API integration.
Uses PAYPAL_CLIENT_ID + PAYPAL_CLIENT_SECRET from env.
Set PAYPAL_MODE=live for production (default: sandbox for testing).
"""
import os
import json
import requests
from datetime import datetime, timedelta

PAYPAL_MODE = os.environ.get("PAYPAL_MODE", "sandbox")
PAYPAL_BASE = (
    "https://api-m.paypal.com"
    if PAYPAL_MODE == "live"
    else "https://api-m.sandbox.paypal.com"
)

COMPANY_NAME = "Wholesale Omniverse LLC"
COMPANY_EMAIL = os.environ.get("PAYPAL_EMAIL", "info@wholesaleomniverse.com")


def _get_token() -> str:
    """Get a PayPal OAuth2 access token."""
    client_id = os.environ.get("PAYPAL_CLIENT_ID", "")
    client_secret = os.environ.get("PAYPAL_CLIENT_SECRET", "")

    if not client_id or not client_secret:
        raise ValueError(
            "PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET must be set.\n"
            "Get them at: https://developer.paypal.com/dashboard/ → My Apps & Credentials"
        )

    resp = requests.post(
        f"{PAYPAL_BASE}/v1/oauth2/token",
        auth=(client_id, client_secret),
        data={"grant_type": "client_credentials"},
        headers={"Accept": "application/json"},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {_get_token()}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


def create_invoice(
    client_name: str,
    client_email: str,
    amount: float,
    service_description: str,
    due_days: int = 3,
) -> dict:
    """
    Create and draft a PayPal invoice for a client.
    Returns invoice_id and a hosted payment URL the client can pay at.
    """
    due_date = (datetime.now() + timedelta(days=due_days)).strftime("%Y-%m-%d")
    invoice_number = f"WO-{datetime.now().strftime('%Y%m%d%H%M%S')}"

    payload = {
        "detail": {
            "invoice_number": invoice_number,
            "invoice_date": datetime.now().strftime("%Y-%m-%d"),
            "payment_term": {
                "term_type": "DUE_ON_DATE_SPECIFIED",
                "due_date": due_date,
            },
            "currency_code": "USD",
            "note": "Thank you for your business. Pay via the link below.",
            "memo": f"Wholesale Omniverse — {service_description}",
        },
        "invoicer": {
            "name": {"business_name": COMPANY_NAME},
            "email_address": COMPANY_EMAIL,
            "phones": [{"country_code": "1", "national_number": "2073854041", "phone_type": "MOBILE"}],
            "website": "https://wholesaleomniverse.com",
        },
        "primary_recipients": [
            {
                "billing_info": {
                    "name": {"full_name": client_name},
                    "email_address": client_email,
                }
            }
        ],
        "items": [
            {
                "name": service_description,
                "description": f"Monthly subscription — {COMPANY_NAME}",
                "quantity": "1",
                "unit_amount": {"currency_code": "USD", "value": f"{amount:.2f}"},
                "unit_of_measure": "QUANTITY",
            }
        ],
        "configuration": {
            "partial_payment": {"allow_partial_payment": False},
            "allow_tips": False,
            "tax_calculated_after_discount": False,
            "tax_inclusive": False,
        },
    }

    resp = requests.post(
        f"{PAYPAL_BASE}/v2/invoicing/invoices",
        headers=_headers(),
        json=payload,
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    invoice_id = data.get("id", "")

    # Extract the hosted payment link
    payment_url = next(
        (link["href"] for link in data.get("links", []) if link.get("rel") == "payer-view"),
        f"https://www.paypal.com/invoice/p/#{invoice_id}",
    )

    return {
        "invoice_id": invoice_id,
        "invoice_number": invoice_number,
        "payment_url": payment_url,
        "amount": amount,
        "due_date": due_date,
        "status": "DRAFT",
    }


def send_invoice(invoice_id: str, send_to_invoicer: bool = False) -> dict:
    """Send a drafted invoice to the client via PayPal email."""
    resp = requests.post(
        f"{PAYPAL_BASE}/v2/invoicing/invoices/{invoice_id}/send",
        headers=_headers(),
        json={"send_to_invoicer": send_to_invoicer},
        timeout=15,
    )
    resp.raise_for_status()
    return {"status": "sent", "invoice_id": invoice_id}


def get_invoice_status(invoice_id: str) -> dict:
    """Check if an invoice has been paid. Returns status: DRAFT, SENT, PAID, CANCELLED, etc."""
    resp = requests.get(
        f"{PAYPAL_BASE}/v2/invoicing/invoices/{invoice_id}",
        headers=_headers(),
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    status = data.get("status", "UNKNOWN")
    amount = data.get("amount", {}).get("value", "0")
    due = data.get("detail", {}).get("payment_term", {}).get("due_date", "")

    payments = data.get("payments", {}).get("transactions", [])
    paid_at = payments[0].get("payment_date", "") if payments else ""

    return {
        "invoice_id": invoice_id,
        "status": status,
        "is_paid": status == "PAID",
        "amount": amount,
        "due_date": due,
        "paid_at": paid_at,
    }


def create_payment_link(
    client_name: str,
    client_email: str,
    amount: float,
    description: str,
    return_url: str = "https://wholesaleomniverse.com/thank-you",
    cancel_url: str = "https://wholesaleomniverse.com/payment-cancelled",
) -> dict:
    """
    Create a one-time PayPal Checkout order and return the approval URL.
    The client clicks the URL, logs into PayPal, and pays.
    """
    payload = {
        "intent": "CAPTURE",
        "purchase_units": [
            {
                "amount": {"currency_code": "USD", "value": f"{amount:.2f}"},
                "description": description,
                "custom_id": client_email,
                "soft_descriptor": "WholesaleOmniverse",
            }
        ],
        "application_context": {
            "brand_name": COMPANY_NAME,
            "landing_page": "BILLING",
            "user_action": "PAY_NOW",
            "return_url": return_url,
            "cancel_url": cancel_url,
        },
    }

    resp = requests.post(
        f"{PAYPAL_BASE}/v2/checkout/orders",
        headers=_headers(),
        json=payload,
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    order_id = data.get("id", "")
    approval_url = next(
        (link["href"] for link in data.get("links", []) if link.get("rel") == "approve"),
        "",
    )

    return {
        "order_id": order_id,
        "payment_url": approval_url,
        "amount": amount,
        "status": data.get("status", "CREATED"),
    }


def paypalme_link(amount: float, description: str = "") -> str:
    """
    Generate a PayPal.me link (no API key needed).
    Set PAYPAL_ME_USERNAME in env (e.g. 'wholesaleomniverse').
    """
    username = os.environ.get("PAYPAL_ME_USERNAME", "wholesaleomniverse")
    link = f"https://paypal.me/{username}/{amount:.2f}"
    return link
