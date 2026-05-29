#!/usr/bin/env python3
"""
Pulls form submissions from Netlify's API and writes them into data/leads.json
where the existing follow-up agent (run_followup_auto.py) will pick them up.

Run from cron every 15 min for fast pickup.
"""
import argparse
import datetime
import json
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

import requests
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from email_template import send_branded_email

console = Console()
DATA_DIR    = Path(__file__).parent / "data"
LEADS_FILE  = DATA_DIR / "leads.json"
SEEN_FILE   = DATA_DIR / "netlify_seen_submissions.json"
NETLIFY_CFG = Path.home() / ".config" / "netlify" / "config.json"

SITE_ID    = os.environ.get("NETLIFY_SITE_ID", "0bb864a7-ac52-4cac-8bf2-d979649dafc3")
API_BASE   = "https://api.netlify.com/api/v1"
BLOBS_BASE = "https://api.netlify.com/api/v1/blobs"
STORE_NAME = "seller-leads"


def _netlify_token() -> str:
    """Resolve auth token from .env first, then fall back to netlify CLI config."""
    tok = os.environ.get("NETLIFY_AUTH_TOKEN", "").strip()
    if tok:
        return tok
    if not NETLIFY_CFG.exists():
        return ""
    try:
        cfg = json.loads(NETLIFY_CFG.read_text())
        for user in cfg.get("users", {}).values():
            t = user.get("auth", {}).get("token", "")
            if t:
                return t
    except Exception:
        return ""
    return ""


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


def _fetch_submissions(token: str) -> list:
    """
    Combines two sources:
      1. Netlify Forms API (if native form detection ever picks up the form)
      2. Netlify Blobs store 'seller-leads' (written by the lead-capture function)
    """
    headers = {"Authorization": f"Bearer {token}"}
    submissions = []

    # Source 1: Netlify native Forms (may be empty if forms not detected)
    try:
        r = requests.get(
            f"{API_BASE}/sites/{SITE_ID}/submissions",
            headers=headers, params={"per_page": 100}, timeout=20,
        )
        if r.status_code == 200:
            submissions.extend(r.json())
    except Exception:
        pass

    # Source 2: Netlify Blobs store written by lead-capture function
    try:
        # List all blob keys in the store
        r = requests.get(
            f"{BLOBS_BASE}/{SITE_ID}/{STORE_NAME}",
            headers=headers, timeout=20,
        )
        if r.status_code == 200:
            for blob in r.json().get("blobs", []):
                key = blob.get("key") or blob.get("id")
                if not key:
                    continue
                # Fetch the actual submission JSON
                gr = requests.get(
                    f"{BLOBS_BASE}/{SITE_ID}/{STORE_NAME}/{key}",
                    headers=headers, timeout=15,
                )
                if gr.status_code == 200:
                    sub = gr.json()
                    # Normalize to look like a Forms-API submission
                    if "data" not in sub and "fields" in sub:
                        sub["data"] = sub["fields"]
                    if "id" not in sub:
                        sub["id"] = key
                    if "created_at" not in sub:
                        sub["created_at"] = sub.get("received_at", _now())
                    submissions.append(sub)
    except Exception as e:
        if not os.environ.get("QUIET"):
            print(f"[pull_netlify_leads] Blobs fetch failed: {e}")

    return submissions


def _submission_to_lead(sub: dict, lead_id: str) -> dict:
    """Map a Netlify submission's fields → our leads.json lead structure."""
    data = sub.get("data", {})
    address = (data.get("address") or "").strip()
    city    = (data.get("city") or "").strip()
    state   = (data.get("state") or "").strip().upper()
    return {
        "lead_id":              lead_id,
        "address":              address,
        "city":                 city,
        "state":                state,
        "zip":                  (data.get("zip") or "").strip(),
        "seller_name":          (data.get("seller_name") or "").strip(),
        "seller_phone":         (data.get("seller_phone") or "").strip(),
        "seller_email":         (data.get("seller_email") or "").strip(),
        "asking_price":         0,
        "estimated_arv":        0,
        "estimated_repairs":    0,
        "estimated_mao":        0,
        "lead_source":          "Website — wholesaleomniverse.com (Netlify)",
        "motivation":           (data.get("reason") or "").strip() or
                                f"Timeline: {data.get('timeline','—')}, "
                                f"Condition: {data.get('condition','—')}",
        "status":               "new",
        "notes":                f"Imported from Netlify form submission {sub.get('id')} "
                                f"on {_now()}",
        "created_at":           sub.get("created_at", _now()),
        "updated_at":           _now(),
        "submitted_via":        "netlify_form",
        "netlify_submission_id":sub.get("id", ""),
        "timeline":             (data.get("timeline") or "").strip(),
        "condition":            (data.get("condition") or "").strip(),
    }


