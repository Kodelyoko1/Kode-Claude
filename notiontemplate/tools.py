"""
NotionTemplate — productized Notion-template generator + landing page kit.
Revenue: $19 per template, $49/mo (3 templates), $149/mo unlimited.

Two input modes:
  1. Owner-dropped manifest in data/nt_inputs/{slug}.json:
       {
         "title": "Solopreneur CRM",
         "preset": "crm",
         "niche": "solo consultants",
         "tagline": "Track 100 leads without losing your mind",
         "price": 19,
         "properties_override": [...]      # optional, replaces preset's defaults
       }
  2. Preset-only quick mode: data/nt_inputs/{slug}.preset uses just the
     preset name on a single line, all other fields inferred.

Presets bundled (no external config needed): habit_tracker, crm,
project_management, content_calendar, meeting_notes, reading_list,
expense_tracker, ooo_documentation.

Engine:
  - Pure-Python preset library → no Notion API required for the v1 product.
  - Output is an upload-ready bundle:
      data/nt_outputs/{slug}/template_spec.md      (build steps for the user)
      data/nt_outputs/{slug}/template.json         (schema; Notion-API-shaped
                                                     for future automation)
      data/nt_outputs/{slug}/landing_page.md       (Gumroad-ready listing copy)
      data/nt_outputs/{slug}/sample_data.csv       (importable seed data)

If NOTION_API_KEY + NOTION_PARENT_PAGE_ID are set, an extra step pushes
the template into the connected workspace. Absent that, the manual
template_spec.md is fully sufficient.
"""
import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from autonomous import storage, mailer, billing, metrics

AGENT_KEY = "notiontemplate"
INPUTS_DIR = Path(__file__).parent.parent / "data" / "nt_inputs"
OUTPUTS_DIR = Path(__file__).parent.parent / "data" / "nt_outputs"


