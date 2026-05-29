"""
Autonomous billing helper for all agents.
Generates PayPal payment links/invoices and tracks payment state in JSON.
Reuses existing paywall infrastructure where possible.
"""
import json
import os
import uuid
from datetime import datetime, timedelta
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
SUBS_FILE = DATA_DIR / "agent_subscriptions.json"
INVOICES_FILE = DATA_DIR / "agent_invoices.json"


def _load(path: Path, default):
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return default


def _save(path: Path, data):
    DATA_DIR.mkdir(exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def paypal_link(amount: float, note: str = "") -> str:
    """Return a paypal.me link for a one-off charge (no API required)."""
    username = os.environ.get("PAYPAL_ME_USERNAME", "wholesaleomniverse")
    return f"https://paypal.me/{username}/{amount:.0f}"


def issue_invoice(
    agent_key: str,
    client_name: str,
    client_email: str,
    amount: float,
    cadence: str = "monthly",
    description: str = "",
) -> dict:
    """
    Issue an invoice for a client. No external API call — just a paypal.me link
    + record stored locally. Owner is notified to confirm payment manually.
    """
    invoice_id = f"INV-{agent_key.upper()[:4]}-{uuid.uuid4().hex[:8].upper()}"
    link = paypal_link(amount)
    record = {
        "invoice_id":  invoice_id,
        "agent":       agent_key,
        "client_name": client_name,
        "client_email": client_email,
        "amount":      amount,
        "cadence":     cadence,
        "description": description,
        "payment_url": link,
        "status":      "issued",
        "issued_at":   datetime.now().isoformat(),
        "paid_at":     "",
        "next_due":    (datetime.now() + timedelta(days=30)).isoformat()
                       if cadence == "monthly" else "",
    }
    inv = _load(INVOICES_FILE, [])
    inv.append(record)
    _save(INVOICES_FILE, inv)
    return record


def mark_paid(invoice_id: str) -> dict:
    inv = _load(INVOICES_FILE, [])
    for r in inv:
        if r["invoice_id"] == invoice_id:
            r["status"] = "paid"
            r["paid_at"] = datetime.now().isoformat()
            _save(INVOICES_FILE, inv)
            return r
    return {"error": f"invoice {invoice_id} not found"}


def list_invoices(agent_key: str = "", status: str = "") -> list:
    inv = _load(INVOICES_FILE, [])
    out = inv
    if agent_key:
        out = [r for r in out if r.get("agent") == agent_key]
    if status:
        out = [r for r in out if r.get("status") == status]
    return out


def revenue_summary(agent_key: str = "") -> dict:
    """Total paid revenue + MRR for an agent (or all)."""
    inv = list_invoices(agent_key=agent_key, status="paid")
    total_paid = sum(r.get("amount", 0) for r in inv)
    monthly_paid = [
        r for r in inv
        if r.get("cadence") == "monthly"
        and r.get("paid_at", "")[:7] == datetime.now().isoformat()[:7]
    ]
    mrr = sum(r.get("amount", 0) for r in inv if r.get("cadence") == "monthly")
    return {
        "total_paid": total_paid,
        "mrr": mrr,
        "this_month_paid": sum(r.get("amount", 0) for r in monthly_paid),
        "paid_count": len(inv),
    }


def overdue_invoices(agent_key: str = "") -> list:
    inv = list_invoices(agent_key=agent_key, status="issued")
    now = datetime.now()
    out = []
    for r in inv:
        try:
            issued = datetime.fromisoformat(r["issued_at"])
            if (now - issued).days > 7:
                out.append(r)
        except Exception:
            continue
    return out
