"""
ColdCaller — autonomous cold-call queue builder for the wholesale pipeline.

Pulls phone-having prospects from data/ps_leads.json and data/leads.json,
filters toll-free junk and known DNCs, dedupes against the call log,
generates a personalized cold-call script per prospect, and emits a
daily queue HTML page with Google Voice click-to-call links + tel: fallback.
The owner gets the queue emailed to them; one tap per prospect dials from
their Google Voice number (207-385-4041 stays as caller-ID).

Google Voice has no public outbound API. The click-to-call URL
https://voice.google.com/u/0/calls?a=nc,%2B1XXXXXXXXXX is the only
reliable way to dial from a Google Voice number programmatically.
For true autonomous dialing, swap to Twilio (different number) — see
the README for the alternate path.

Entry point: run_full_cycle()
"""
import json
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from autonomous import storage, mailer, billing, metrics

AGENT_KEY = "coldcaller"
DATA_DIR = Path(__file__).parent.parent / "data"
QUEUE_DIR = DATA_DIR / "cd_queues"
LOG_FILE = "cd_calls.json"            # storage.load/save key
DNC_FILE = "cd_dnc.json"              # owner-maintained do-not-call list
OWNER_GV_NUMBER = "207-385-4041"      # Google Voice number (caller-ID)
OWNER_NAME = "Ty"

# Phone number-prefix blacklist — toll-free and shared business lines that
# can never belong to a residential property owner. Skip-trace sites
# commonly leak these from the page footer.
TOLLFREE_PREFIXES = {"800", "888", "877", "866", "855", "844", "833", "822"}


def _digits(phone: str) -> str:
    return re.sub(r"\D", "", phone or "")


def _normalize(phone: str) -> str:
    """Return E.164 +1XXXXXXXXXX or '' if not a 10-digit US number."""
    d = _digits(phone)
    if len(d) == 11 and d.startswith("1"):
        d = d[1:]
    if len(d) != 10:
        return ""
    return f"+1{d}"


def _is_usable(phone_e164: str) -> bool:
    """Reject toll-free, 555-fakes, area codes that don't exist."""
    if not phone_e164.startswith("+1") or len(phone_e164) != 12:
        return False
    area = phone_e164[2:5]
    if area in TOLLFREE_PREFIXES:
        return False
    if area.startswith("0") or area.startswith("1"):
        return False  # invalid area codes
    if phone_e164[5:8] == "555":
        return False  # 555 directory-info / movie fakes
    return True


def _gv_link(phone_e164: str) -> str:
    """Click-to-call URL that opens Google Voice and dials the number from
    the owner's Google Voice line. Works on mobile (Voice app) and desktop
    (web). The %2B is URL-encoded '+'."""
    return f"https://voice.google.com/u/0/calls?a=nc,%2B{phone_e164.lstrip('+')}"


