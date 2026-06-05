"""
CourseForge — packages existing agent outputs into upload-ready mini-courses.
Revenue: $29 self-publish kit, $99 done-for-you, $297/mo white-label.

Owner-dropped manifest in data/co_inputs/{slug}.json:
  {
    "title": "The 7-Day Podcast Show Notes System",
    "subtitle": "Turn raw transcripts into SEO-ready show notes",
    "price": 29,
    "platform": "gumroad|udemy|payhip",
    "audience": "podcasters and content creators",
    "modules": [
      {"lesson": "Module 1: Why show notes drive search traffic",
       "source": "sn_outputs/episode-1.md"},
      {"lesson": "Module 2: Building your transcript pipeline",
       "source": "tr_outputs/episode-1.txt"},
      {"lesson": "Module 3: Writing the perfect TL;DR",
       "source": "sw_outputs/perfect-tldr.md"}
    ]
  }

Source paths are resolved relative to data/. CourseForge reads each source,
wraps it in a standardized lesson template (intro, content, key takeaways,
action item), and emits a complete course package:

  data/co_outputs/{slug}/
    README.md                  ← full course doc with TOC
    landing_page.md            ← Gumroad/Payhip-ready copy
    module_01.md ... module_NN.md
    manifest.json              ← metadata for the upload step

If a thumbnail spec is provided ({"thumbnail": {...}}) it routes through
ThumbForge for the cover image.
"""
import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from autonomous import storage, mailer, billing, metrics
from courseforge import health

AGENT_KEY = "courseforge"
INPUTS_DIR = Path(__file__).parent.parent / "data" / "co_inputs"
OUTPUTS_DIR = Path(__file__).parent.parent / "data" / "co_outputs"
DATA_DIR = Path(__file__).parent.parent / "data"


def _resolve_source(source: str) -> Path:
    p = DATA_DIR / source
    return p if p.exists() else Path("")


def _first_paragraph(text: str, limit: int = 280) -> str:
    cleaned = re.sub(r"^#.*$", "", text, flags=re.M).strip()
    paras = [p.strip() for p in cleaned.split("\n\n") if p.strip()]
    if not paras:
        return ""
    return paras[0][:limit].rstrip() + ("…" if len(paras[0]) > limit else "")


def _key_lines(text: str, n: int = 4) -> list:
    bullets = re.findall(r"^\s*-\s+(.+)$", text, re.M)
    if bullets:
        return bullets[:n]
    sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text)
             if len(s.strip()) > 20]
    return sents[:n]


def _render_lesson(idx: int, total: int, lesson_title: str, source_text: str) -> str:
    intro = _first_paragraph(source_text)
    takeaways = _key_lines(source_text)
    body = source_text.strip()
    lines = [
        f"# {lesson_title}",
        "",
        f"_Lesson {idx} of {total}_",
        "",
        "## What you'll learn",
        intro or "[[TODO: 2-sentence overview of this lesson]]",
        "",
        "## Lesson content",
        "",
        body,
        "",
        "## Key takeaways",
    ]
    for t in takeaways:
        lines.append(f"- {t}")
    if not takeaways:
        lines.append("- [[TODO: 3-5 key takeaways from this lesson]]")
    lines.append("")
    lines.append("## Action item")
    lines.append(f"Apply one specific thing from this lesson in the next 24 hours. "
                 f"Write down what you did and what you learned.")
    lines.append("")
    lines.append("---")
    lines.append(f"_Next: continue to lesson {idx + 1 if idx < total else 'wrap-up'}._")
    return "\n".join(lines)


def _render_readme(spec: dict, modules: list) -> str:
    lines = [
        f"# {spec.get('title', 'Untitled Course')}",
        "",
        spec.get("subtitle", ""),
        "",
        f"**Price:** ${spec.get('price', 29)}",
        f"**Audience:** {spec.get('audience', 'creators and operators')}",
        f"**Platform:** {spec.get('platform', 'gumroad')}",
        f"**Lessons:** {len(modules)}",
        "",
        "## Table of contents",
        "",
    ]
    for i, m in enumerate(modules, 1):
        lines.append(f"{i}. [{m['lesson']}](module_{i:02d}.md)")
    lines.append("")
    lines.append("## How to use this course")
    lines.append("")
    lines.append("Work through one lesson per day. Each lesson ends with an action "
                 "item — complete it before moving on. The course compounds if you "
                 "do the work.")
    lines.append("")
    lines.append("---")
    lines.append(f"_Packaged by CourseForge on {datetime.now():%Y-%m-%d}._")
    return "\n".join(lines)


def _render_landing(spec: dict, modules: list) -> str:
    title = spec.get("title", "Untitled Course")
    sub = spec.get("subtitle", "")
    audience = spec.get("audience", "people who want to ship faster")
    price = spec.get("price", 29)
    bullets = []
    for m in modules[:8]:
        bullets.append(f"- {m['lesson']}")
    return "\n".join([
        f"# {title}",
        "",
        f"**{sub}**",
        "",
        f"For: {audience}",
        "",
        "## What's inside",
        "",
        f"{len(modules)} lessons covering:",
        "",
        *bullets,
        "",
        "## What you'll walk away with",
        "",
        "- A working system you can apply the same day",
        "- A repeatable checklist instead of a vague theory",
        "- Templates you can copy and modify",
        "",
        "## Pricing",
        "",
        f"**${price}** one-time. Lifetime access. No upsells.",
        "",
        "[Buy now →]([[INSERT_GUMROAD_LINK]])",
        "",
        "---",
        "30-day refund. No questions asked.",
    ])


