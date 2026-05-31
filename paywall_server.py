#!/usr/bin/env python3
"""
PayPal webhook receiver — auto-activates clients when PayPal confirms payment.

Run this on a server with a public URL, then set your PayPal webhook URL to:
  http://YOUR_SERVER_IP:5055/paypal/webhook

In PayPal Developer Dashboard → Webhooks → Add Webhook:
  Event: INVOICING.INVOICE.PAID
  URL:   http://YOUR_SERVER_IP:5055/paypal/webhook

For local testing (no public server), use ngrok:
  ngrok http 5055
  Then set the ngrok URL as your PayPal webhook.
"""
import json
import os
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

from flask import Flask, request, jsonify
from paywall.gate import _load, _save, _mark_paid, list_pending_payments
from paywall.paypal import get_invoice_status

app = Flask(__name__)

DATA_DIR = Path(__file__).parent / "data"
SAAS_FILE = DATA_DIR / "saas_clients.json"
OAS_FILE  = DATA_DIR / "outreach_clients.json"

WEBHOOK_SECRET = os.environ.get("PAYPAL_WEBHOOK_SECRET", "")


def _find_client_by_invoice(invoice_id: str):
    """Find a client record by their PayPal invoice ID."""
    for file_path in [SAAS_FILE, OAS_FILE]:
        clients = _load(file_path)
        for client_id, c in clients.items():
            if c.get("paypal_invoice_id") == invoice_id:
                return client_id, c, clients, file_path
    return None, None, None, None


def _find_client_by_email(payer_email: str):
    """Find a client by payer email (used for checkout order payments)."""
    for file_path in [SAAS_FILE, OAS_FILE]:
        clients = _load(file_path)
        for client_id, c in clients.items():
            if c.get("email", "").lower() == payer_email.lower():
                return client_id, c, clients, file_path
    return None, None, None, None


@app.route("/paypal/webhook", methods=["POST"])
def paypal_webhook():
    """Receive PayPal webhook events and auto-activate clients on payment."""
    try:
        event = request.get_json(force=True)
    except Exception:
        return jsonify({"error": "Invalid JSON"}), 400

    event_type = event.get("event_type", "")
    resource   = event.get("resource", {})

    print(f"[Webhook] Event: {event_type}")

    # ── INVOICE PAID ──────────────────────────────────────────────────────────
    if event_type == "INVOICING.INVOICE.PAID":
        invoice_id = resource.get("id", "")
        payer_info  = resource.get("payments", {}).get("transactions", [{}])[0]
        paid_amount = payer_info.get("payment_details", {}).get("amount", {}).get("value", "?")

        client_id, client, all_clients, file_path = _find_client_by_invoice(invoice_id)

        if client_id:
            _mark_paid(client_id, invoice_id)
            print(f"[Webhook] Activated client {client_id} ({client.get('name')}) — ${paid_amount} received")
            return jsonify({"status": "activated", "client_id": client_id}), 200
        else:
            print(f"[Webhook] Invoice {invoice_id} paid but no matching client found")
            return jsonify({"status": "no_client_found", "invoice_id": invoice_id}), 200

    # ── CHECKOUT ORDER COMPLETED ──────────────────────────────────────────────
    elif event_type in ("CHECKOUT.ORDER.APPROVED", "PAYMENT.CAPTURE.COMPLETED"):
        payer_email = (
            resource.get("payer", {}).get("email_address", "") or
            resource.get("payment_source", {}).get("paypal", {}).get("email_address", "")
        )
        custom_id = ""
        for unit in resource.get("purchase_units", []):
            custom_id = unit.get("custom_id", "")
            if custom_id:
                break

        # Try custom_id (we store client email there) or payer email
        lookup_email = custom_id or payer_email
        client_id, client, all_clients, file_path = _find_client_by_email(lookup_email)

        if client_id:
            _mark_paid(client_id)
            print(f"[Webhook] Activated client {client_id} ({client.get('name')}) via checkout")
            return jsonify({"status": "activated", "client_id": client_id}), 200
        else:
            print(f"[Webhook] Checkout paid by {lookup_email} but no matching client found")
            return jsonify({"status": "no_client_found", "payer": lookup_email}), 200

    # ── OTHER EVENTS ──────────────────────────────────────────────────────────
    else:
        print(f"[Webhook] Unhandled event type: {event_type}")
        return jsonify({"status": "ignored", "event_type": event_type}), 200


@app.route("/paywall/status", methods=["GET"])
def paywall_status():
    """Health check + pending payments overview."""
    pending = list_pending_payments()
    return jsonify({
        "status": "running",
        "paypal_mode": os.environ.get("PAYPAL_MODE", "sandbox"),
        "pending_payments": pending["count"],
        "clients_pending": [
            {"id": p["client_id"], "name": p["name"], "amount": p["amount_due"]}
            for p in pending["pending"]
        ],
    })


@app.route("/paywall/activate/<client_id>", methods=["POST"])
def manual_activate(client_id):
    """Manually activate a client (for Venmo/Zelle/cash payments)."""
    from paywall.gate import verify_payment
    result = verify_payment(client_id)
    return jsonify(result)


LEADS_FILE = DATA_DIR / "leads.json"


def _load_leads():
    if LEADS_FILE.exists():
        with open(LEADS_FILE) as f:
            return json.load(f)
    return {}


def _save_leads(leads):
    DATA_DIR.mkdir(exist_ok=True)
    with open(LEADS_FILE, "w") as f:
        json.dump(leads, f, indent=2)


