"""
Lead Sieve — automated lead scoring and hot-list delivery.

Reads every lead in data/leads.json (+ data/ps_leads.json, data/hd_leads.json),
scores each one 0–100 across four signal groups, then:
  - Writes data/ls_scored.json with all leads + scores
  - Writes data/ls_reports/YYYY-MM-DD.md with a ranked digest
  - Emails the owner a daily hot-list

Scoring model (100 pts total):
  Distress / motivation type  0–35
  Equity / discount           0–30
  Lead age (DOM proxy)        0–20
  Contact info completeness   0–10
  Pipeline engagement bonus   0–5

Tiers:  HOT ≥ 70 | WARM 40–69 | COLD < 40

Entry point: run_full_cycle()
"""
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from autonomous import storage, mailer, metrics, billing

AGENT_KEY = "lead_sieve"
REPORTS_DIR = Path(__file__).parent.parent / "data" / "ls_reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

# ── Distress signal weights ──────────────────────────────────────────────────

MOTIVATION_SCORES = {
    "foreclosure":    35,
    "pre-foreclosure": 35,
    "pre_foreclosure": 35,
    "tax_delinquent": 30,
    "probate":        25,
    "vacant":         20,
    "code_violations": 10,
    "code_violation":  10,
}


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _lead_age_days(lead: dict) -> float:
    created = lead.get("created_at") or lead.get("added_at") or lead.get("scraped_at")
    if not created:
        return 0.0
    try:
        ts = datetime.fromisoformat(created.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return (_now_utc() - ts).total_seconds() / 86400
    except (ValueError, TypeError):
        return 0.0


def score_lead(lead: dict) -> dict:
    """Return a scored copy of the lead with 'sieve_score', 'sieve_tier', and
    'sieve_breakdown' fields added."""
    s = dict(lead)
    pts = 0
    breakdown = {}

    # 1. Distress / motivation type (0–35)
    motivation = (lead.get("motivation") or lead.get("record_type") or "").lower()
    dist_pts = MOTIVATION_SCORES.get(motivation, 0)
    # Bonus: multiple distress signals in notes
    notes_lower = (lead.get("notes") or "").lower()
    distress_keywords = ["tax lien", "lien", "eviction", "divorce", "estate",
                         "probate", "foreclos", "delinquent", "vacant", "code"]
    extra = sum(1 for kw in distress_keywords if kw in notes_lower and kw not in motivation)
    dist_pts = min(35, dist_pts + extra * 3)
    pts += dist_pts
    breakdown["distress"] = dist_pts

    # 2. Equity / discount (0–30)
    arv = lead.get("estimated_arv") or 0.0
    ask = lead.get("asking_price") or 0.0
    mao = lead.get("estimated_mao") or 0.0
    eq_pts = 0
    if arv and ask and arv > 0:
        discount = (arv - ask) / arv
        if discount >= 0.40:
            eq_pts = 30
        elif discount >= 0.30:
            eq_pts = 20
        elif discount >= 0.20:
            eq_pts = 10
        else:
            eq_pts = 5
    elif mao and ask:
        if ask <= mao:
            eq_pts = 30
        elif ask <= mao * 1.10:
            eq_pts = 15
        elif ask <= mao * 1.20:
            eq_pts = 5
    pts += eq_pts
    breakdown["equity"] = eq_pts

    # 3. Lead age (0–20) — older = seller more likely motivated to deal
    age_days = _lead_age_days(lead)
    if age_days >= 90:
        age_pts = 20
    elif age_days >= 60:
        age_pts = 15
    elif age_days >= 30:
        age_pts = 10
    elif age_days >= 7:
        age_pts = 5
    else:
        age_pts = 2
    pts += age_pts
    breakdown["age_days"] = round(age_days, 1)
    breakdown["age"] = age_pts

    # 4. Contact info completeness (0–10)
    has_email = bool(lead.get("seller_email") or lead.get("email") or lead.get("owner_email"))
    has_phone = bool(lead.get("seller_phone") or lead.get("phone") or lead.get("owner_phone"))
    contact_pts = (10 if has_email and has_phone
                   else 5 if has_email or has_phone
                   else 0)
    pts += contact_pts
    breakdown["contact"] = contact_pts

    # 5. Pipeline engagement bonus (0–5)
    status = (lead.get("status") or "").lower()
    engage_pts = 5 if status in ("contacted", "negotiating") else 0
    pts += engage_pts
    breakdown["engagement"] = engage_pts

    s["sieve_score"] = pts
    s["sieve_tier"] = "HOT" if pts >= 70 else "WARM" if pts >= 40 else "COLD"
    s["sieve_breakdown"] = breakdown
    s["sieve_scored_at"] = _now_utc().isoformat()
    return s


def _collect_all_leads() -> list[dict]:
    """Pull from the shared leads store + per-agent stores."""
    seen = set()
    results = []

    def _add(leads_data):
        if isinstance(leads_data, dict):
            items = list(leads_data.values())
        elif isinstance(leads_data, list):
            items = leads_data
        else:
            return
        for lead in items:
            key = (
                (lead.get("lead_id") or "")
                or (lead.get("case_number") or "")
                or (lead.get("address", "") + lead.get("city", ""))
            )
            if key and key not in seen:
                seen.add(key)
                results.append(lead)

    _add(storage.load("leads.json", {}))
    _add(storage.load("ps_leads.json", []))
    _add(storage.load("hd_leads.json", {}))
    return results


def _build_report(scored: list[dict], date_str: str) -> str:
    hot   = [l for l in scored if l["sieve_tier"] == "HOT"]
    warm  = [l for l in scored if l["sieve_tier"] == "WARM"]
    cold  = [l for l in scored if l["sieve_tier"] == "COLD"]

    lines = [
        f"# Lead Sieve Report — {date_str}",
        "",
        f"**Total leads scored:** {len(scored)}  "
        f"| 🔥 HOT: {len(hot)}  | WARM: {len(warm)}  | COLD: {len(cold)}",
        "",
    ]

    def _section(tier_leads: list[dict], header: str):
        if not tier_leads:
            return
        lines.append(f"## {header}")
        lines.append("")
        lines.append("| Score | Address | City | Motivation | Age (days) | Contact |")
        lines.append("|-------|---------|------|-----------|-----------|---------|")
        for lead in tier_leads:
            addr = lead.get("address") or lead.get("property_address") or "—"
            city = lead.get("city") or "—"
            mot  = lead.get("motivation") or lead.get("record_type") or "—"
            age  = lead["sieve_breakdown"].get("age_days", "—")
            has_contact = "✓" if lead["sieve_breakdown"].get("contact", 0) > 0 else "✗"
            lines.append(
                f"| **{lead['sieve_score']}** | {addr} | {city} | {mot} | {age} | {has_contact} |"
            )
        lines.append("")

    _section(hot,  "🔥 HOT Leads (score ≥ 70)")
    _section(warm, "🌡 WARM Leads (score 40–69)")
    _section(cold, "❄ COLD Leads (score < 40)")
    return "\n".join(lines)


def _email_body(scored: list[dict], date_str: str) -> str:
    hot  = [l for l in scored if l["sieve_tier"] == "HOT"]
    warm = [l for l in scored if l["sieve_tier"] == "WARM"]

    lines = [
        f"Lead Sieve Daily Hot-List — {date_str}",
        "=" * 50,
        f"Total leads scored: {len(scored)}",
        f"  🔥 HOT  : {len(hot)}",
        f"  🌡 WARM : {len(warm)}",
        f"  ❄ COLD : {len(scored) - len(hot) - len(warm)}",
        "",
    ]

    top = hot[:10] or warm[:10]
    if top:
        lines.append("TOP LEADS TO CONTACT TODAY:")
        lines.append("-" * 40)
        for i, lead in enumerate(top, 1):
            addr = lead.get("address") or lead.get("property_address") or "Unknown"
            city = lead.get("city") or "?"
            mot  = lead.get("motivation") or lead.get("record_type") or "unknown"
            lines.append(
                f"{i}. [{lead['sieve_tier']}] {addr}, {city} "
                f"— {mot} (score {lead['sieve_score']})"
            )
            if lead.get("seller_phone") or lead.get("phone"):
                phone = lead.get("seller_phone") or lead.get("phone")
                lines.append(f"   Phone: {phone}")
            if lead.get("seller_email") or lead.get("email"):
                email = lead.get("seller_email") or lead.get("email")
                lines.append(f"   Email: {email}")
        lines.append("")

    lines.append("Full report saved to data/ls_reports/")
    return "\n".join(lines)


def _trigger_followup_for_hot_leads(hot_leads: list[dict]) -> int:
    """Kick off stage-1 follow-up for HOT leads that have an email and haven't
    been contacted yet. Only acts on leads that exist in leads.json (have a
    lead_id) so we don't spam propscout/hud prospects without a verified email."""
    try:
        from followup_agent.tools import send_followup_email
    except ImportError:
        return 0

    triggered = 0
    for lead in hot_leads:
        lead_id = lead.get("lead_id")
        if not lead_id:
            continue
        email = lead.get("seller_email") or lead.get("email")
        if not email:
            continue
        status = (lead.get("status") or "new").lower()
        stage  = lead.get("followup_stage", 0)
        if status == "new" and stage == 0:
            result = send_followup_email(lead_id)
            if result.get("email_sent") or result.get("status") in ("sent", "skipped"):
                triggered += 1
    return triggered


def run_full_cycle() -> dict:
    date_str = _now_utc().strftime("%Y-%m-%d")

    all_leads = _collect_all_leads()
    scored = sorted(
        [score_lead(l) for l in all_leads],
        key=lambda x: x["sieve_score"],
        reverse=True,
    )

    hot  = [l for l in scored if l["sieve_tier"] == "HOT"]
    warm = [l for l in scored if l["sieve_tier"] == "WARM"]

    storage.save("ls_scored.json", scored)

    report_md = _build_report(scored, date_str)
    report_path = REPORTS_DIR / f"{date_str}.md"
    report_path.write_text(report_md, encoding="utf-8")

    followup_triggered = _trigger_followup_for_hot_leads(hot)

    digest_sent = 0
    owner_email = os.environ.get("LS_OWNER_EMAIL") or os.environ.get("SMTP_USER")
    if owner_email:
        body = _email_body(scored, date_str)
        result = mailer.send(
            AGENT_KEY,
            owner_email,
            f"🔥 Lead Sieve: {len(hot)} HOT leads — {date_str}",
            body,
            purpose="notification",
        )
        if result.get("status") == "sent":
            digest_sent = 1

    rev = billing.revenue_summary(AGENT_KEY)
    subs = storage.load("ls_subscribers.json", [])
    metrics.record(
        AGENT_KEY,
        leads_scored=len(scored),
        hot_leads=len(hot),
        warm_leads=len(warm),
        followup_triggered=followup_triggered,
        digest_sent=digest_sent,
        active_subs=sum(1 for s in subs if s.get("status") == "active"),
        mrr=rev["mrr"],
    )

    return {
        "leads_scored":        len(scored),
        "hot_leads":           len(hot),
        "warm_leads":          len(warm),
        "cold_leads":          len(scored) - len(hot) - len(warm),
        "followup_triggered":  followup_triggered,
        "digest_sent":         digest_sent,
        "report_path":         str(report_path),
        "mrr":                 rev["mrr"],
    }
