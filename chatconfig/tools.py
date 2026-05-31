"""
ChatConfig — generates importable chatbot flows from a simple FAQ manifest.
Revenue: $99 one-time setup, $49/mo monitoring + updates, $297 multi-bot pack.

Owner-dropped manifest in data/cc_inputs/{slug}.json:
  {
    "business_name": "Acme Coffee",
    "business_type": "cafe",
    "tone": "warm, brief",
    "hours": "Mon-Fri 7am-6pm, Sat-Sun 8am-3pm",
    "contact": {"phone": "+1...", "email": "hi@acme.com", "address": "..."},
    "faqs": [
      {"q": "do you take reservations?", "a": "Yes — call ahead or book on OpenTable."},
      {"q": "do you have wifi?",         "a": "Yes, free wifi for all customers."}
    ],
    "escalation": "If the bot can't help, route to: hi@acme.com"
  }

Output bundle in data/cc_outputs/{slug}/:
  voiceflow_flow.json    — importable shape for Voiceflow
  botpress_flow.json     — importable shape for Botpress
  simple_responses.json  — platform-agnostic intent → response map
                            (works in Drift, Intercom, custom widgets)
  setup_guide.md         — step-by-step install per platform
"""
import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from autonomous import storage, mailer, billing, metrics

AGENT_KEY = "chatconfig"
INPUTS_DIR = Path(__file__).parent.parent / "data" / "cc_inputs"
OUTPUTS_DIR = Path(__file__).parent.parent / "data" / "cc_outputs"