def _hooks_by_motivation(prospect: dict) -> tuple[str, str]:
    """Return (opener, voicemail) script lines tuned to the source signal."""
    addr = prospect.get("address", "your property")
    notes = prospect.get("notes", "")
    motivation = prospect.get("record_type", "")
    hooks = {
        "tax_delinquent": (
            f"I came across {addr} in the tax records — "
            + (notes.lower() + "." if notes else "looks like there's a past-due balance still showing.")
            + " Wanted to see if you'd be open to a cash offer to clear that?",
            f"Hi, this is {OWNER_NAME} calling about {addr}. I'm a local cash buyer — "
            "saw your property on the public tax-delinquent list and wanted to see "
            "if a quick cash offer would help. Call me back at "
            + OWNER_GV_NUMBER + ". Thanks."
        ),
        "code_violations": (
            f"I came across {addr} in the city's code-violation list. "
            "Sometimes those repairs add up — wanted to see if you'd consider a cash sale, "
            "as-is, instead of doing the fixes?",
            f"Hi, this is {OWNER_NAME} calling about {addr}. I saw the property on "
            "the open code-violation list — I buy houses as-is in any condition. "
            "Call me back at " + OWNER_GV_NUMBER + " if you'd like to skip the repairs. Thanks."
        ),
        "foreclosure": (
            f"I saw a court filing tied to {addr} — sometimes a fast cash close "
            "is the cleanest way through that. Would you be open to a no-obligation offer?",
            f"Hi, this is {OWNER_NAME} — I came across a foreclosure-related filing "
            f"on {addr} and wanted to see if a cash offer would help you avoid that. "
            "Call me back at " + OWNER_GV_NUMBER + ". Thanks."
        ),
        "vacant": (
            f"I noticed {addr} is registered with the city as vacant. "
            "If you'd rather not deal with carrying it, I'd be open to making a cash offer.",
            f"Hi, this is {OWNER_NAME} calling about {addr}. The city has it listed as vacant — "
            "if you're tired of carrying it, I'm a local cash buyer. "
            "Call me back at " + OWNER_GV_NUMBER + ". Thanks."
        ),
        "probate": (
            f"I came across an estate filing connected to {addr}. "
            "If you're an heir and want to skip the sale process, I'd be happy to make a cash offer.",
            f"Hi, this is {OWNER_NAME} — I'm calling about an estate property at {addr}. "
            "I'm a local cash buyer if you'd rather sell as-is. "
            "Call me back at " + OWNER_GV_NUMBER + ". Thanks."
        ),
    }
    return hooks.get(motivation, (
        f"I came across {addr} in some {prospect.get('city','')} public records — "
        "I'm a local cash buyer, would you be open to a no-obligation cash offer?",
        f"Hi, this is {OWNER_NAME} calling about {addr}. I'm a local cash buyer — "
        "give me a call back at " + OWNER_GV_NUMBER + " if you'd consider a cash offer. Thanks.",
    ))


def _build_script(prospect: dict) -> dict:
    """Per-prospect script: opener, 30-sec elevator, probe Q, soft close,
    voicemail drop. The owner reads from this on the call."""
    owner = (prospect.get("owner_name") or "").title()
    first = owner.split()[0] if owner else "there"
    city = (prospect.get("city") or "").title()
    addr = prospect.get("address", "your property")
    opener, voicemail = _hooks_by_motivation(prospect)
    return {
        "live_call": (
            f"\"Hi, is this {first}? My name is {OWNER_NAME} — quick question, "
            f"I won't keep you. {opener}\"\n\n"
            "── If they engage ──\n"
            f"\"Great. I'm a local real-estate investor here in {city}. "
            "I buy houses as-is, in any condition, close in 7-14 days, "
            "and cover all closing costs — no agent fees, no inspections, "
            "no repairs on your end.\"\n\n"
            "── Probe ──\n"
            f"\"How soon would you need to sell {addr}? — or, if you wouldn't "
            "consider it, I won't bug you again.\"\n\n"
            "── Soft close ──\n"
            "\"I can have a written cash offer in your hand within 48 hours. "
            "What's the best email to send it to?\""
        ),
        "voicemail": f"\"{voicemail}\"",
        "callback_number": OWNER_GV_NUMBER,
    }


def _called_set() -> set:
    """Numbers already on the call log within the cool-down window (7 days)."""
    log = storage.load(LOG_FILE, [])
    cutoff = datetime.now() - timedelta(days=7)
    out = set()
    for entry in log:
        try:
            ts = datetime.fromisoformat(entry.get("at", ""))
        except (ValueError, TypeError):
            continue
        if ts >= cutoff:
            phone = entry.get("phone", "")
            if phone:
                out.add(phone)
    return out


def _dnc_set() -> set:
    return {p for p in storage.load(DNC_FILE, []) if p}