def _next_lead_id(leads: dict) -> str:
    n = len(leads) + 1
    while f"LEAD-{n:04d}" in leads:
        n += 1
    return f"LEAD-{n:04d}"


def _notify(lead: dict):
    addr_line = f"{lead['address']}, {lead['city']} {lead['state']}".strip(", ")
    to = os.environ.get("DIGEST_EMAIL") or os.environ.get("SMTP_USER", "")
    if not to:
        return
    body_text = (
        f"NEW seller lead from the website!\n\n"
        f"  Lead ID:  {lead['lead_id']}\n"
        f"  Address:  {addr_line}\n"
        f"  Name:     {lead['seller_name']}\n"
        f"  Phone:    {lead['seller_phone']}\n"
        f"  Email:    {lead['seller_email']}\n"
        f"  Timeline: {lead['timeline']}\n"
        f"  Condition:{lead['condition']}\n"
        f"  Reason:   {lead['motivation']}\n\n"
        f"Call or text the seller within 24h."
    )
    body_html = (
        f"<p><strong>NEW seller lead from the website!</strong></p>"
        f"<ul>"
        f"<li><strong>Lead ID:</strong> {lead['lead_id']}</li>"
        f"<li><strong>Address:</strong> {addr_line}</li>"
        f"<li><strong>Name:</strong> {lead['seller_name']}</li>"
        f"<li><strong>Phone:</strong> <a href=\"tel:{lead['seller_phone']}\">{lead['seller_phone']}</a></li>"
        f"<li><strong>Email:</strong> {lead['seller_email']}</li>"
        f"<li><strong>Timeline:</strong> {lead['timeline']}</li>"
        f"<li><strong>Condition:</strong> {lead['condition']}</li>"
        f"<li><strong>Reason:</strong> {lead['motivation']}</li>"
        f"</ul>"
        f"<p>Call or text within 24h. The 6-touch follow-up sequence will run automatically.</p>"
    )
    send_branded_email(
        to_email=to,
        subject=f"NEW seller lead — {addr_line}",
        body_text=body_text,
        body_html_inner=body_html,
    )


def run(dry_run: bool = False, quiet: bool = False) -> dict:
    token = _netlify_token()
    if not token:
        msg = ("No Netlify auth token. Either run `netlify login` once, or set "
               "NETLIFY_AUTH_TOKEN in .env.")
        if not quiet: console.print(f"[red]{msg}[/red]")
        return {"error": msg, "imported": 0}

    try:
        submissions = _fetch_submissions(token)
    except Exception as e:
        msg = f"Netlify API error: {e}"
        if not quiet: console.print(f"[red]{msg}[/red]")
        return {"error": msg, "imported": 0}

    leads = _load(LEADS_FILE, {})
    seen  = set(_load(SEEN_FILE, []))

    # Pre-compute the set of submission IDs already imported into leads.json
    existing_sub_ids = {
        L.get("netlify_submission_id", "") for L in leads.values()
        if L.get("netlify_submission_id")
    }
    skip_ids = seen | existing_sub_ids

    imported = []
    for sub in submissions:
        sub_id = sub.get("id", "")
        if not sub_id or sub_id in skip_ids:
            continue
        # Sanity check — must look like our seller-intake form
        data = sub.get("data", {})
        if not data.get("address") and not data.get("seller_phone"):
            continue

        lead_id = _next_lead_id(leads)
        lead    = _submission_to_lead(sub, lead_id)

        if dry_run:
            imported.append({"would_import": lead})
            continue

        leads[lead_id] = lead
        seen.add(sub_id)
        imported.append(lead)
        _notify(lead)

    if not dry_run and imported:
        _save(LEADS_FILE, leads)
        _save(SEEN_FILE, sorted(list(seen)))

    if not quiet:
        if imported:
            console.print(Panel(
                Text.from_markup(
                    f"[bold green]Imported {len(imported)} new lead(s) from Netlify[/bold green]\n"
                    + "\n".join(
                        f"  {l.get('lead_id','?')}  "
                        f"{l.get('address','')[:30]:30}  "
                        f"{l.get('seller_phone','')}"
                        for l in imported if isinstance(l, dict) and l.get("lead_id")
                    )
                ),
                border_style="green",
            ))
        else:
            console.print(f"[dim]No new submissions. ({len(submissions)} total on Netlify, all already imported.)[/dim]")

    return {"imported": len(imported), "total_on_netlify": len(submissions)}


def main():
    parser = argparse.ArgumentParser(description="Bridge Netlify form submissions → data/leads.json")
    parser.add_argument("--dry-run", action="store_true", help="Show what would import, don't write")
    parser.add_argument("--quiet",   action="store_true", help="Suppress output (for cron)")
    args = parser.parse_args()
    result = run(dry_run=args.dry_run, quiet=args.quiet)
    if "error" in result:
        sys.exit(1)


if __name__ == "__main__":
    main()