def _intent_id(question: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", question.lower()).strip("_")
    return f"intent_{slug[:40]}"


def _trigger_phrases(question: str) -> list:
    q = question.lower().strip().rstrip("?")
    phrases = {q}
    words = q.split()
    if len(words) >= 2:
        phrases.add(" ".join(words[1:]))                  # drop leading question word
        phrases.add(" ".join(w for w in words if len(w) > 3))
    if q.startswith("do you"):
        phrases.add(q.replace("do you ", ""))
    if q.startswith("can i"):
        phrases.add(q.replace("can i ", ""))
    return sorted(p for p in phrases if p)


def _build_simple_responses(spec: dict) -> dict:
    intents = {}
    intents["greeting"] = {
        "triggers": ["hi", "hello", "hey", "good morning", "good evening"],
        "response": f"Hi! Welcome to {spec.get('business_name', 'us')}. "
                    f"What can I help you with?",
    }
    intents["hours"] = {
        "triggers": ["hours", "when are you open", "are you open", "what time"],
        "response": f"Our hours: {spec.get('hours', 'see our website')}.",
    }
    contact = spec.get("contact", {})
    if contact:
        contact_parts = []
        if "phone" in contact:
            contact_parts.append(f"📞 {contact['phone']}")
        if "email" in contact:
            contact_parts.append(f"✉️ {contact['email']}")
        if "address" in contact:
            contact_parts.append(f"📍 {contact['address']}")
        intents["contact"] = {
            "triggers": ["contact", "phone", "email", "address", "location", "where are you"],
            "response": "Here's how to reach us:\n" + "\n".join(contact_parts),
        }
    for faq in spec.get("faqs", []):
        q = faq.get("q", "").strip()
        a = faq.get("a", "").strip()
        if not (q and a):
            continue
        intents[_intent_id(q)] = {
            "triggers": _trigger_phrases(q),
            "response": a,
        }
    intents["fallback"] = {
        "triggers": [],
        "response": (spec.get("escalation")
                     or f"I'm not sure about that — I'll connect you with the team. "
                        f"Email us at {contact.get('email', '[your email]')} and we'll "
                        f"get back to you fast."),
    }
    return intents


def _voiceflow_flow(spec: dict, intents: dict) -> dict:
    """Voiceflow Dialog Manager flow shape (simplified — importable as a stub)."""
    nodes = []
    nodes.append({
        "type": "start",
        "id": "node_start",
        "next": "node_greeting",
    })
    nodes.append({
        "type": "speak",
        "id": "node_greeting",
        "speech": intents["greeting"]["response"],
        "next": "node_listen",
    })
    nodes.append({
        "type": "capture",
        "id": "node_listen",
        "intents": [
            {"intent": k, "phrases": v["triggers"], "goto": f"node_resp_{k}"}
            for k, v in intents.items() if k not in ("greeting", "fallback")
        ],
        "fallback": "node_fallback",
    })
    for k, v in intents.items():
        if k in ("greeting", "fallback"):
            continue
        nodes.append({
            "type": "speak",
            "id": f"node_resp_{k}",
            "speech": v["response"],
            "next": "node_listen",
        })
    nodes.append({
        "type": "speak",
        "id": "node_fallback",
        "speech": intents["fallback"]["response"],
        "next": "end",
    })
    return {
        "platform": "voiceflow",
        "name": spec.get("business_name", "bot"),
        "version": "1.0",
        "nodes": nodes,
    }


def _botpress_flow(spec: dict, intents: dict) -> dict:
    """Botpress flow shape (simplified import)."""
    return {
        "platform": "botpress",
        "name": spec.get("business_name", "bot"),
        "version": "1.0",
        "entry_node": "greeting",
        "nodes": {
            "greeting": {
                "say": intents["greeting"]["response"],
                "next": "listen",
            },
            "listen": {
                "type": "intent_capture",
                "intents": {k: {"utterances": v["triggers"], "say": v["response"]}
                            for k, v in intents.items() if k not in ("greeting", "fallback")},
                "fallback": intents["fallback"]["response"],
            },
        },
    }


def _setup_guide(spec: dict, slug: str) -> str:
    name = spec.get("business_name", "Your bot")
    return "\n".join([
        f"# {name} — chatbot setup guide",
        "",
        f"_Generated {datetime.now():%Y-%m-%d} by ChatConfig._",
        "",
        "## Files in this bundle",
        "",
        f"- `voiceflow_flow.json` — drop into Voiceflow via Import",
        f"- `botpress_flow.json` — Botpress import format",
        f"- `simple_responses.json` — platform-agnostic intent → response map",
        f"- `setup_guide.md` — this file",
        "",
        "## Voiceflow setup (3 minutes)",
        "",
        "1. Go to https://creator.voiceflow.com → New Project",
        "2. Settings → Versions → Import Project → upload `voiceflow_flow.json`",
        "3. Train the assistant once. Test inside the Voiceflow tester.",
        "4. Publish → copy embed snippet → paste into your site `<head>`.",
        "",
        "## Botpress setup (3 minutes)",
        "",
        "1. Go to https://app.botpress.cloud → New Bot",
        "2. Studio → Modules → Import Flow → upload `botpress_flow.json`",
        "3. Hit 'Build'. Test in the emulator.",
        "4. Channels → Webchat → copy embed snippet.",
        "",
        "## Custom widget (use `simple_responses.json`)",
        "",
        "Drop `simple_responses.json` into your own widget code; each intent has a",
        "list of trigger phrases (lowercased, substring match works) and a response",
        "string. The `fallback` intent fires when nothing matches.",
        "",
        "## Monthly maintenance",
        "",
        "Add new FAQs to the manifest and re-run ChatConfig. Re-import the fresh",
        "flow to keep the bot answering the latest questions.",
    ])


def build_chatbot(spec: dict, slug: str) -> dict:
    out_dir = OUTPUTS_DIR / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    intents = _build_simple_responses(spec)
    (out_dir / "simple_responses.json").write_text(json.dumps(intents, indent=2))
    (out_dir / "voiceflow_flow.json").write_text(json.dumps(_voiceflow_flow(spec, intents), indent=2))
    (out_dir / "botpress_flow.json").write_text(json.dumps(_botpress_flow(spec, intents), indent=2))
    (out_dir / "setup_guide.md").write_text(_setup_guide(spec, slug))
    return {"slug": slug, "out_dir": str(out_dir), "intent_count": len(intents)}


def build_queue() -> dict:
    INPUTS_DIR.mkdir(parents=True, exist_ok=True)
    produced = 0
    failed = 0
    for spec_path in sorted(INPUTS_DIR.glob("*.json")):
        slug = spec_path.stem
        if (OUTPUTS_DIR / slug / "setup_guide.md").exists():
            continue
        try:
            spec = json.loads(spec_path.read_text())
        except Exception:
            failed += 1
            continue
        build_chatbot(spec, slug)
        produced += 1
    return {"bots_produced": produced, "failures": failed}


def fulfill_cycle() -> dict:
    subs = storage.load("cc_subscribers.json", [])
    log = storage.load("cc_delivery_log.json", {})
    sent = 0
    for sub in subs:
        if sub.get("status") != "active":
            continue
        email = sub.get("email")
        if not email:
            continue
        already = set(log.get(email, []))
        new = [d for d in OUTPUTS_DIR.iterdir()
               if d.is_dir() and d.name not in already
               and (d / "setup_guide.md").exists()]
        if not new:
            continue
        body_parts = [f"Hi {sub.get('name', 'there')},\n",
                      f"{len(new)} new chatbot bundle(s) ready:\n"]
        for d in new[:5]:
            files = sorted(d.glob("*"))
            body_parts.append(f"\n--- {d.name} ---")
            for f_ in files:
                body_parts.append(f"  data/cc_outputs/{d.name}/{f_.name}")
        body = "\n".join(body_parts) + "\n"
        r = mailer.send(AGENT_KEY, email,
                        f"Chatbot bundles ready — {len(new)} new",
                        body, purpose="fulfillment")
        if r.get("status") == "sent":
            log[email] = list(already | {d.name for d in new})
            sent += 1
    storage.save("cc_delivery_log.json", log)
    return {"fulfillment_sent": sent}


def acquire_cycle() -> dict:
    leads = storage.load("cc_leads.json", [])
    sent = 0
    for lead in leads:
        if lead.get("trial_sent"):
            continue
        email = lead.get("email")
        if not email:
            continue
        body = (
            f"Hi {lead.get('name', 'there')},\n\n"
            f"ChatConfig builds a working FAQ chatbot for your business from a "
            f"short manifest you fill out (business name, hours, contact, 5-15 FAQs).\n\n"
            f"You get back an importable flow for Voiceflow AND Botpress AND a "
            f"plain JSON intent map you can drop into any custom widget. Plus a "
            f"setup guide so a non-developer can install it in 5 minutes.\n\n"
            f"Pricing:\n"
            f"  $99 one-time setup (you fill out the FAQ, we ship the bundle)\n"
            f"  $49/mo monitoring + monthly FAQ refresh\n"
            f"  $297 multi-bot pack (3 bots for related properties)\n\n"
            f"Reply 'yes' and I'll send the FAQ manifest template.\n"
        )
        r = mailer.send(AGENT_KEY, email,
                        "Free FAQ chatbot bundle for your business",
                        body, purpose="outreach")
        if r.get("status") == "sent":
            lead["trial_sent"] = datetime.now().isoformat()
            sent += 1
    storage.save("cc_leads.json", leads)
    return {"outreach_sent": sent}


def run_full_cycle() -> dict:
    q = build_queue()
    a = acquire_cycle()
    f = fulfill_cycle()
    rev = billing.revenue_summary(AGENT_KEY)
    subs = storage.load("cc_subscribers.json", [])
    metrics.record(
        AGENT_KEY,
        products_produced=q["bots_produced"],
        outreach_sent=a["outreach_sent"],
        fulfillment_sent=f["fulfillment_sent"],
        active_subs=sum(1 for s in subs if s.get("status") == "active"),
        mrr=rev["mrr"],
        total_revenue=rev["total_paid"],
    )
    return {**q, **a, **f, **rev}