def build_queue(max_calls: int = 25) -> dict:
    """Pull phone-having prospects, dedupe, generate scripts, return the queue."""
    sources = []
    # PropScout output is the cleanest source (structured + recent)
    ps = storage.load("ps_leads.json", [])
    sources.extend(ps)
    # Anything in the shared leads pipeline with a phone
    leads = storage.load("leads.json", {})
    if isinstance(leads, dict):
        for l in leads.values():
            if l.get("seller_phone"):
                sources.append({
                    "owner_name": l.get("seller_name", ""),
                    "address":    l.get("address", ""),
                    "city":       l.get("city", ""),
                    "state":      l.get("state", ""),
                    "phone":      l.get("seller_phone", ""),
                    "record_type": l.get("motivation", ""),
                })

    already = _called_set()
    dnc = _dnc_set()
    seen = set()
    queue = []
    for p in sources:
        e164 = _normalize(p.get("phone", ""))
        if not e164 or not _is_usable(e164) or e164 in already or e164 in dnc:
            continue
        if e164 in seen:
            continue
        seen.add(e164)
        p = {**p, "phone_e164": e164, "gv_link": _gv_link(e164)}
        p["script"] = _build_script(p)
        queue.append(p)
        if len(queue) >= max_calls:
            break
    return {"queue": queue, "total": len(queue),
            "skipped_already_called": sum(1 for p in sources
                if _normalize(p.get("phone","")) in already),
            "skipped_dnc": sum(1 for p in sources
                if _normalize(p.get("phone","")) in dnc),
            "skipped_unusable": sum(1 for p in sources
                if (_normalize(p.get("phone","")) and not _is_usable(_normalize(p.get("phone","")))))}


