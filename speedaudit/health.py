"""
Derived audit-history health for SpeedAudit.

Walks the reports in data/sa_outputs/ and the delivery log in
data/sa_delivery_log.json to surface:

  · average performance score across all audits
  · count + names of error audits (sites that wouldn't respond)
  · score distribution by bucket
  · oldest unaudited lead (from sa_leads.json with no trial_sent)
  · per-subscriber last-audit age

No new state file — pure derivation.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

DATA_DIR     = Path(__file__).parent.parent / "data"
OUTPUTS_DIR  = DATA_DIR / "sa_outputs"
LEADS_FILE   = DATA_DIR / "sa_leads.json"
SUBS_FILE    = DATA_DIR / "sa_subscribers.json"
DELIVERY_LOG = DATA_DIR / "sa_delivery_log.json"

SCORE_RE = re.compile(r"\*\*Score:\*\*\s*(\d+)/100", re.M)
URL_RE   = re.compile(r"\*\*URL:\*\*\s*(\S+)", re.M)


def _load(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return default


def aggregate() -> dict:
    """Walk all reports and aggregate score distribution + error count."""
    if not OUTPUTS_DIR.exists():
        return {"total": 0, "average_score": None,
                "errors": 0, "buckets": {}}
    reports = list(OUTPUTS_DIR.glob("*.md"))
    scores = []
    errors = []
    for r in reports:
        try:
            text = r.read_text(errors="ignore")
        except OSError:
            continue
        if "Could not audit" in text:
            url_match = URL_RE.search(text)
            errors.append({"slug": r.stem, "url": url_match.group(1) if url_match else ""})
            continue
        score_match = SCORE_RE.search(text)
        if score_match:
            scores.append(int(score_match.group(1)))
    buckets = Counter()
    for s in scores:
        if s >= 90:
            buckets["90-100"] += 1
        elif s >= 75:
            buckets["75-89"] += 1
        elif s >= 50:
            buckets["50-74"] += 1
        else:
            buckets["<50"] += 1
    avg = sum(scores) / len(scores) if scores else None
    return {
        "total":          len(reports),
        "scored":         len(scores),
        "errors":         len(errors),
        "error_samples":  errors[:5],
        "average_score":  round(avg, 1) if avg is not None else None,
        "buckets":        dict(buckets),
    }


def lead_inventory() -> dict:
    leads = _load(LEADS_FILE, [])
    if not isinstance(leads, list):
        return {"total": 0, "un_pitched": 0, "oldest_unpitched": None}
    un_pitched = [l for l in leads if not l.get("trial_sent")]
    return {
        "total": len(leads),
        "un_pitched": len(un_pitched),
        "oldest_unpitched": un_pitched[0]["email"] if un_pitched else None,
    }


def subscriber_freshness() -> list[dict]:
    """For each active subscriber, when was their last monthly delivery?"""
    subs = _load(SUBS_FILE, [])
    log  = _load(DELIVERY_LOG, {})
    if not isinstance(subs, list) or not isinstance(log, dict):
        return []
    out = []
    now = datetime.now()
    for s in subs:
        if s.get("status") != "active":
            continue
        email = s.get("email", "")
        last = log.get(email, {}).get("last_audit_at", "")
        if last:
            try:
                age_d = (now - datetime.fromisoformat(last.split("+")[0])).days
            except ValueError:
                age_d = -1
        else:
            age_d = -1
        out.append({"email": email, "site": s.get("site", ""),
                    "plan": s.get("plan", ""), "days_since_audit": age_d})
    return sorted(out, key=lambda x: -x["days_since_audit"])


def report_lines() -> list[str]:
    agg = aggregate()
    leads = lead_inventory()
    subs = subscriber_freshness()

    lines = ["== SpeedAudit — derived health =="]
    lines.append("")
    lines.append(f"Audits produced: {agg['total']}  ·  scored: {agg['scored']}  ·  "
                 f"errors: {agg['errors']}")
    if agg["average_score"] is not None:
        lines.append(f"Average score: {agg['average_score']}/100")
    if agg.get("buckets"):
        lines.append("Score distribution:")
        for bucket in ("90-100", "75-89", "50-74", "<50"):
            n = agg["buckets"].get(bucket, 0)
            lines.append(f"  {bucket:<8s}  {n}")
    if agg["error_samples"]:
        lines.append("")
        lines.append("Error samples (sites that wouldn't audit):")
        for e in agg["error_samples"]:
            lines.append(f"  {e['slug']:<40s}  {e['url']}")

    lines.append("")
    lines.append(f"Leads: {leads['total']}  ·  un-pitched: {leads['un_pitched']}"
                 + (f"  ·  next: {leads['oldest_unpitched']}" if leads['oldest_unpitched'] else ""))

    if subs:
        lines.append("")
        lines.append("Active subscriber audit freshness:")
        for s in subs:
            age = f"{s['days_since_audit']}d" if s["days_since_audit"] >= 0 else "never"
            lines.append(f"  {s['email']:<30s}  {s['site']:<30s}  last={age}  ({s['plan']})")
    return lines
