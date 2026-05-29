"""
StoryForge — writers' coaching agent.
Revenue: $19/mo single project, $49/mo unlimited, $197 full story bible.
"""
import random
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from autonomous import storage, mailer, billing, metrics

AGENT_KEY = "storyforge"
PROJECTS_DIR = Path(__file__).parent.parent / "data" / "sf_projects"


BEAT_3ACT = [
    ("Opening Image", "The protagonist's status quo before the story begins."),
    ("Inciting Incident", "The event that disrupts the status quo."),
    ("Plot Point 1", "Protagonist commits to pursuing the goal."),
    ("Midpoint", "Stakes raise; the protagonist's understanding shifts."),
    ("Plot Point 2", "All seems lost — false defeat."),
    ("Climax", "Protagonist confronts the antagonist directly."),
    ("Resolution", "The new normal."),
]


PROMPT_BANK = [
    "Write the moment your protagonist realizes they were wrong about the antagonist's motive.",
    "Show, don't tell, your protagonist's deepest fear without ever naming it.",
    "Rewrite your opening paragraph in present tense, then in past tense — keep the one with stronger pull.",
    "Give your antagonist a moment of genuine kindness toward someone else.",
    "Write a scene where your protagonist gets what they want and immediately regrets it.",
    "Cut your last chapter in half. What survives?",
    "Add a physical object that recurs three times across the story.",
    "Write your protagonist's worst conversation with the character they love most.",
    "Describe your setting through your antagonist's eyes.",
    "Write a scene that contradicts what your protagonist believes about themselves.",
]


def build_story_bible(project_name: str, brief: dict) -> dict:
    project_dir = PROJECTS_DIR / project_name
    project_dir.mkdir(parents=True, exist_ok=True)

    # Character bible
    char_bible = [f"# Character Bible — {project_name}\n"]
    char_bible.append(f"## Protagonist: {brief.get('protagonist', 'Unnamed')}")
    char_bible.append(f"- **Want:** {brief.get('protag_want', 'Define explicitly.')}")
    char_bible.append(f"- **Need:** {brief.get('protag_need', 'Define explicitly.')}")
    char_bible.append(f"- **Wound:** {brief.get('protag_wound', 'Define the past event that shaped them.')}\n")
    char_bible.append(f"## Antagonist")
    char_bible.append(f"- **Want:** {brief.get('antag_want', 'Define explicitly.')}")
    char_bible.append(f"- **Justification:** {brief.get('antag_justification', 'Why do they believe they are right?')}")
    (project_dir / "character_bible.md").write_text("\n".join(char_bible))

    # Beat sheet
    beats = [f"# Beat Sheet — {project_name}\n"]
    for name, desc in BEAT_3ACT:
        beats.append(f"## {name}")
        beats.append(f"_{desc}_\n")
        beats.append("_[Your draft here]_\n")
    (project_dir / "beat_sheet.md").write_text("\n".join(beats))

    # Scene outline scaffold
    scenes = [f"# Scene Outline — {project_name}\n"]
    for i in range(1, 13):
        scenes.append(f"### Scene {i}")
        scenes.append("- Location: ")
        scenes.append("- POV: ")
        scenes.append("- Scene goal: ")
        scenes.append("- Outcome: ")
        scenes.append("- What changes: \n")
    (project_dir / "scene_outline.md").write_text("\n".join(scenes))

    return {"project": project_name, "files": 3}


def consistency_pass(project_name: str) -> str:
    project_dir = PROJECTS_DIR / project_name
    if not project_dir.exists():
        return ""
    bible = (project_dir / "character_bible.md").read_text() if (project_dir / "character_bible.md").exists() else ""
    beats = (project_dir / "beat_sheet.md").read_text() if (project_dir / "beat_sheet.md").exists() else ""
    flags = []
    # Crude consistency checks
    if "Want:" in bible and "want" not in beats.lower():
        flags.append("Protagonist's stated **want** does not appear in beat sheet — risk of unclear motivation.")
    if "Wound:" in bible and "wound" not in beats.lower() and "past" not in beats.lower():
        flags.append("Protagonist's **wound** never surfaces in beats — consider when it manifests.")
    if "Antagonist" in bible and beats.count("Antagonist") < 2:
        flags.append("Antagonist appears in <2 beats — risk of a flat opposition force.")
    if not flags:
        flags.append("No major continuity issues detected. Tighten scene-level prose.")
    report = "# Consistency Report\n\n" + "\n".join(f"- {f}" for f in flags)
    (project_dir / "consistency_report.md").write_text(report)
    return report


def daily_prompts() -> dict:
    subs = storage.load("sf_subscribers.json", [])
    sent = 0
    for s in subs:
        if s.get("status") != "active" or not s.get("daily_prompt"):
            continue
        prompt = random.choice(PROMPT_BANK)
        body = (
            f"Good morning,\n\n"
            f"Today's writing prompt for {s.get('project', 'your project')}:\n\n"
            f"> {prompt}\n\n"
            f"Spend 25 minutes. Don't edit yet.\n\n"
            f"— StoryForge"
        )
        result = mailer.send(AGENT_KEY, s["email"],
                             "StoryForge — today's prompt",
                             body, purpose="fulfillment")
        if result.get("status") == "sent":
            sent += 1
    return {"prompts_sent": sent}


def weekly_consistency() -> dict:
    subs = storage.load("sf_subscribers.json", [])
    sent = 0
    for s in subs:
        if s.get("status") != "active":
            continue
        report = consistency_pass(s.get("project", ""))
        if not report:
            continue
        result = mailer.send(AGENT_KEY, s["email"],
                             f"StoryForge — consistency report ({datetime.now():%b %d})",
                             report, purpose="fulfillment")
        if result.get("status") == "sent":
            sent += 1
    return {"consistency_sent": sent}


def fulfill_orders() -> dict:
    orders = storage.load("sf_orders.json", [])
    sent = 0
    for o in orders:
        if o.get("status") != "paid" or o.get("delivered_at"):
            continue
        result = build_story_bible(o["project"], o.get("brief", {}))
        proj_dir = PROJECTS_DIR / o["project"]
        attachments = [str(p) for p in proj_dir.glob("*.md")]
        body = (
            f"Hi {o.get('writer_name', 'there')},\n\n"
            f"Your story bible for {o['project']} is attached:\n"
            f"- Character bible\n- Beat sheet\n- Scene outline\n\n"
            f"Fill in the prompts; reply when you want a consistency pass.\n\n"
            f"— StoryForge"
        )
        r2 = mailer.send(AGENT_KEY, o["email"],
                         f"Your story bible — {o['project']}",
                         body, purpose="fulfillment", attachments=attachments)
        if r2.get("status") == "sent":
            o["status"] = "delivered"
            o["delivered_at"] = datetime.now().isoformat()
            sent += 1
    storage.save("sf_orders.json", orders)
    return {"bibles_delivered": sent}


def run_full_cycle() -> dict:
    d = daily_prompts()
    w = weekly_consistency() if datetime.now().weekday() == 6 else {"consistency_sent": 0}
    o = fulfill_orders()
    rev = billing.revenue_summary(AGENT_KEY)
    subs = storage.load("sf_subscribers.json", [])
    metrics.record(
        AGENT_KEY,
        fulfillment_sent=d["prompts_sent"] + w["consistency_sent"] + o["bibles_delivered"],
        active_subs=sum(1 for s in subs if s.get("status") == "active"),
        mrr=rev["mrr"],
        total_revenue=rev["total_paid"],
    )
    return {**d, **w, **o, **rev}
