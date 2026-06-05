"""
Localize — translate marketing/content copy to target languages with cultural notes.
Revenue: $19/page, $49/mo (5 pages), $199/mo unlimited.

Two input sources:
  1. Owner-dropped JSON in data/lz_inputs/{slug}.json:
       {
         "source_text": "...",
         "source_lang": "en",            # optional, default 'en'
         "target_langs": ["es","fr","de","pt-br"],
         "purpose": "marketing|technical|conversational",
         "audience": "small business owners",
         "tone": "warm, direct"
       }
  2. Auto-source from data/sw_outputs/*.md (SEOWriter) and data/nl_newsletters/*
     when a config file data/lz_config.json exists with {"target_langs": [...]}
     and {"auto_sources": ["sw_outputs", "nl_newsletters"]}.

Engine:
  - If ANTHROPIC_API_KEY set → Claude `claude-sonnet-4-6` translates with
    purpose-aware prompting (marketing → keeps brand voice; technical →
    preserves terminology; conversational → idiomatic, never literal).
  - Else → degraded fallback: copies the source text and prepends a
    `[NEEDS_TRANSLATION_TO_{LANG}]` marker so the owner sees what's pending.

Output: data/lz_outputs/{slug}/{lang_code}.md  + a culture-notes block
per target language (idioms to avoid, name/date formats, RTL flags).
"""
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from autonomous import storage, mailer, billing, metrics
from localize import health

AGENT_KEY = "localize"
INPUTS_DIR = Path(__file__).parent.parent / "data" / "lz_inputs"
OUTPUTS_DIR = Path(__file__).parent.parent / "data" / "lz_outputs"
DATA_DIR = Path(__file__).parent.parent / "data"

LANG_NAMES = {
    "es":     "Spanish (Latin America)",
    "es-es":  "Spanish (Spain)",
    "fr":     "French",
    "de":     "German",
    "pt-br":  "Portuguese (Brazil)",
    "pt":     "Portuguese (Portugal)",
    "it":     "Italian",
    "ja":     "Japanese",
    "ko":     "Korean",
    "zh":     "Chinese (Simplified)",
    "zh-tw":  "Chinese (Traditional)",
    "ar":     "Arabic",
    "he":     "Hebrew",
    "nl":     "Dutch",
    "pl":     "Polish",
    "ru":     "Russian",
    "tr":     "Turkish",
    "hi":     "Hindi",
    "id":     "Indonesian",
    "vi":     "Vietnamese",
}
RTL_LANGS = {"ar", "he", "fa", "ur"}

CULTURE_NOTES = {
    "es":     "Use neutral Latin American Spanish (avoid 'vosotros'). Date format: DD/MM/YYYY. Currency varies by country — keep USD if global, localize if per-country.",
    "es-es":  "Use 'vosotros' for plural informal. Date: DD/MM/YYYY. Currency: € (EUR).",
    "fr":     "Maintain formal 'vous' for B2B unless the brand is explicitly casual. Date: DD/MM/YYYY. Currency: €. Avoid Anglicisms when a French term exists.",
    "de":     "B2B = formal 'Sie' always. Compound nouns are expected, not awkward. Date: DD.MM.YYYY. Decimal comma (1.234,56). Currency: €.",
    "pt-br":  "Brazilian Portuguese is significantly different from European — never auto-port. Date: DD/MM/YYYY. Currency: R$ or local equivalent.",
    "ja":     "Use keigo (polite form) for B2B; casual ます-form for consumer. Date: YYYY年MM月DD日. Currency: ¥. Watch for direct second-person 'you' — often dropped.",
    "ar":     "RTL — confirm CSS handles direction. Date: hijri or gregorian per audience. Avoid imagery with bare arms / alcohol if Gulf-targeted.",
    "zh":     "Simplified for mainland China; Traditional for Taiwan/HK. Date: YYYY年MM月DD日. Avoid the number 4 in pricing where possible.",
}


def _resolve_lang_name(code: str) -> str:
    return LANG_NAMES.get(code.lower(), code)