PRESETS = {
    "habit_tracker": {
        "title": "Habit Tracker",
        "tagline": "Build streaks. Compound improvement.",
        "properties": [
            {"name": "Habit", "type": "title"},
            {"name": "Category", "type": "select", "options": ["Health", "Mind", "Work", "Money"]},
            {"name": "Frequency", "type": "select", "options": ["Daily", "Weekdays", "Weekly", "Monthly"]},
            {"name": "Streak", "type": "number"},
            {"name": "Last Done", "type": "date"},
            {"name": "Active", "type": "checkbox"},
            {"name": "Notes", "type": "rich_text"},
        ],
        "views": ["Today (filter: Active = true)", "By Category (board)", "Streak Leaders (sort: Streak desc)"],
        "seed_rows": [
            ["Morning workout", "Health", "Daily", "7", "2026-05-29", "true", "30 min minimum"],
            ["Read 20 pages", "Mind", "Daily", "12", "2026-05-29", "true", ""],
            ["Inbox zero", "Work", "Weekdays", "3", "2026-05-29", "true", ""],
        ],
    },
    "crm": {
        "title": "Solo CRM",
        "tagline": "Track 100 leads without losing your mind",
        "properties": [
            {"name": "Lead", "type": "title"},
            {"name": "Company", "type": "rich_text"},
            {"name": "Stage", "type": "select", "options": ["New", "Contacted", "Demo", "Proposal", "Won", "Lost"]},
            {"name": "Owner", "type": "person"},
            {"name": "Email", "type": "email"},
            {"name": "Phone", "type": "phone_number"},
            {"name": "Value (USD)", "type": "number"},
            {"name": "Next Action", "type": "rich_text"},
            {"name": "Next Action Date", "type": "date"},
            {"name": "Source", "type": "select", "options": ["Inbound", "Referral", "Outbound", "Event"]},
            {"name": "Last Touched", "type": "date"},
        ],
        "views": [
            "Pipeline (board, grouped by Stage)",
            "This Week's Follow-ups (filter: Next Action Date this week)",
            "Cold Leads (filter: Last Touched > 30 days ago)",
        ],
        "seed_rows": [
            ["Sarah Chen", "Acme Corp", "Demo", "", "sarah@acme.com", "+1...", "5000", "Send proposal", "2026-06-02", "Inbound", "2026-05-25"],
            ["John Patel", "Beta LLC", "Contacted", "", "john@beta.com", "+1...", "1200", "Follow up on questions", "2026-06-01", "Referral", "2026-05-28"],
        ],
    },
    "project_management": {
        "title": "Project Hub",
        "tagline": "Every project, every owner, every deadline — one page",
        "properties": [
            {"name": "Task", "type": "title"},
            {"name": "Project", "type": "select", "options": ["Project A", "Project B", "Backlog"]},
            {"name": "Owner", "type": "person"},
            {"name": "Status", "type": "select", "options": ["Inbox", "In Progress", "Blocked", "Done"]},
            {"name": "Priority", "type": "select", "options": ["P0", "P1", "P2", "P3"]},
            {"name": "Due", "type": "date"},
            {"name": "Estimate (hrs)", "type": "number"},
            {"name": "Tags", "type": "multi_select", "options": ["Bug", "Feature", "Research", "Ops"]},
        ],
        "views": [
            "Kanban (board, grouped by Status)",
            "This Sprint (filter: Due within 14 days)",
            "Blocked (filter: Status = Blocked)",
            "By Owner (board, grouped by Owner)",
        ],
        "seed_rows": [
            ["Ship onboarding email v2", "Project A", "", "In Progress", "P1", "2026-06-05", "8", "Feature"],
            ["Audit Q2 conversion drop", "Project A", "", "Inbox", "P0", "2026-06-02", "4", "Research"],
        ],
    },
    "content_calendar": {
        "title": "Content Calendar",
        "tagline": "Plan, schedule, repurpose — all in one timeline",
        "properties": [
            {"name": "Title", "type": "title"},
            {"name": "Format", "type": "select", "options": ["Short", "Long-form", "Newsletter", "Carousel", "Podcast"]},
            {"name": "Platform", "type": "multi_select", "options": ["YouTube", "LinkedIn", "X", "IG", "Substack"]},
            {"name": "Status", "type": "select", "options": ["Idea", "Draft", "Edit", "Scheduled", "Published"]},
            {"name": "Publish Date", "type": "date"},
            {"name": "Hook", "type": "rich_text"},
            {"name": "Performance", "type": "number"},
        ],
        "views": [
            "Calendar (publish date)",
            "Pipeline (board, grouped by Status)",
            "Top Performers (sort: Performance desc)",
        ],
        "seed_rows": [
            ["5 mistakes killing your DTC checkout", "Carousel", "LinkedIn,IG", "Scheduled", "2026-06-01", "Surprise shipping kills 40% of carts.", ""],
        ],
    },
    "meeting_notes": {
        "title": "Meeting Notes",
        "tagline": "Searchable. Actionable. Never lose a decision again.",
        "properties": [
            {"name": "Meeting", "type": "title"},
            {"name": "Date", "type": "date"},
            {"name": "Attendees", "type": "multi_select"},
            {"name": "Type", "type": "select", "options": ["1:1", "Standup", "Strategy", "Customer", "Vendor"]},
            {"name": "Decisions", "type": "rich_text"},
            {"name": "Action Items", "type": "rich_text"},
            {"name": "Linked Project", "type": "select"},
        ],
        "views": ["By Date", "By Type (board)", "Pending Actions (filter: Action Items not empty)"],
        "seed_rows": [["Q3 planning", "2026-06-03", "", "Strategy", "Ship onboarding v2 by 7/15", "Sarah owns proposal", ""]],
    },
    "reading_list": {
        "title": "Reading List",
        "tagline": "Books, articles, podcasts — captured, rated, revisited",
        "properties": [
            {"name": "Title", "type": "title"},
            {"name": "Type", "type": "select", "options": ["Book", "Article", "Podcast", "Video", "Paper"]},
            {"name": "Author", "type": "rich_text"},
            {"name": "Status", "type": "select", "options": ["To Read", "Reading", "Finished", "Abandoned"]},
            {"name": "Rating", "type": "select", "options": ["★★★★★", "★★★★", "★★★", "★★", "★"]},
            {"name": "Topic", "type": "multi_select"},
            {"name": "Key Takeaway", "type": "rich_text"},
            {"name": "URL", "type": "url"},
        ],
        "views": ["Currently Reading (filter: Status = Reading)", "5★ Picks (filter: Rating = ★★★★★)", "By Topic"],
        "seed_rows": [["Atomic Habits", "Book", "James Clear", "Finished", "★★★★★", "Productivity", "Environment > willpower.", ""]],
    },
    "expense_tracker": {
        "title": "Expense Tracker",
        "tagline": "See where the money goes. Without a spreadsheet.",
        "properties": [
            {"name": "Item", "type": "title"},
            {"name": "Date", "type": "date"},
            {"name": "Amount (USD)", "type": "number"},
            {"name": "Category", "type": "select", "options": ["Software", "Marketing", "Office", "Travel", "Food", "Other"]},
            {"name": "Vendor", "type": "rich_text"},
            {"name": "Tax Deductible", "type": "checkbox"},
            {"name": "Receipt", "type": "files"},
        ],
        "views": ["This Month (filter: Date this month)", "By Category (board)", "Deductible (filter: Tax Deductible = true)"],
        "seed_rows": [["Claude API", "2026-05-29", "20", "Software", "Anthropic", "true", ""]],
    },
    "ooo_documentation": {
        "title": "Ops & SOPs",
        "tagline": "Internal docs your team will actually open",
        "properties": [
            {"name": "Doc", "type": "title"},
            {"name": "Owner", "type": "person"},
            {"name": "Category", "type": "select", "options": ["Onboarding", "Process", "Tool", "Policy", "Incident"]},
            {"name": "Last Reviewed", "type": "date"},
            {"name": "Status", "type": "select", "options": ["Draft", "Live", "Archived"]},
            {"name": "Related Team", "type": "multi_select", "options": ["Eng", "Sales", "Marketing", "Ops"]},
        ],
        "views": ["Live Docs (filter: Status = Live)", "Stale (filter: Last Reviewed > 90 days)", "By Category (board)"],
        "seed_rows": [["How we ship on Fridays", "", "Process", "2026-05-01", "Live", "Eng"]],
    },
}


