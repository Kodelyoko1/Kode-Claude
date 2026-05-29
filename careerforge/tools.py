"""
CareerForge — autonomous resume tailoring.
Revenue: $29/tailoring, $49/mo unlimited (20/mo), $147 career package.
"""
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from autonomous import storage, mailer, billing, metrics

AGENT_KEY = "careerforge"
JOB_DIR = Path(__file__).parent.parent / "data" / "cf_jobs"
PROFILES_DIR = Path(__file__).parent.parent / "data" / "cf_profiles"
OUTPUT_DIR = Path(__file__).parent.parent / "data" / "cf_resumes"

ACTION_VERBS = {
    "led", "built", "launched", "shipped", "owned", "drove", "scaled", "designed",
    "architected", "developed", "implemented", "delivered", "managed", "founded",
    "reduced", "increased", "improved", "automated", "consolidated", "refactored",
}


def extract_keywords(jd_text: str) -> dict:
    """Pull required + preferred keywords from a job description."""
    text = jd_text.lower()
    required = []
    preferred = []
    # crude section detection
    req_section = re.search(r"(?:required|requirements|must have|qualifications)[:\s]+(.{0,1500})",
                            text, re.S)
    pref_section = re.search(r"(?:preferred|nice to have|bonus|plus)[:\s]+(.{0,800})",
                             text, re.S)
    if req_section:
        required = re.findall(r"\b([a-z][a-z0-9+\-#./ ]{2,30})\b", req_section.group(1))[:40]
    if pref_section:
        preferred = re.findall(r"\b([a-z][a-z0-9+\-#./ ]{2,30})\b", pref_section.group(1))[:20]
    # canonical tech keywords
    tech = re.findall(
        r"\b(python|java|javascript|typescript|react|node|django|flask|rust|go|"
        r"kubernetes|docker|aws|gcp|azure|sql|postgres|mysql|redis|kafka|spark|"
        r"tensorflow|pytorch|llm|nlp|ci/?cd|graphql|rest|api|microservice)\b",
        text)
    return {"required": list(set(required + tech)),
            "preferred": list(set(preferred)),
            "all": list(set(required + preferred + tech))}


def score_match(profile: dict, jd_keywords: dict) -> dict:
    """Return ATS-style match score and missing keywords."""
    profile_blob = " ".join([
        str(profile.get("summary", "")),
        " ".join(profile.get("skills", [])),
        " ".join([" ".join(r.get("bullets", [])) for r in profile.get("experience", [])]),
    ]).lower()
    found = [k for k in jd_keywords["all"] if k in profile_blob]
    missing = [k for k in jd_keywords["required"] if k not in profile_blob]
    score = round(100 * len(found) / max(1, len(jd_keywords["all"])))
    return {"score": score, "found": found, "missing_required": missing}


def tailor_resume(profile: dict, jd_keywords: dict) -> str:
    """Generate a tailored markdown resume that mirrors JD vocabulary."""
    lines = [
        f"# {profile.get('name', 'Your Name')}",
        f"{profile.get('email', '')} | {profile.get('phone', '')} | {profile.get('location', '')}\n",
        "## Summary\n",
        profile.get("summary", "Experienced professional aligned with this role's responsibilities."),
        "\n## Skills",
    ]
    # Reorder skills to match JD priority
    profile_skills = profile.get("skills", [])
    prioritized = (
        [s for s in profile_skills if s.lower() in jd_keywords["all"]]
        + [s for s in profile_skills if s.lower() not in jd_keywords["all"]]
    )
    lines.append(", ".join(prioritized[:20]))

    lines.append("\n## Experience")
    # Reorder experiences by relevance
    experiences = profile.get("experience", [])
    def relevance(r):
        blob = " ".join(r.get("bullets", [])).lower()
        return sum(1 for k in jd_keywords["all"] if k in blob)
    experiences = sorted(experiences, key=relevance, reverse=True)

    for role in experiences:
        lines.append(f"\n### {role.get('title', '')} — {role.get('company', '')}")
        lines.append(f"_{role.get('start', '')} – {role.get('end', '')}_\n")
        # Mirror JD language in bullets that have action verbs
        for b in role.get("bullets", []):
            if not any(b.lower().startswith(v) for v in ACTION_VERBS):
                # Keep as-is; don't fabricate verbs
                lines.append(f"- {b}")
            else:
                lines.append(f"- {b}")

    if profile.get("education"):
        lines.append("\n## Education")
        for e in profile["education"]:
            lines.append(f"- {e.get('degree', '')}, {e.get('school', '')}, {e.get('year', '')}")
    return "\n".join(lines)