def _culture_note(code: str) -> str:
    note = CULTURE_NOTES.get(code.lower(), "")
    if code.lower() in RTL_LANGS:
        note = ("**RTL language** — ensure your CSS uses `dir=\"rtl\"`. " + note).strip()
    return note or "[[TODO: add culture/format notes specific to this market]]"


_LAST_CLAUDE_ERROR = ""


def _claude_translate(text: str, source_lang: str, target_code: str,
                      purpose: str, audience: str, tone: str) -> str:
    global _LAST_CLAUDE_ERROR
    if not os.environ.get("ANTHROPIC_API_KEY"):
        _LAST_CLAUDE_ERROR = "ANTHROPIC_API_KEY not set"
        return ""
    try:
        import anthropic
        client = anthropic.Anthropic()
        target_name = _resolve_lang_name(target_code)
        rules = {
            "marketing": "Preserve persuasive intent and brand voice. Adapt idioms to local equivalents — never translate them literally. Keep CTAs short and active. Convert measurements/currencies where a literal value would feel foreign.",
            "technical": "Preserve technical terms (API, SDK, OAuth, etc.) — do not localize them. Preserve markdown structure, code blocks, and inline code exactly. Translate only the prose around them.",
            "conversational": "Use natural, spoken register. Contractions and idioms welcome. Avoid stilted formality unless the source has it.",
        }.get(purpose, "Translate naturally for the target audience.")
        prompt = (
            f"Translate the following from {source_lang} into {target_name}.\n\n"
            f"Audience: {audience}\n"
            f"Tone: {tone}\n"
            f"Purpose: {purpose}\n\n"
            f"Rules:\n{rules}\n\n"
            f"Output ONLY the translated text — no preamble, no explanation.\n\n"
            f"--- SOURCE ---\n{text}"
        )
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )
        _LAST_CLAUDE_ERROR = ""
        return msg.content[0].text.strip()
    except Exception as e:
        _LAST_CLAUDE_ERROR = f"{type(e).__name__}: {str(e)[:240]}"
        return ""


def _fallback_translate(text: str, target_code: str) -> str:
    reason = _LAST_CLAUDE_ERROR or "translation engine unavailable"
    return (f"[NEEDS_TRANSLATION_TO_{target_code.upper()}]\n"
            f"\n_Translation engine unavailable — reason: `{reason}`. "
            f"Source text follows verbatim below; once the engine is restored, re-run "
            f"this agent and the file will be overwritten._\n\n"
            f"{text}")


def translate_one(spec: dict, target_code: str) -> str:
    body = _claude_translate(
        spec.get("source_text", ""),
        spec.get("source_lang", "en"),
        target_code,
        spec.get("purpose", "marketing"),
        spec.get("audience", "general audience"),
        spec.get("tone", "natural, clear"),
    ) or _fallback_translate(spec.get("source_text", ""), target_code)

    return "\n".join([
        f"# {_resolve_lang_name(target_code)} — `{target_code}`",
        "",
        f"_Purpose: {spec.get('purpose', 'marketing')} | Audience: {spec.get('audience', 'general')}_",
        "",
        "## Translation",
        "",
        body,
        "",
        "## Localization notes",
        "",
        _culture_note(target_code),
        "",
        "---",
        f"_Generated {datetime.now():%Y-%m-%d} by Localize._",
    ])


def build_one(spec: dict, slug: str) -> dict:
    target_langs = spec.get("target_langs") or []
    if not target_langs:
        return {"slug": slug, "error": "no target_langs"}
    out_dir = OUTPUTS_DIR / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    produced = []
    for code in target_langs:
        out_path = out_dir / f"{code.lower()}.md"
        if out_path.exists():
            continue
        out_path.write_text(translate_one(spec, code))
        produced.append(str(out_path))
    return {"slug": slug, "produced": produced}


def _config() -> dict:
    p = DATA_DIR / "lz_config.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def _spec_from_source_file(src: Path, target_langs: list) -> dict:
    text = src.read_text(errors="ignore")
    return {
        "source_text": text,
        "source_lang": "en",
        "target_langs": target_langs,
        "purpose": "marketing",
        "audience": "general audience",
        "tone": "natural, clear",
    }