@app.route("/leads", methods=["POST", "OPTIONS"])
def submit_lead():
    """
    Capture a motivated seller lead from the website intake form.
    Writes a new entry into data/leads.json, where the follow-up agent will pick it up.
    """
    # CORS preflight
    if request.method == "OPTIONS":
        return ("", 204, {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Content-Type",
            "Access-Control-Allow-Methods": "POST, OPTIONS",
        })

    try:
        data = request.get_json(force=True, silent=True) or request.form.to_dict()
    except Exception:
        return jsonify({"error": "Bad JSON"}), 400

    address = (data.get("address") or "").strip()
    phone   = (data.get("seller_phone") or "").strip()
    if not address or not phone:
        return jsonify({"error": "Address and phone are required."}), 400

    leads = _load_leads()
    import datetime
    n = len(leads) + 1
    while f"LEAD-{n:04d}" in leads:
        n += 1
    lead_id = f"LEAD-{n:04d}"

    now = datetime.datetime.now().isoformat()
    leads[lead_id] = {
        "lead_id":          lead_id,
        "address":          address,
        "city":             (data.get("city") or "").strip(),
        "state":            (data.get("state") or "").strip().upper(),
        "zip":              (data.get("zip") or "").strip(),
        "seller_name":      (data.get("seller_name") or "").strip(),
        "seller_phone":     phone,
        "seller_email":     (data.get("seller_email") or "").strip(),
        "asking_price":     0,
        "estimated_arv":    0,
        "estimated_repairs":0,
        "estimated_mao":    0,
        "lead_source":      "Website — wholesaleomniverse.com",
        "motivation":       (data.get("reason") or "").strip() or
                            f"Timeline: {data.get('timeline','—')}, Condition: {data.get('condition','—')}",
        "status":           "new",
        "notes":            f"Submitted via web form on {now}",
        "created_at":       now,
        "updated_at":       now,
        "submitted_via":    "web_form",
        "timeline":         data.get("timeline", ""),
        "condition":        data.get("condition", ""),
        "ip":               request.headers.get("X-Forwarded-For", request.remote_addr or ""),
        "user_agent":       request.headers.get("User-Agent", ""),
    }
    _save_leads(leads)

    # Notify owner immediately so they can call within 24h
    try:
        from email_template import send_branded_email
        addr_line = f"{address}, {leads[lead_id]['city']} {leads[lead_id]['state']}"
        body_text = (
            f"NEW seller lead from the website!\n\n"
            f"  Lead ID:  {lead_id}\n"
            f"  Address:  {addr_line}\n"
            f"  Name:     {leads[lead_id]['seller_name']}\n"
            f"  Phone:    {phone}\n"
            f"  Email:    {leads[lead_id]['seller_email']}\n"
            f"  Timeline: {data.get('timeline', '—')}\n"
            f"  Condition:{data.get('condition', '—')}\n"
            f"  Reason:   {data.get('reason', '—')}\n\n"
            f"Call or text the seller within 24h."
        )
        body_html = (
            f"<p><strong>NEW seller lead from the website!</strong></p>"
            f"<ul>"
            f"<li><strong>Lead ID:</strong> {lead_id}</li>"
            f"<li><strong>Address:</strong> {addr_line}</li>"
            f"<li><strong>Name:</strong> {leads[lead_id]['seller_name']}</li>"
            f"<li><strong>Phone:</strong> <a href=\"tel:{phone}\">{phone}</a></li>"
            f"<li><strong>Email:</strong> {leads[lead_id]['seller_email']}</li>"
            f"<li><strong>Timeline:</strong> {data.get('timeline','—')}</li>"
            f"<li><strong>Condition:</strong> {data.get('condition','—')}</li>"
            f"<li><strong>Reason:</strong> {data.get('reason','—')}</li>"
            f"</ul>"
            f"<p>Call or text the seller within 24h.</p>"
        )
        notify_to = os.environ.get("DIGEST_EMAIL") or os.environ.get("SMTP_USER", "")
        if notify_to:
            send_branded_email(
                to_email=notify_to,
                subject=f"NEW seller lead — {addr_line}",
                body_text=body_text,
                body_html_inner=body_html,
            )
    except Exception as e:
        print(f"[/leads] notify failed: {e}")

    return jsonify({
        "status": "ok",
        "lead_id": lead_id,
        "message": "Thanks. We received your info and will reach out within 24 hours.",
    }), 200, {"Access-Control-Allow-Origin": "*"}


@app.route("/", methods=["GET"])
def root():
    return jsonify({"status": "ok", "service": "Wholesale Omniverse paywall + lead capture"})


if __name__ == "__main__":
    port = int(os.environ.get("PAYWALL_PORT", 5055))
    print(f"""
╔══════════════════════════════════════════════════════╗
║   Wholesale Omniverse — PayPal Webhook Server        ║
║                                                      ║
║   Listening on http://0.0.0.0:{port}               ║
║                                                      ║
║   PayPal Webhook URL:                                ║
║   http://YOUR_SERVER_IP:{port}/paypal/webhook      ║
║                                                      ║
║   For local testing:                                 ║
║   ngrok http {port}                                 ║
║   Then set ngrok URL in PayPal Developer Dashboard   ║
╚══════════════════════════════════════════════════════╝
""")
    app.run(host="0.0.0.0", port=port, debug=False)
