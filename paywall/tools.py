"""
Paywall tools — shared by both SaaS and Outreach agents.
"""
from paywall.gate import (
    create_client_paywall,
    verify_payment,
    require_payment,
    list_pending_payments,
)

TOOLS = [
    {
        "name": "create_payment_link",
        "description": (
            "Generate a PayPal payment link or invoice for a client and put them in 'pending_payment' status. "
            "If PAYPAL_CLIENT_ID + PAYPAL_CLIENT_SECRET are set, creates a real PayPal invoice emailed to the client. "
            "If not, falls back to a PayPal.me link. "
            "Call this right after registering a new client."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "client_id":   {"type": "string", "description": "SAAS-XXXX or OAS-XXXX"},
                "use_invoice": {"type": "boolean", "description": "True = PayPal invoice (emailed), False = checkout URL", "default": True},
            },
            "required": ["client_id"],
        },
    },
    {
        "name": "verify_payment",
        "description": (
            "Check if a client has paid. If they paid via PayPal invoice, auto-confirms from PayPal. "
            "Also works as manual activation for Venmo/Zelle/cash payments. "
            "Flips client to 'active' once payment is confirmed."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "client_id": {"type": "string"},
            },
            "required": ["client_id"],
        },
    },
    {
        "name": "check_payment_status",
        "description": "Check whether a client is allowed to use the service (has paid). Returns allowed: true/false.",
        "input_schema": {
            "type": "object",
            "properties": {
                "client_id": {"type": "string"},
            },
            "required": ["client_id"],
        },
    },
    {
        "name": "list_pending_payments",
        "description": "List all clients across both SaaS and Outreach services who have not yet paid.",
        "input_schema": {"type": "object", "properties": {}},
    },
]

TOOL_FUNCTIONS = {
    "create_payment_link":   lambda client_id, use_invoice=True: create_client_paywall(client_id, use_invoice),
    "verify_payment":        verify_payment,
    "check_payment_status":  require_payment,
    "list_pending_payments": list_pending_payments,
}