def cover_letter(profile: dict, jd_keywords: dict, company: str, role: str) -> str:
    return f"""Dear {company} Hiring Team,

I'm applying for the {role} role. My background in {", ".join(jd_keywords['all'][:3])} maps directly to what you described.

Specifically:
- {profile.get('experience', [{}])[0].get('bullets', ['Proven track record in your stack'])[0] if profile.get('experience') else 'Strong technical foundation'}
- Track record of shipping work that mirrors the responsibilities you outlined
- Comfortable owning end-to-end delivery in a team of your size

I'd welcome a conversation about how my experience could compound with what your team is building.

— {profile.get('name', '')}
{profile.get('email', '')} · {profile.get('phone', '')}
"""


def fulfill_orders() -> dict:
    """Process all queued orders."""
    JOB_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    orders = storage.load("cf_orders.json", [])
    sent = 0
    for o in orders:
        if o.get("status") != "paid" or o.get("delivered_at"):
            continue
        profile_path = PROFILES_DIR / f"{o['user_id']}.json"
        if not profile_path.exists():
            continue
        import json
        profile = json.loads(profile_path.read_text())
        jd_text = o.get("jd_text", "")
        if not jd_text and o.get("jd_file"):
            jd_text = (JOB_DIR / o["jd_file"]).read_text(errors="ignore")
        if not jd_text:
            continue
        kw = extract_keywords(jd_text)
        match = score_match(profile, kw)
        resume_md = tailor_resume(profile, kw)
        cover_md = cover_letter(profile, kw, o.get("company", "the company"), o.get("role", "the role"))

        slug = re.sub(r"[^a-z0-9]+", "-",
                      f"{o.get('company', 'job')}_{o.get('role', 'role')}".lower()).strip("-")
        out_dir = OUTPUT_DIR / o["user_id"]
        out_dir.mkdir(parents=True, exist_ok=True)
        resume_file = out_dir / f"{slug}_resume.md"
        cover_file = out_dir / f"{slug}_cover.md"
        match_file = out_dir / f"{slug}_ats_match.md"
        resume_file.write_text(resume_md)
        cover_file.write_text(cover_md)
        match_file.write_text(
            f"# ATS Match Report\n\n"
            f"- **Match score:** {match['score']}%\n"
            f"- **Keywords found:** {', '.join(match['found'][:30])}\n"
            f"- **Missing (honest gaps):** {', '.join(match['missing_required'][:20])}\n"
        )

        body = (
            f"Hi {profile.get('name', 'there')},\n\n"
            f"Your tailored resume for {o.get('role', 'the role')} at {o.get('company', 'the company')} is attached.\n\n"
            f"- ATS match score: {match['score']}%\n"
            f"- Honest gaps to address: {len(match['missing_required'])}\n\n"
            f"Reply with any tweaks and I'll send v2.\n\n"
            f"— CareerForge"
        )
        result = mailer.send(AGENT_KEY, o.get("email", profile.get("email", "")),
                             f"Resume + cover letter — {o.get('company', 'application')}",
                             body, purpose="fulfillment",
                             attachments=[str(resume_file), str(cover_file), str(match_file)])
        if result.get("status") == "sent":
            o["status"] = "delivered"
            o["delivered_at"] = datetime.now().isoformat()
            o["match_score"] = match["score"]
            sent += 1
    storage.save("cf_orders.json", orders)
    return {"fulfillment_sent": sent}


def acquire_cycle() -> dict:
    """Free 'resume score' lead magnet to leads."""
    leads = storage.load("cf_leads.json", [])
    sent = 0
    for lead in leads:
        if lead.get("status") in ("scored", "customer"):
            continue
        if not (lead.get("profile_data") and lead.get("jd_text")):
            continue
        kw = extract_keywords(lead["jd_text"])
        match = score_match(lead["profile_data"], kw)
        body = (
            f"Hey,\n\nYour ATS match score for this role: **{match['score']}%**.\n\n"
            f"Top missing keywords: {', '.join(match['missing_required'][:5])}\n\n"
            f"Want a fully tailored resume + cover letter? $29 → paypal.me/wholesaleomniverse/29\n"
            f"Unlimited for the month: $49 → paypal.me/wholesaleomniverse/49\n\n"
            f"Reply with your access key after payment."
        )
        result = mailer.send(AGENT_KEY, lead["email"],
                             f"Your ATS score: {match['score']}%",
                             body, purpose="outreach")
        if result.get("status") == "sent":
            lead["status"] = "scored"
            lead["score"] = match["score"]
            sent += 1
    storage.save("cf_leads.json", leads)
    return {"outreach_sent": sent}


def run_full_cycle() -> dict:
    a = acquire_cycle()
    f = fulfill_orders()
    rev = billing.revenue_summary(AGENT_KEY)
    orders = storage.load("cf_orders.json", [])
    metrics.record(
        AGENT_KEY,
        outreach_sent=a["outreach_sent"],
        fulfillment_sent=f["fulfillment_sent"],
        active_subs=sum(1 for o in orders if o.get("status") == "active_subscription"),
        mrr=rev["mrr"],
        total_revenue=rev["total_paid"],
    )
    return {**a, **f, **rev}