def _render_spec_md(slug: str, spec: dict, preset: dict) -> str:
    title = spec.get("title") or preset["title"]
    tagline = spec.get("tagline") or preset["tagline"]
    properties = spec.get("properties_override") or preset["properties"]

    lines = [
        f"# {title}",
        "",
        f"_{tagline}_",
        "",
        "## How to build this template in Notion",
        "",
        "1. Create a new page in Notion.",
        "2. Add a `Database — Table` block.",
        "3. Add the properties below in order (set the type as listed).",
        "4. Create the views in the **Views** section using the filter/sort/group hints.",
        "5. Import `sample_data.csv` from the bundle to populate seed rows.",
        "",
        "## Properties",
        "",
        "| Name | Type | Options |",
        "|---|---|---|",
    ]
    for p in properties:
        opts = ", ".join(p.get("options", [])) if p.get("options") else ""
        lines.append(f"| {p['name']} | {p['type']} | {opts} |")
    lines.append("")
    lines.append("## Views")
    lines.append("")
    for v in preset["views"]:
        lines.append(f"- {v}")
    lines.append("")
    lines.append("## Suggested next moves")
    lines.append("")
    lines.append("- Connect a calendar block above the database for upcoming dates.")
    lines.append("- Add a synced block at the top with the template's purpose.")
    lines.append("- Duplicate the page once filled in — that's your reusable template.")
    lines.append("")
    lines.append("---")
    lines.append(f"_Generated {datetime.now():%Y-%m-%d} by NotionTemplate._")
    return "\n".join(lines)


def _render_template_json(spec: dict, preset: dict) -> dict:
    return {
        "title": spec.get("title") or preset["title"],
        "tagline": spec.get("tagline") or preset["tagline"],
        "schema": {
            "properties": spec.get("properties_override") or preset["properties"],
        },
        "views": preset["views"],
        "notion_api_shape": {
            "parent": {"type": "page_id", "page_id": "[[FILL_IN]]"},
            "title": [{"text": {"content": spec.get("title") or preset["title"]}}],
            "is_inline": False,
        },
    }


