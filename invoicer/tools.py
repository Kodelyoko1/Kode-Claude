"""
Invoicer — autonomous PayPal invoice generator.

Walks every <agent>_subscribers.json / <agent>_clients.json across the
fleet, computes who's due to be invoiced this billing period, drafts a
PayPal invoice via the v2 Invoicing API, and (when INVOICER_LIVE=1)
sends it to the customer. Defaults to dry-run.

Billing logic:
  · Recurring plans (price_mo > 0): invoiced monthly on the calendar
    boundary of `activated_at` (e.g. activated Mar 14 → invoiced again
    Apr 14, May 14, etc.). One invoice per (email, plan) per month.
  · One-time plans (one_time > 0, price_mo == 0): invoiced exactly once,
    triggered by status="active" (post-activation). Owner flips to
    `fulfilled` after delivery — invoicer never re-bills.

State:
  data/invoicer_log.json   — rolling per-attempt outcome log
  data/invoicer_state.json — last_invoiced timestamp keyed by
                             "<agent>:<email>:<plan>"

Env:
  INVOICER_LIVE             default 0 — set to 1 to actually POST to PayPal
  INVOICER_MAX_PER_CYCLE    default 25 — cap invoices per run
  INVOICER_DRY_VERBOSE      default 1 — print what would be sent in dry-run
"""
from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from autonomous import mailer, billing  # noqa
from paywall import paypal as pp
from invoicer import subscriptions_api as subs_api

AGENT_KEY = "invoicer"
DATA_DIR   = Path(__file__).parent.parent / "data"
LOG        = DATA_DIR / "invoicer_log.json"
STATE      = DATA_DIR / "invoicer_state.json"

LIVE              = os.environ.get("INVOICER_LIVE", "0") == "1"
MAX_PER_CYCLE     = int(os.environ.get("INVOICER_MAX_PER_CYCLE", "25"))
DRY_VERBOSE       = os.environ.get("INVOICER_DRY_VERBOSE", "1") == "1"


# ─────────────────────────── Helpers ───────────────────────────

def _now() -> str:
    return datetime.now().isoformat()


def _load(p: Path, default):
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return default