def _render_queue_html(queue: list) -> str:
    today = datetime.now().strftime("%A, %B %d, %Y")
    cards = []
    for i, p in enumerate(queue, 1):
        owner = (p.get("owner_name") or "(unknown owner)").title()
        addr = p.get("address", "")
        city = (p.get("city") or "").title()
        state = p.get("state", "")
        rec = p.get("record_type", "")
        phone_display = f"({p['phone_e164'][2:5]}) {p['phone_e164'][5:8]}-{p['phone_e164'][8:]}"
        notes = p.get("notes", "")
        script = p["script"]
        cards.append(f"""
        <div class="card" id="p{i}">
          <div class="head">
            <div class="num">{i:02d}</div>
            <div class="who">
              <div class="name">{owner}</div>
              <div class="addr">{addr} · {city}, {state}</div>
              <div class="meta"><span class="tag">{rec}</span>{('  ·  ' + notes) if notes else ''}</div>
            </div>
            <div class="dial-block">
              <a class="dial gv" href="{p['gv_link']}" target="_blank">📞 Call via Google Voice</a>
              <a class="dial tel" href="tel:{p['phone_e164']}">📱 Tap to dial · {phone_display}</a>
            </div>
          </div>
          <details class="script">
            <summary>Live-call script</summary>
            <pre>{script['live_call']}</pre>
          </details>
          <details class="script">
            <summary>Voicemail drop</summary>
            <pre>{script['voicemail']}</pre>
          </details>
          <div class="disp">
            <span class="dlabel">Disposition →</span>
            <button onclick="logCall('{p['phone_e164']}','spoke')">✓ Spoke</button>
            <button onclick="logCall('{p['phone_e164']}','no_answer')">📵 No answer</button>
            <button onclick="logCall('{p['phone_e164']}','voicemail')">📬 VM left</button>
            <button onclick="logCall('{p['phone_e164']}','interested')">🔥 Interested</button>
            <button onclick="logCall('{p['phone_e164']}','dnc')">🚫 DNC</button>
          </div>
        </div>""")
    js = """
    function logCall(phone, status) {
      let log = JSON.parse(localStorage.getItem('cd_log') || '{}');
      log[phone] = {status: status, at: new Date().toISOString()};
      localStorage.setItem('cd_log', JSON.stringify(log));
      document.querySelector(`#p${event.target.closest('.card').id.slice(1)} .head`).style.opacity = '0.4';
      event.target.parentElement.innerHTML = `<span class="logged">Logged: ${status}</span>`;
    }
    """
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Cold-Call Queue · {today}</title>
<style>
 body {{ margin:0; background:#0f172a; color:#e2e8f0; font-family:-apple-system,sans-serif; }}
 header {{ padding:20px 24px; background:#1e293b; border-bottom:3px solid #f59e0b; }}
 h1 {{ margin:0; font-size:22px; color:#f59e0b; }}
 .sub {{ color:#94a3b8; margin-top:4px; font-size:14px; }}
 .summary {{ padding:16px 24px; background:#0b1220; color:#94a3b8; font-size:13px; }}
 .grid {{ padding:16px 24px; display:grid; gap:14px; }}
 .card {{ background:#1e293b; border:1px solid #334155; border-radius:8px; padding:14px 16px; }}
 .head {{ display:grid; grid-template-columns:48px 1fr auto; gap:14px; align-items:center; }}
 .num {{ background:#0f172a; border-radius:6px; padding:10px; text-align:center;
         font-weight:800; font-size:18px; color:#f59e0b; }}
 .name {{ font-weight:700; color:#fff; font-size:16px; }}
 .addr {{ color:#cbd5e1; font-size:13px; margin-top:2px; }}
 .meta {{ color:#94a3b8; font-size:12px; margin-top:4px; }}
 .tag {{ background:#334155; padding:2px 8px; border-radius:10px; font-size:11px; color:#fef3c7; }}
 .dial-block {{ display:flex; flex-direction:column; gap:6px; }}
 .dial {{ background:#10b981; color:#fff; text-decoration:none; padding:8px 14px;
         border-radius:6px; font-weight:700; font-size:13px; text-align:center;
         display:inline-block; }}
 .dial.tel {{ background:#3b82f6; }}
 .dial:hover {{ filter:brightness(1.1); }}
 details.script {{ margin-top:10px; background:#0f172a; padding:8px 12px;
         border-radius:6px; cursor:pointer; }}
 details.script summary {{ color:#94a3b8; font-weight:600; font-size:13px; }}
 details.script pre {{ white-space:pre-wrap; color:#e2e8f0; font-size:13px;
         margin:8px 0 0; font-family:inherit; line-height:1.5; }}
 .disp {{ margin-top:10px; display:flex; gap:6px; align-items:center; flex-wrap:wrap; }}
 .dlabel {{ color:#64748b; font-size:11px; text-transform:uppercase; letter-spacing:1px; }}
 .disp button {{ background:#334155; color:#e2e8f0; border:0; border-radius:5px;
         padding:5px 10px; font-size:12px; cursor:pointer; }}
 .disp button:hover {{ background:#475569; }}
 .logged {{ color:#10b981; font-weight:700; font-size:13px; }}
</style></head><body>
<header>
 <h1>📞 Cold-Call Queue</h1>
 <div class="sub">{today} · {len(queue)} prospects · Caller-ID: {OWNER_GV_NUMBER} (Google Voice)</div>
</header>
<div class="summary">
 Tap the green button to dial from your Google Voice number. On desktop you'll
 see Google Voice's call window pop up; on mobile the Voice app handles it.
 Dispositions are saved to your browser (localStorage) — export to JSON
 nightly if you want them in the call log.
</div>
<div class="grid">{''.join(cards)}</div>
<script>{js}</script>
</body></html>"""


def acquire_cycle() -> dict:
    """Build today's queue and write the HTML page + email digest."""
    QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    max_calls = int(os.environ.get("CD_MAX_CALLS", "25"))
    result = build_queue(max_calls=max_calls)
    queue = result["queue"]

    today_slug = datetime.now().strftime("%Y%m%d")
    html_path = QUEUE_DIR / f"{today_slug}.html"
    html_path.write_text(_render_queue_html(queue))

    # Also pin the latest as a stable filename for cron-based access
    latest_path = QUEUE_DIR / "latest.html"
    latest_path.write_text(_render_queue_html(queue))

    return {**result, "html_path": str(html_path)}


def fulfill_cycle(stats: dict) -> dict:
    """Email the owner a daily call list + the HTML attachment."""
    owner = os.environ.get("CD_OWNER_EMAIL",
                            os.environ.get("SMTP_USER", ""))
    if not owner or not stats["queue"]:
        return {"digest_sent": 0}
    lines = [
        f"Cold-Call Queue — {datetime.now():%b %d, %Y}",
        "",
        f"{stats['total']} prospects ready to call (caller-ID: {OWNER_GV_NUMBER}).",
        f"Skipped: {stats['skipped_already_called']} already-called, "
        f"{stats['skipped_dnc']} on DNC list, {stats['skipped_unusable']} toll-free/invalid.",
        "",
        "Tap a link below to dial from your Google Voice number:",
        "",
    ]
    for i, p in enumerate(stats["queue"][:25], 1):
        owner_disp = (p.get("owner_name") or "(unknown)").title()
        phone_pretty = f"({p['phone_e164'][2:5]}) {p['phone_e164'][5:8]}-{p['phone_e164'][8:]}"
        rec = p.get("record_type", "")
        lines.append(f"{i:02d}. {owner_disp} — {p.get('address','')} ({p.get('city','')}, {p.get('state','')})")
        lines.append(f"    Motivation: {rec}" + (f" — {p['notes']}" if p.get("notes") else ""))
        lines.append(f"    Dial: {p['gv_link']}")
        lines.append(f"    Or tap: tel:{p['phone_e164']}   ({phone_pretty})")
        lines.append("")
    lines.append(f"Full queue page (open on your phone for one-tap dialing):\n{stats['html_path']}")
    body = "\n".join(lines)
    r = mailer.send(AGENT_KEY, owner,
                    f"Cold-Call Queue — {stats['total']} prospects ({datetime.now():%b %d})",
                    body, purpose="fulfillment",
                    attachments=[stats["html_path"]])
    return {"digest_sent": 1 if r.get("status") == "sent" else 0}


def record_disposition(phone_e164: str, status: str, notes: str = "") -> dict:
    """Append a call outcome to the log. Status ∈ {spoke,no_answer,voicemail,interested,dnc}."""
    log = storage.load(LOG_FILE, [])
    log.append({
        "phone": phone_e164,
        "status": status,
        "notes": notes,
        "at": datetime.now().isoformat(),
    })
    storage.save(LOG_FILE, log[-2000:])
    if status == "dnc":
        dnc = storage.load(DNC_FILE, [])
        if phone_e164 not in dnc:
            dnc.append(phone_e164)
        storage.save(DNC_FILE, dnc)
    return {"logged": 1, "status": status}


def run_full_cycle() -> dict:
    stats = acquire_cycle()
    digest = fulfill_cycle(stats)
    rev = billing.revenue_summary(AGENT_KEY)
    subs = storage.load("cd_subscribers.json", [])
    metrics.record(
        AGENT_KEY,
        prospects_added=stats["total"],
        fulfillment_sent=digest["digest_sent"],
        active_subs=sum(1 for s in subs if s.get("status") == "active"),
        mrr=rev["mrr"],
        total_revenue=rev["total_paid"],
    )
    # Don't leak the full queue (with scripts) up through metrics
    return {
        "total":           stats["total"],
        "skipped_called":  stats["skipped_already_called"],
        "skipped_dnc":     stats["skipped_dnc"],
        "skipped_invalid": stats["skipped_unusable"],
        "html_path":       stats["html_path"],
        "digest_sent":     digest["digest_sent"],
        "mrr":             rev["mrr"],
    }
