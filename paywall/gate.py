"""
Paywall gate — checks payment status before allowing access.
Works for both SaaS clients (saas_clients.json) and Outreach clients (outreach_clients.json).
"""
import json
import os
from pathlib import Path
from paywall.paypal import (
    create_invoice, send_invoice, get_invoice_status,
    create_payment_link, paypalme_link,
)

DATA_DIR = Path(__file__).parent.parent / "data"
SAAS_CLIENTS_FILE   = DATA_DIR / "saas_clients.json"
OAS_CLIENTS_FILE    = DATA_DIR / "outreach_clients.json"

PAYWALL_BLOCKED_STATUSES = {"pending_payment", "payment_failed", "inactive"}


def _load(path):
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return {}


def _save(path, data):
    DATA_DIR.mkdir(exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def _get_client_and_file(client_id: str):
    """Return (client_dict, all_clients_dict, file_path) for any client ID."""
    if client_id.startswith("SAAS-"):
        all_clients = _load(SAAS_CLIENTS_FILE)
        return all_clients.get(client_id), all_clients, SAAS_CLIENTS_FILE
    if client_id.startswith("OAS-"):
        all_clients = _load(OAS_CLIENTS_FILE)
        return all_clients.get(client_id), all_clients, OAS_CLIENTS_FILE
    return None, {}, None


def is_paid(client_id: str) -> bool:
    """Return True if the client has an active paid subscription."""
    client, _, _ = _get_client_and_file(client_id)
    if not client:
        return False
    return client.get("status") == "active" and client.get("payment_verified", False)


def require_payment(client_id: str) -> dict:
    """
    Check if a client has paid. Returns:
      {"allowed": True}  — if paid and active
      {"allowed": False, "reason": "...", "payment_url": "..."}  — if not paid
    """
    client, _, _ = _get_client_and_file(client_id)
    if not client:
        return {"allowed": False, "reason": "Client not found."}

    status = client.get("status", "")
    if status == "active" and client.get("payment_verified", False):
        return {"allowed": True}

    if status in PAYWALL_BLOCKED_STATUSES or not client.get("payment_verified", False):
        payment_url = client.get("payment_url", "")
        invoice_id = client.get("paypal_invoice_id", "")

        # Auto-check PayPal if we have an invoice ID
        if invoice_id:
            try:
                invoice_status = get_invoice_status(invoice_id)
                if invoice_status.get("is_paid"):
                    _mark_paid(client_id, invoice_id)
                    return {"allowed": True}
            except Exception:
                pass

        return {
            "allowed": False,
            "reason": f"Payment required. Status: {status}",
            "payment_url": payment_url or "Run create_client_paywall() to generate a payment link.",
            "client_name": client.get("name", ""),
            "amount_due": client.get("monthly_fee", 0),
        }

    return {"allowed": True}


def _mark_paid(client_id: str, invoice_id: str = ""):
    """Flip client to active + payment_verified after confirmed payment."""
    client, all_clients, file_path = _get_client_and_file(client_id)
    if not client or not file_path:
        return
    all_clients[client_id]["status"] = "active"
    all_clients[client_id]["payment_verified"] = True
    if invoice_id:
        all_clients[client_id]["paypal_invoice_id"] = invoice_id
    _save(file_path, all_clients)


def create_client_paywall(client_id: str, use_invoice: bool = True) -> dict:
    """
    Generate a PayPal payment link or invoice for a client and store it.
    use_invoice=True  → PayPal invoice (client gets email from PayPal with payment link)
    use_invoice=False → PayPal Checkout order link (direct URL, no PayPal email sent)
    Falls back to PayPal.me link if API credentials are not set.
    """
    client, all_clients, file_path = _get_client_and_file(client_id)
    if not client:
        return {"error": f"Client {client_id} not found."}

    name    = client.get("name", "Client")
    email   = client.get("email", "")
    amount  = client.get("monthly_fee", 97)
    plan    = client.get("plan", client.get("tier", "basic"))
    desc    = f"Wholesale Omniverse — {plan.title()} Plan (Monthly)"

    # Check if PayPal API credentials are available
    has_api_creds = bool(
        os.environ.get("PAYPAL_CLIENT_ID") and
        os.environ.get("PAYPAL_CLIENT_SECRET")
    )

    if not has_api_creds:
        # Fallback: PayPal.me link (no API needed)
        payment_url = paypalme_link(amount, desc)
        all_clients[client_id]["payment_url"] = payment_url
        all_clients[client_id]["status"] = "pending_payment"
        all_clients[client_id]["payment_verified"] = False
        _save(file_path, all_clients)
        return {
            "method": "paypal_me",
            "payment_url": payment_url,
            "amount": amount,
            "note": "Share this link with the client. Once paid, manually call verify_payment() to activate.",
            "activate_command": f'verify_payment("{client_id}")',
        }

    try:
        if use_invoice:
            # Create + send PayPal invoice
            invoice = create_invoice(name, email, amount, desc)
            send_invoice(invoice["invoice_id"])
            payment_url = invoice["payment_url"]
            invoice_id  = invoice["invoice_id"]

            all_clients[client_id]["paypal_invoice_id"] = invoice_id
            all_clients[client_id]["payment_url"] = payment_url
            all_clients[client_id]["status"] = "pending_payment"
            all_clients[client_id]["payment_verified"] = False
            _save(file_path, all_clients)

            return {
                "method": "paypal_invoice",
                "invoice_id": invoice_id,
                "invoice_number": invoice["invoice_number"],
                "payment_url": payment_url,
                "amount": amount,
                "due_date": invoice["due_date"],
                "note": f"Invoice emailed to {email} via PayPal. Client pays the link. Call verify_payment() to check.",
            }
        else:
            # One-time checkout link
            order = create_payment_link(name, email, amount, desc)
            payment_url = order["payment_url"]
            order_id    = order["order_id"]

            all_clients[client_id]["paypal_order_id"] = order_id
            all_clients[client_id]["payment_url"] = payment_url
            all_clients[client_id]["status"] = "pending_payment"
            all_clients[client_id]["payment_verified"] = False
            _save(file_path, all_clients)

            return {
                "method": "paypal_checkout",
                "order_id": order_id,
                "payment_url": payment_url,
                "amount": amount,
                "note": f"Share this checkout URL with {name}. Call verify_payment() after they pay.",
            }

    except Exception as e:
        # Fallback on any API error
        payment_url = paypalme_link(amount, desc)
        all_clients[client_id]["payment_url"] = payment_url
        all_clients[client_id]["status"] = "pending_payment"
        all_clients[client_id]["payment_verified"] = False
        _save(file_path, all_clients)
        return {
            "method": "paypal_me_fallback",
            "payment_url": payment_url,
            "amount": amount,
            "error": str(e),
            "note": "PayPal API error — fell back to PayPal.me link. Share with client manually.",
        }


def verify_payment(client_id: str) -> dict:
    """
    Check PayPal for payment confirmation and activate the client if paid.
    Can also be called manually to activate after cash/Venmo/Zelle payment.
    """
    client, all_clients, file_path = _get_client_and_file(client_id)
    if not client:
        return {"error": f"Client {client_id} not found."}

    invoice_id = client.get("paypal_invoice_id", "")

    # Try PayPal invoice check
    if invoice_id:
        try:
            status = get_invoice_status(invoice_id)
            if status.get("is_paid"):
                _mark_paid(client_id, invoice_id)
                return {
                    "status": "PAID",
                    "client": client.get("name"),
                    "activated": True,
                    "paid_at": status.get("paid_at", ""),
                    "amount": status.get("amount"),
                }
            else:
                return {
                    "status": status.get("status"),
                    "client": client.get("name"),
                    "activated": False,
                    "payment_url": client.get("payment_url", ""),
                    "message": f"Not yet paid. Invoice status: {status['status']}",
                }
        except Exception as e:
            pass  # Fall through to manual activation below

    # Manual activation (for Venmo/Zelle/cash payments without PayPal API)
    _mark_paid(client_id)
    return {
        "status": "manually_activated",
        "client": client.get("name"),
        "activated": True,
        "message": "Client activated manually. No PayPal invoice found — marked as paid.",
    }


def list_pending_payments() -> dict:
    """List all clients across both services who haven't paid yet."""
    pending = []
    for file_path, prefix in [(SAAS_CLIENTS_FILE, "SaaS"), (OAS_CLIENTS_FILE, "Outreach")]:
        clients = _load(file_path)
        for c in clients.values():
            if not c.get("payment_verified", False) or c.get("status") == "pending_payment":
                pending.append({
                    "client_id": c["client_id"],
                    "service": prefix,
                    "name": c.get("name"),
                    "email": c.get("email"),
                    "amount_due": c.get("monthly_fee"),
                    "payment_url": c.get("payment_url", "not generated"),
                    "status": c.get("status"),
                })
    return {"pending": pending, "count": len(pending)}