def _save(p: Path, data) -> None:
    p.parent.mkdir(exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{p.name}.", suffix=".tmp", dir=p.parent)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, p)
    except Exception:
        try: os.unlink(tmp)
        except OSError: pass
        raise


def _append_log(entry: dict) -> None:
    log = _load(LOG, [])
    if not isinstance(log, list):
        log = []
    log.append(entry)
    if len(log) > 500:
        log = log[-500:]
    _save(LOG, log)


def _state_key(agent: str, email: str, plan: str) -> str:
    return f"{agent}:{email.lower()}:{plan}"


def _months_between(a: datetime, b: datetime) -> int:
    return (b.year - a.year) * 12 + (b.month - a.month)


# ─────────────────────────── Catalog enumeration ───────────────────────────

# Discover every agent that has a subscribers.py or clients.py module.
def _discover_billable_modules() -> list[tuple[str, str, Path, Path]]:
    """Return [(agent_name, module_name, file_path, subs_json_path), ...]."""
    root = Path(__file__).parent.parent
    out = []
    for sub in root.glob("*/subscribers.py"):
        agent = sub.parent.name
        # Find the SUBS path the module writes to
        text = sub.read_text()
        # crude: look for SUBS = DATA_DIR / "<prefix>_subscribers.json"
        import re
        m = re.search(r'SUBS\s*=\s*DATA_DIR\s*/\s*"([^"]+)"', text)
        if not m:
            m = re.search(r'SUBS_FILE\s*=\s*DATA_DIR\s*/\s*"([^"]+)"', text)
        if m:
            out.append((agent, f"{agent}.subscribers", sub, DATA_DIR / m.group(1)))
    for sub in root.glob("*/clients.py"):
        agent = sub.parent.name
        text = sub.read_text()
        import re
        m = re.search(r'CLIENT_FILE\s*=\s*DATA_DIR\s*/\s*"([^"]+)"', text)
        if m:
            out.append((agent, f"{agent}.clients", sub, DATA_DIR / m.group(1)))
    return out


def _load_plans(module_name: str) -> dict:
    try:
        mod = importlib.import_module(module_name)
        return getattr(mod, "PLANS", {})
    except Exception:
        return {}


# ─────────────────────────── Due-detection ───────────────────────────

def find_due_invoices() -> list[dict]:
    """Walk all subscriber files; emit one task per (sub, plan) that is
    due to be invoiced now."""
    state = _load(STATE, {})
    if not isinstance(state, dict):
        state = {}
    now = datetime.now()
    due: list[dict] = []

    for agent, mod_name, _src_path, subs_path in _discover_billable_modules():
        if not subs_path.exists():
            continue
        subs = _load(subs_path, [])
        if not isinstance(subs, list):
            continue
        plans = _load_plans(mod_name)
        for s in subs:
            status = s.get("status", "")
            if status != "active":
                continue
            email = (s.get("email") or s.get("contact_email") or "").strip().lower()
            if not email or "@" not in email:
                continue
            plan_key = s.get("plan", "")
            plan_info = plans.get(plan_key, {})
            price_mo = float(plan_info.get("price_mo", 0) or 0)
            one_time = float(plan_info.get("one_time", 0) or 0)
            label    = plan_info.get("label", plan_key)
            key = _state_key(agent, email, plan_key)
            last = state.get(key, "")
            try:
                last_dt = datetime.fromisoformat(last.split("+")[0]) if last else None
            except (ValueError, AttributeError):
                last_dt = None

            if price_mo > 0:
                # Monthly recurring — bill if no invoice this calendar month
                if last_dt is None or _months_between(last_dt, now) >= 1:
                    due.append({
                        "agent": agent, "email": email, "name": s.get("name", ""),
                        "plan": plan_key, "label": label, "amount": price_mo,
                        "cycle": "monthly",
                    })
            elif one_time > 0:
                # One-time — bill exactly once, ever
                if last_dt is None:
                    due.append({
                        "agent": agent, "email": email, "name": s.get("name", ""),
                        "plan": plan_key, "label": label, "amount": one_time,
                        "cycle": "one_time",
                    })
    return due


# ─────────────────────────── Invoice creation ───────────────────────────

def _next_invoice_number() -> str:
    """WO-YYYY-NNNN — increment from last logged invoice_number."""
    year = datetime.now().year
    log = _load(LOG, [])
    if not isinstance(log, list):
        log = []
    max_n = 0
    for r in log:
        inv = (r.get("invoice_number") or "")
        if inv.startswith(f"WO-{year}-"):
            try:
                n = int(inv.rsplit("-", 1)[-1])
                max_n = max(max_n, n)
            except (ValueError, IndexError):
                pass
    return f"WO-{year}-{max_n + 1:04d}"


def _draft_invoice_for(task: dict) -> dict:
    """Build the PayPal v2 invoice request body (and supplemental fields
    we use for the customer email + logging)."""
    invoice_number = _next_invoice_number()
    name_parts = (task.get("name") or task["email"].split("@")[0]).strip().split(" ", 1)
    first = name_parts[0]
    last  = name_parts[1] if len(name_parts) > 1 else "(customer)"
    amount = f"{task['amount']:.2f}"

    body = {
        "detail": {
            "invoice_number": invoice_number,
            "reference":      f"{task['agent']}:{task['plan']}",
            "currency_code":  "USD",
            "note":           task["label"],
            "terms_and_conditions": "Net 3. 1.5%/mo on balances past due (advance notice required).",
            "payment_term":   {"term_type": "NET_3"},
        },
        "invoicer": {
            "name":          {"given_name": "Tylumiere", "surname": "Wholesale Omniverse LLC"},
            "email_address": os.environ.get("PAYPAL_EMAIL", "wholesaleomniverse@gmail.com"),
            "website":       "https://paypal.me/OmniSales",
        },
        "primary_recipients": [{
            "billing_info": {
                "name":          {"given_name": first, "surname": last},
                "email_address": task["email"],
            },
        }],
        "items": [{
            "name":        task["label"][:60],
            "description": f"{task['agent']} — {task['cycle']} billing",
            "quantity":    "1",
            "unit_amount": {"currency_code": "USD", "value": amount},
        }],
        "configuration": {
            "partial_payment": {"allow_partial_payment": False},
            "allow_tip":       False,
            "tax_inclusive":   False,
        },
    }
    return {"invoice_number": invoice_number, "body": body, "task": task}


def _post_invoice(draft: dict) -> dict:
    """Two paths now:
      · Monthly recurring → PayPal Subscriptions API (works on this app).
        Creates Product+Plan (cached) then Subscription, returns the
        approval_url customer must visit.
      · One-time → uses paypal.me link as fallback since the Invoicing
        feature is not enabled on the app. Returns a paypal.me/<amount>
        URL the owner emails manually.
    Honours dry-run for both paths.
    """
    task = draft["task"]
    cycle = task["cycle"]
    if not LIVE:
        if cycle == "monthly":
            return {"ok": True, "dry_run": True, "kind": "subscription",
                    "approval_url": f"(dry-run; would create Subscription for ${task['amount']}/mo)"}
        return {"ok": True, "dry_run": True, "kind": "paypal_me",
                "payment_url": f"https://paypal.me/OmniSales/{task['amount']:.2f}"}

    # LIVE branch
    if cycle == "monthly":
        try:
            product_id = subs_api.ensure_product(
                task["agent"], task["plan"], task["label"])
            plan_id = subs_api.ensure_plan(
                product_id, task["agent"], task["plan"],
                task["amount"], task["label"])
            r = subs_api.create_subscription(
                plan_id, task["email"], task["name"])
            if not r.get("id"):
                return {"ok": False, "error": f"subscription create: {r}"}
            return {"ok": True, "kind": "subscription",
                    "subscription_id": r["id"], "status": r["status"],
                    "approval_url": r["approval_url"],
                    "product_id": product_id, "plan_id": plan_id}
        except Exception as e:
            return {"ok": False, "kind": "subscription",
                    "error": f"{type(e).__name__}: {str(e)[:200]}"}
    # One-time: paypal.me fallback (Invoicing scope not on this app yet)
    return {"ok": True, "kind": "paypal_me",
            "payment_url": f"https://paypal.me/OmniSales/{task['amount']:.2f}",
            "note": "Invoicing feature not enabled; falling back to paypal.me. Owner must email manually."}


def _mark_invoiced(task: dict) -> None:
    state = _load(STATE, {})
    if not isinstance(state, dict):
        state = {}
    state[_state_key(task["agent"], task["email"], task["plan"])] = _now()
    _save(STATE, state)


# ─────────────────────────── Cycle ───────────────────────────

def _email_customer(task: dict, result: dict, invoice_number: str) -> bool:
    """Send the customer either:
      · subscription approval URL (monthly) → they click → PayPal auto-bills, OR
      · paypal.me link (one-time)
    Returns True if mailer accepted, False otherwise."""
    if not LIVE:
        return False  # don't email during dry-run
    kind = result.get("kind", "")
    first = (task.get("name") or task["email"].split("@")[0]).split(" ", 1)[0]
    if kind == "subscription":
        body = (
            f"Hi {first},\n\n"
            f"Your {task['label']} subscription is ready to activate. PayPal will\n"
            f"bill ${task['amount']:.2f}/mo on the date you approve below.\n\n"
            f"Approve & start:\n"
            f"  {result['approval_url']}\n\n"
            f"Cancel anytime from your PayPal dashboard.\n\n"
            f"— Tylumiere, Wholesale Omniverse"
        )
        subject = f"Activate your {task['label']} subscription — {invoice_number}"
    elif kind == "paypal_me":
        body = (
            f"Hi {first},\n\n"
            f"Your {task['label']} order is ready. Pay via PayPal:\n\n"
            f"  {result['payment_url']}\n\n"
            f"Reference {invoice_number} in the note. I'll deliver within 24h of payment confirmation.\n\n"
            f"— Tylumiere, Wholesale Omniverse"
        )
        subject = f"Payment link for {task['label']} — {invoice_number}"
    else:
        return False
    r = mailer.send(AGENT_KEY, task["email"], subject, body, purpose="outreach")
    return r.get("status") == "sent"


def run_cycle() -> dict:
    due = find_due_invoices()
    capped = due[:MAX_PER_CYCLE]
    sent = 0
    failed = 0
    emailed = 0
    drafts: list[dict] = []

    for task in capped:
        draft = _draft_invoice_for(task)
        result = _post_invoice(draft)
        entry = {
            "ts": _now(),
            "invoice_number": draft["invoice_number"],
            "agent": task["agent"], "email": task["email"], "plan": task["plan"],
            "amount": task["amount"], "cycle": task["cycle"],
            "live": LIVE,
            **result,
        }
        _append_log(entry)
        if result.get("ok"):
            sent += 1
            _mark_invoiced(task)
            drafts.append(entry)
            if not LIVE and DRY_VERBOSE:
                kind = result.get("kind", "?")
                print(f"  [DRY] {draft['invoice_number']}  {task['agent']:<18s}  "
                      f"{task['plan']:<22s}  ${task['amount']:>7.2f}  ({kind})  → {task['email']}")
            else:
                if _email_customer(task, result, draft["invoice_number"]):
                    emailed += 1
                    print(f"  [✓] {draft['invoice_number']}  {task['agent']:<18s}  "
                          f"{task['plan']:<22s}  ${task['amount']:>7.2f}  "
                          f"({result.get('kind','?')})  → {task['email']}")
        else:
            failed += 1
            print(f"  [FAIL] {draft['invoice_number']}  {task['agent']}  "
                  f"{task['email']}  — {result.get('error','?')[:120]}")
    return {
        "due_found":      len(due),
        "due_capped":     len(capped),
        "sent":           sent,
        "emailed":        emailed,
        "failed":         failed,
        "live":           LIVE,
        "drafts":         drafts,
    }


def run_full_cycle() -> dict:
    """Compatibility shim for cron-style invocation."""
    return run_cycle()
