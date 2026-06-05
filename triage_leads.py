#!/usr/bin/env python3
"""
Triage data/leads.json — categorize by actionability and surface the
freshest rows you can actually work today.

Usage:
  python3 triage_leads.py                  # summary + top 10 phone-actionable
  python3 triage_leads.py --bucket phone   # all phone-actionable
  python3 triage_leads.py --bucket email   # all email-actionable
  python3 triage_leads.py --bucket stale   # leads with names but no contact
  python3 triage_leads.py --bucket sources # gov/FB-group source notes (need scraping first)
  python3 triage_leads.py --bucket all     # everything
  python3 triage_leads.py --json           # machine-readable output of all buckets
"""
import argparse
import json
import sys
from pathlib import Path

DATA = Path(__file__).parent / "data" / "leads.json"


def _phone(l):
    return (l.get("phone") or l.get("phone_number")
            or l.get("seller_phone") or l.get("owner_phone") or "").strip()


def _email(l):
    return (l.get("email") or l.get("seller_email")
            or l.get("owner_email") or "").strip()


def _is_source_note(l):
    addr = (l.get("address") or "").lower()
    src = (l.get("lead_source") or "").lower()
    keywords = ("courthouse", "facebook group", "wholesale real estate -",
                "list", "records -", "leads network", "directory",
                "subreddit", "forum")
    return any(k in addr for k in keywords) or any(k in src for k in keywords)


def _is_test(l):
    addr = (l.get("address") or "").lower()
    return "742 evergreen" in addr  # The Simpsons address


def _toll_free(phone):
    """Return True if the phone is a toll-free / shared trustee number."""
    digits = "".join(c for c in phone if c.isdigit())
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        return False
    npa = digits[:3]
    return npa in {"800", "833", "844", "855", "866", "877", "888"}


def bucketize(leads):
    out = {"phone_direct": [], "phone_shared": [], "email": [],
           "stale": [], "sources": [], "in_progress": [], "test": []}
    # First pass: count phone frequencies to spot shared numbers
    from collections import Counter
    phone_count = Counter()
    for l in leads:
        p = _phone(l)
        if p: phone_count[p] += 1

    for l in leads:
        if _is_test(l):
            out["test"].append(l); continue
        if _is_source_note(l):
            out["sources"].append(l); continue
        status = (l.get("status") or "").lower()
        if status in ("assigned", "contract sent", "under contract", "closed"):
            out["in_progress"].append(l); continue
        p = _phone(l)
        if p and status in ("new", ""):
            # A "direct line" = used once across the dataset AND not toll-free
            if phone_count[p] == 1 and not _toll_free(p):
                out["phone_direct"].append(l)
            else:
                out["phone_shared"].append(l)
            continue
        if _email(l) and status in ("new", ""):
            out["email"].append(l); continue
        if (l.get("seller_name") or "").strip():
            out["stale"].append(l)
        else:
            out["sources"].append(l)
    return out


def _sort_key(l):
    """Freshest first by motivation score, then by lead_id (newer LEAD- IDs first)."""
    score = float(l.get("motivation_score") or l.get("score") or 0)
    lid = l.get("lead_id", "")
    try:
        n = int(lid.replace("LEAD-", ""))
    except (ValueError, AttributeError):
        n = 0
    return (-score, -n)


def fmt_lead(l, idx):
    addr = l.get("address", "?")[:50]
    city = l.get("city", "")
    st = l.get("state", "")
    name = l.get("seller_name", "")
    phone = _phone(l)
    email = _email(l)
    arv = l.get("estimated_arv") or 0
    rep = l.get("estimated_repairs") or 0
    mao = l.get("estimated_mao") or 0
    asking = l.get("asking_price") or 0
    motiv = (l.get("motivation") or "")[:55]
    contact = phone or email or "(none)"
    return (f"{idx:>3d}. {l.get('lead_id', '?'):<10s}  {addr:<50s}  {city}, {st}\n"
            f"     seller: {name or '(unknown)':<28s}  contact: {contact}\n"
            f"     ARV ${arv:>9,.0f}  repairs ${rep:>8,.0f}  MAO ${mao:>9,.0f}  "
            f"asking ${asking:>9,.0f}\n"
            f"     motivation: {motiv}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--bucket", default="summary",
                   choices=["summary", "phone_direct", "phone_shared", "email",
                            "stale", "sources", "in_progress", "all"])
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--json", action="store_true")
    a = p.parse_args()

    if not DATA.exists():
        print(f"✗ {DATA} not found"); sys.exit(1)
    raw = json.loads(DATA.read_text())
    leads = list(raw.values()) if isinstance(raw, dict) else raw
    buckets = bucketize(leads)

    if a.json:
        print(json.dumps({k: len(v) for k, v in buckets.items()}, indent=2))
        return

    # Summary
    print(f"Total in data/leads.json: {len(leads)}")
    print()
    print(f"{'BUCKET':<14s}  {'COUNT':>6s}  WHAT IT MEANS")
    print("-" * 80)
    print(f"{'phone_direct':<14s}  {len(buckets['phone_direct']):>6d}  Unique non-toll-free — CALL THESE THIS WEEK")
    print(f"{'phone_shared':<14s}  {len(buckets['phone_shared']):>6d}  Toll-free / shared trustee — skip or research listing")
    print(f"{'email':<14s}  {len(buckets['email']):>6d}  Actionable via FollowUp agent")
    print(f"{'stale':<14s}  {len(buckets['stale']):>6d}  Has seller name, no contact — needs enrichment")
    print(f"{'sources':<14s}  {len(buckets['sources']):>6d}  Gov records / FB groups — owner must scrape these")
    print(f"{'in_progress':<14s}  {len(buckets['in_progress']):>6d}  Already assigned/under contract")
    print(f"{'test':<14s}  {len(buckets['test']):>6d}  Seed/test records — ignore")
    print()

    if a.bucket == "summary":
        # Show top N phone-direct (the real workable pool)
        print(f"Top {a.limit} phone-direct (work these THIS WEEK):")
        print()
        for i, l in enumerate(sorted(buckets["phone_direct"], key=_sort_key)[:a.limit], 1):
            print(fmt_lead(l, i))
            print()
        print(f"For more: python3 triage_leads.py --bucket phone_direct --limit 50")
        return

    if a.bucket == "all":
        for k, v in buckets.items():
            print(f"\n=== {k.upper()} ({len(v)}) ===")
            for i, l in enumerate(sorted(v, key=_sort_key)[:a.limit], 1):
                print(fmt_lead(l, i))
                print()
        return

    rows = buckets.get(a.bucket, [])
    print(f"=== {a.bucket.upper()} ({len(rows)}) ===\n")
    for i, l in enumerate(sorted(rows, key=_sort_key)[:a.limit], 1):
        print(fmt_lead(l, i))
        print()


if __name__ == "__main__":
    main()