def _render_landing(spec: dict, preset: dict) -> str:
    title = spec.get("title") or preset["title"]
    tagline = spec.get("tagline") or preset["tagline"]
    niche = spec.get("niche", "anyone tired of half-built systems")
    price = spec.get("price", 19)
    prop_count = len(spec.get("properties_override") or preset["properties"])
    return "\n".join([
        f"# {title}",
        "",
        f"**{tagline}**",
        "",
        f"Built for {niche}.",
        "",
        "## What you get",
        "",
        f"- A pre-built Notion database with **{prop_count} properties** ready to use.",
        f"- **{len(preset['views'])} pre-configured views** so you're not staring at a blank canvas.",
        "- Sample data so you can see what 'finished' looks like.",
        "- A 2-minute setup walkthrough.",
        "",
        "## Why this beats a blank Notion page",
        "",
        "Blank Notion = decision fatigue. This template makes the decisions for you so you can start using it today.",
        "",
        "## Pricing",
        "",
        f"**${price}** one-time. Lifetime access. Free updates.",
        "",
        "[Buy now →]([[INSERT_GUMROAD_LINK]])",
        "",
        "---",
        "30-day refund, no questions asked.",
    ])


def _render_csv(preset: dict) -> str:
    headers = [p["name"] for p in preset["properties"]]
    lines = [",".join(headers)]
    for row in preset.get("seed_rows", []):
        # naive CSV — escape commas in cells by wrapping in quotes
        lines.append(",".join(f'"{c}"' if "," in str(c) else str(c) for c in row))
    return "\n".join(lines)