def _render_manifest(spec: dict, modules: list, out_dir: Path) -> dict:
    return {
        "title": spec.get("title"),
        "subtitle": spec.get("subtitle"),
        "price_usd": spec.get("price", 29),
        "platform": spec.get("platform", "gumroad"),
        "lesson_count": len(modules),
        "audience": spec.get("audience"),
        "files": [f"module_{i:02d}.md" for i in range(1, len(modules) + 1)] + [
            "README.md", "landing_page.md"],
        "packaged_at": datetime.now().isoformat(),
    }


def build_course(spec: dict, slug: str) -> dict:
    modules = spec.get("modules", [])
    if not modules:
        return {"slug": slug, "error": "no modules in manifest"}
    out_dir = OUTPUTS_DIR / slug
    out_dir.mkdir(parents=True, exist_ok=True)

    rendered = []
    missing = []
    for i, m in enumerate(modules, 1):
        src_path = _resolve_source(m.get("source", ""))
        if not src_path:
            missing.append(m.get("source"))
            source_text = "[[TODO: source file not found — paste lesson content here]]"
        else:
            source_text = src_path.read_text(errors="ignore")
        lesson_md = _render_lesson(i, len(modules), m.get("lesson", f"Lesson {i}"), source_text)
        (out_dir / f"module_{i:02d}.md").write_text(lesson_md)
        rendered.append(f"module_{i:02d}.md")

    (out_dir / "README.md").write_text(_render_readme(spec, modules))
    (out_dir / "landing_page.md").write_text(_render_landing(spec, modules))
    manifest = _render_manifest(spec, modules, out_dir)
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))

    return {"slug": slug, "out_dir": str(out_dir), "lessons": len(rendered),
            "missing_sources": missing}


def build_queue() -> dict:
    INPUTS_DIR.mkdir(parents=True, exist_ok=True)
    produced = 0
    failed = 0
    for spec_path in sorted(INPUTS_DIR.glob("*.json")):
        slug = spec_path.stem
        if (OUTPUTS_DIR / slug / "manifest.json").exists():
            continue
        try:
            spec = json.loads(spec_path.read_text())
        except Exception:
            failed += 1
            continue
        r = build_course(spec, slug)
        if "error" in r:
            failed += 1
        else:
            produced += 1
    return {"courses_produced": produced, "failures": failed}


def fulfill_cycle() -> dict:
    subs = storage.load("co_subscribers.json", [])
    log = storage.load("co_delivery_log.json", {})
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
                    and (d / "manifest.json").exists()]
        if not new_dirs:
            continue
        body_parts = [f"Hi {sub.get('name', 'there')},\n",
                      f"{len(new_dirs)} new course package(s) ready to upload:\n"]
        for d in new_dirs[:5]:
            try:
                m = json.loads((d / "manifest.json").read_text())
            except Exception:
                continue
            body_parts.append(f"\n--- {m.get('title', d.name)} ---")
            body_parts.append(f"  {m.get('lesson_count')} lessons | ${m.get('price_usd')} | "
                              f"{m.get('platform')}")
            body_parts.append(f"  Folder: data/co_outputs/{d.name}/")
            body_parts.append(f"  Upload checklist: README.md + landing_page.md + all module_*.md")
        body = "\n".join(body_parts) + "\n"
        r = mailer.send(AGENT_KEY, email,
                        f"Course packages ready — {len(new_dirs)} new",
                        body, purpose="fulfillment")
        if r.get("status") == "sent":
            log[email] = list(already | {d.name for d in new_dirs})
            sent += 1
    storage.save("co_delivery_log.json", log)
    return {"fulfillment_sent": sent}


def acquire_cycle() -> dict:
    leads = storage.load("co_leads.json", [])
    sent = 0
    for lead in leads:
        if lead.get("trial_sent"):
            continue
        email = lead.get("email")
        if not email:
            continue
        body = (
            f"Hi {lead.get('name', 'there')},\n\n"
            f"CourseForge packages your existing content (blog posts, transcripts, "
            f"newsletter archives) into upload-ready mini-courses for Gumroad, "
            f"Payhip, or Udemy.\n\n"
            f"Send me 3-8 pieces of content you've already written and you'll get "
            f"back a complete course folder — README, landing page, lesson files, "
            f"and a manifest — within 48 hours. First one free.\n\n"
            f"Pricing after the trial:\n"
            f"  $29 self-publish kit (you upload it)\n"
            f"  $99 done-for-you (we upload + write the listing)\n"
            f"  $297/mo white-label (we package + maintain monthly)\n\n"
            f"Reply with links to 3-8 pieces of content and a title idea.\n"
        )
        r = mailer.send(AGENT_KEY, email,
                        "Free: package your old content into a mini-course",
                        body, purpose="outreach")
        if r.get("status") == "sent":
            lead["trial_sent"] = datetime.now().isoformat()
            sent += 1
    storage.save("co_leads.json", leads)
    return {"outreach_sent": sent}


def run_full_cycle() -> dict:
    q = build_queue()
    a = acquire_cycle()
    f = fulfill_cycle()
    rev = billing.revenue_summary(AGENT_KEY)
    subs = storage.load("co_subscribers.json", [])
    metrics.record(
        AGENT_KEY,
        products_produced=q["courses_produced"],
        outreach_sent=a["outreach_sent"],
        fulfillment_sent=f["fulfillment_sent"],
        active_subs=sum(1 for s in subs if s.get("status") == "active"),
        mrr=rev["mrr"],
        total_revenue=rev["total_paid"],
    )
    return {**q, **a, **f, **rev}