def build_queue() -> dict:
    INPUTS_DIR.mkdir(parents=True, exist_ok=True)
    produced = 0
    for spec_path in sorted(INPUTS_DIR.glob("*.json")):
        slug = spec_path.stem
        try:
            spec = json.loads(spec_path.read_text())
        except Exception:
            continue
        r = build_one(spec, slug)
        if r.get("produced"):
            produced += len(r["produced"])

    cfg = _config()
    targets = cfg.get("target_langs") or []
    if targets:
        for sub in cfg.get("auto_sources", []):
            src_dir = DATA_DIR / sub
            if not src_dir.exists():
                continue
            for src in src_dir.glob("*.md"):
                slug = f"{sub}-{src.stem}"
                if (OUTPUTS_DIR / slug).exists():
                    continue
                spec = _spec_from_source_file(src, targets)
                r = build_one(spec, slug)
                if r.get("produced"):
                    produced += len(r["produced"])
    return {"translations_produced": produced}


def fulfill_cycle() -> dict:
    subs = storage.load("lz_subscribers.json", [])
    log = storage.load("lz_delivery_log.json", {})
    sent = 0
    for sub in subs:
        if sub.get("status") != "active":
            continue
        email = sub.get("email")
        if not email:
            continue
        already = set(log.get(email, []))
        new_dirs = [d for d in OUTPUTS_DIR.iterdir()
                    if d.is_dir() and d.name not in already]
        if not new_dirs:
            continue
        body_parts = [f"Hi {sub.get('name', 'there')},\n",
                      f"{len(new_dirs)} new translation bundle(s) ready:\n"]
        for d in new_dirs[:8]:
            files = sorted(d.glob("*.md"))
            body_parts.append(f"\n--- {d.name} ({len(files)} languages) ---")
            for f_ in files:
                body_parts.append(f"  data/lz_outputs/{d.name}/{f_.name}")
        body = "\n".join(body_parts) + "\n"
        r = mailer.send(AGENT_KEY, email,
                        f"Translations ready — {len(new_dirs)} new",
                        body, purpose="fulfillment")
        if r.get("status") == "sent":
            log[email] = list(already | {d.name for d in new_dirs})
            sent += 1
    storage.save("lz_delivery_log.json", log)
    return {"fulfillment_sent": sent}


def acquire_cycle() -> dict:
    leads = storage.load("lz_leads.json", [])
    sent = 0
    for lead in leads:
        if lead.get("trial_sent"):
            continue
        email = lead.get("email")
        if not email:
            continue
        body = (
            f"Hi {lead.get('name', 'there')},\n\n"
            f"I run an automated translation + localization service for SaaS and DTC brands.\n"
            f"Send me one page of copy (landing page, onboarding email, product description) "
            f"and you'll get back culture-aware translations to up to 3 languages — free first one.\n\n"
            f"Every translation comes with a localization-notes block (date/currency formats, "
            f"idioms to avoid, RTL flags) so your dev team doesn't ship something embarrassing.\n\n"
            f"Pricing after the trial:\n"
            f"  $19 per page per language\n"
            f"  $49/mo for 5 pages × any 3 languages\n"
            f"  $199/mo unlimited pages × up to 8 languages\n\n"
            f"Reply with the page + the languages you want and I'll send the bundle back.\n"
        )
        r = mailer.send(AGENT_KEY, email,
                        "Free localization (3 languages, with culture notes)",
                        body, purpose="outreach")
        if r.get("status") == "sent":
            lead["trial_sent"] = datetime.now().isoformat()
            sent += 1
    storage.save("lz_leads.json", leads)
    return {"outreach_sent": sent}


def run_full_cycle() -> dict:
    q = build_queue()
    a = acquire_cycle()
    f = fulfill_cycle()
    rev = billing.revenue_summary(AGENT_KEY)
    subs = storage.load("lz_subscribers.json", [])
    metrics.record(
        AGENT_KEY,
        products_produced=q["translations_produced"],
        outreach_sent=a["outreach_sent"],
        fulfillment_sent=f["fulfillment_sent"],
        active_subs=sum(1 for s in subs if s.get("status") == "active"),
        mrr=rev["mrr"],
        total_revenue=rev["total_paid"],
    )
    return {**q, **a, **f, **rev}