def _try_push_to_notion(template_json: dict) -> dict:
    """Optional: if NOTION_API_KEY + NOTION_PARENT_PAGE_ID set, create the template in workspace."""
    key = os.environ.get("NOTION_API_KEY")
    parent = os.environ.get("NOTION_PARENT_PAGE_ID")
    if not (key and parent):
        return {"pushed": False, "reason": "no NOTION_API_KEY / NOTION_PARENT_PAGE_ID"}
    try:
        import urllib.request
        body = {
            "parent": {"type": "page_id", "page_id": parent},
            "title": template_json["notion_api_shape"]["title"],
            "is_inline": False,
            "properties": {
                p["name"]: {p["type"]: {} if p["type"] != "title" else {}}
                for p in template_json["schema"]["properties"]
            },
        }
        req = urllib.request.Request(
            "https://api.notion.com/v1/databases",
            data=json.dumps(body).encode(),
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "Notion-Version": "2022-06-28",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            return {"pushed": True, "status": r.status}
    except Exception as e:
        return {"pushed": False, "reason": str(e)[:200]}


def build_template(spec: dict, slug: str) -> dict:
    preset_key = spec.get("preset", "crm")
    preset = PRESETS.get(preset_key)
    if not preset:
        return {"slug": slug, "error": f"unknown preset '{preset_key}' (valid: {sorted(PRESETS)})"}

    out_dir = OUTPUTS_DIR / slug
    out_dir.mkdir(parents=True, exist_ok=True)

    (out_dir / "template_spec.md").write_text(_render_spec_md(slug, spec, preset))
    tmpl_json = _render_template_json(spec, preset)
    (out_dir / "template.json").write_text(json.dumps(tmpl_json, indent=2))
    (out_dir / "landing_page.md").write_text(_render_landing(spec, preset))
    (out_dir / "sample_data.csv").write_text(_render_csv(preset))

    push = _try_push_to_notion(tmpl_json)
    return {"slug": slug, "out_dir": str(out_dir), "preset": preset_key, "notion_push": push}


def build_queue() -> dict:
    INPUTS_DIR.mkdir(parents=True, exist_ok=True)
    produced = 0
    failed = 0
    for spec_path in sorted(INPUTS_DIR.iterdir()):
        slug = spec_path.stem
        if (OUTPUTS_DIR / slug / "template.json").exists():
            continue
        if spec_path.suffix == ".json":
            try:
                spec = json.loads(spec_path.read_text())
            except Exception:
                failed += 1
                continue
        elif spec_path.suffix == ".preset":
            preset_name = spec_path.read_text().strip().lower()
            spec = {"preset": preset_name}
        else:
            continue
        r = build_template(spec, slug)
        if "error" in r:
            failed += 1
        else:
            produced += 1
    return {"templates_produced": produced, "failures": failed}


def fulfill_cycle() -> dict:
    subs = storage.load("nt_subscribers.json", [])
    log = storage.load("nt_delivery_log.json", {})
    sent = 0
    for sub in subs:
        if sub.get("status") != "active":
            continue
        email = sub.get("email")
        if not email:
            continue
        already = set(log.get(email, []))
        new_dirs = [d for d in OUTPUTS_DIR.iterdir()
                    if d.is_dir() and d.name not in already
                    and (d / "template.json").exists()]
        if not new_dirs:
            continue
        body_parts = [f"Hi {sub.get('name', 'there')},\n",
                      f"{len(new_dirs)} new Notion template(s) ready:\n"]
        for d in new_dirs[:5]:
            body_parts.append(f"\n--- {d.name} ---")
            body_parts.append(f"  Spec: data/nt_outputs/{d.name}/template_spec.md")
            body_parts.append(f"  Landing copy: data/nt_outputs/{d.name}/landing_page.md")
            body_parts.append(f"  Schema JSON: data/nt_outputs/{d.name}/template.json")
            body_parts.append(f"  Seed data: data/nt_outputs/{d.name}/sample_data.csv")
        body = "\n".join(body_parts) + "\n"
        r = mailer.send(AGENT_KEY, email,
                        f"Notion templates ready — {len(new_dirs)} new",
                        body, purpose="fulfillment")
        if r.get("status") == "sent":
            log[email] = list(already | {d.name for d in new_dirs})
            sent += 1
    storage.save("nt_delivery_log.json", log)
    return {"fulfillment_sent": sent}


def acquire_cycle() -> dict:
    leads = storage.load("nt_leads.json", [])
    available = sorted(PRESETS.keys())
    sent = 0
    for lead in leads:
        if lead.get("trial_sent"):
            continue
        email = lead.get("email")
        if not email:
            continue
        body = (
            f"Hi {lead.get('name', 'there')},\n\n"
            f"I run an autonomous Notion template service. Pick a preset and you get back "
            f"an upload-ready bundle: a build spec, a Gumroad landing-page draft, a JSON schema, "
            f"and seed CSV — within 24 hours.\n\n"
            f"Bundled presets ({len(available)}):\n  "
            + ", ".join(available) + "\n\n"
            f"Tiers:\n"
            f"  $19 per template (one-off)\n"
            f"  $49/mo for 3 templates\n"
            f"  $149/mo unlimited (great if you're building a Notion template store)\n\n"
            f"Reply with the preset you want + a niche (e.g. 'crm for life coaches') "
            f"and I'll send the bundle back.\n"
        )
        r = mailer.send(AGENT_KEY, email,
                        "Free Notion template bundle (your choice)",
                        body, purpose="outreach")
        if r.get("status") == "sent":
            lead["trial_sent"] = datetime.now().isoformat()
            sent += 1
    storage.save("nt_leads.json", leads)
    return {"outreach_sent": sent}


def run_full_cycle() -> dict:
    q = build_queue()
    a = acquire_cycle()
    f = fulfill_cycle()
    rev = billing.revenue_summary(AGENT_KEY)
    subs = storage.load("nt_subscribers.json", [])
    metrics.record(
        AGENT_KEY,
        products_produced=q["templates_produced"],
        outreach_sent=a["outreach_sent"],
        fulfillment_sent=f["fulfillment_sent"],
        active_subs=sum(1 for s in subs if s.get("status") == "active"),
        mrr=rev["mrr"],
        total_revenue=rev["total_paid"],
    )
    return {**q, **a, **f, **rev}
