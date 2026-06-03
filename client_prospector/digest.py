"""
Owner-facing daily digest for Client Prospector.

The audience for this digest is the *owner*, not the prospects. It surfaces
the actions only a human can take (process replies, sign new clients) so they
don't get buried in inbox noise.

Sections:
  1. Replies awaiting onboarding — highest-leverage action, with exact CLI lines
  2. Today's activity — pitches + follow-ups sent in the last 24 hours
  3. Stale prospects expired today
  4. Funnel snapshot
  5. MRR ceiling from the pitchable pool
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from email_template import send_branded_email

DATA_DIR        = Path(__file__).parent.parent / "data"
PROSPECTS_FILE  = DATA_DIR / "prospects.json"
SAAS_FILE       = DATA_DIR / "saas_clients.json"
OAS_FILE        = DATA_DIR / "oas_clients.json"
DIGESTS_DIR     = DATA_DIR / "cp_digests"
LOG_FILE        = DATA_DIR / "cp_digest_log.json"

REPLY_RATE = 0.05
CONVERT_RATE = 0.20
SAAS_PRICE = 197
OAS_PRICE  = 500


def _load(path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return default


def _save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


def _within_last_day(iso: str) -> bool:
    if not iso:
        return False
    try:
        ts = datetime.fromisoformat(iso.replace("Z", "+00:00").split("+")[0])
    except ValueError:
        return False
    return ts > datetime.now() - timedelta(hours=24)


def _today() -> bool:
    return True  # placeholder kept for shape; per-call helper below


def _collect_sections() -> dict:
    prospects = _load(PROSPECTS_FILE, {})
    if not isinstance(prospects, dict):
        prospects = {}

    awaiting_onboard = []
    pitched_today    = []
    followed_up_today = []
    went_stale_today = []
    by_state = {"new": 0, "pitched": 0, "followed_up": 0,
                "replied": 0, "converted": 0, "stale": 0}
    pitchable_saas = 0
    pitchable_oas  = 0

    for pid, p in prospects.items():
        if p.get("converted_client_id"):
            by_state["converted"] += 1
        elif p.get("replied") or p.get("status") == "replied":
            by_state["replied"] += 1
            if not p.get("converted_client_id"):
                awaiting_onboard.append(p)
        elif p.get("status") == "stale":
            by_state["stale"] += 1
        elif p.get("status") == "followed_up" or p.get("followup_count", 0) > 0:
            by_state["followed_up"] += 1
        elif p.get("status") == "pitched":
            by_state["pitched"] += 1
        else:
            by_state["new"] += 1

        if _within_last_day(p.get("pitched_at", "")):
            pitched_today.append(p)
        if _within_last_day(p.get("followup_1_sent_at", "")):
            followed_up_today.append(p)
        if _within_last_day(p.get("marked_stale_at", "")):
            went_stale_today.append(p)

        if p.get("email") and not p.get("email_bounced"):
            if p.get("product_pitched") == "oas":
                pitchable_oas += 1
            else:
                pitchable_saas += 1

    # MRR right now
    saas = _load(SAAS_FILE, {}) if isinstance(_load(SAAS_FILE, {}), dict) else {}
    oas  = _load(OAS_FILE,  {}) if isinstance(_load(OAS_FILE,  {}), dict) else {}
    mrr = 0.0
    for c in list(saas.values()) + list(oas.values()):
        if c.get("status") == "active" and c.get("payment_verified"):
            mrr += float(c.get("monthly_fee", 0))

    ceiling = int(
        pitchable_saas * REPLY_RATE * CONVERT_RATE * SAAS_PRICE
        + pitchable_oas * REPLY_RATE * CONVERT_RATE * OAS_PRICE
    )

    return {
        "awaiting_onboard": awaiting_onboard,
        "pitched_today": pitched_today,
        "followed_up_today": followed_up_today,
        "went_stale_today": went_stale_today,
        "by_state": by_state,
        "pitchable_saas": pitchable_saas,
        "pitchable_oas": pitchable_oas,
        "mrr_actual": mrr,
        "mrr_ceiling": ceiling,
        "total_prospects": len(prospects),
    }


# ─────────────────────────── Render ───────────────────────────

def _render_text(s: dict) -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    lines = [
        f"Client Prospector — Owner Digest — {today}",
        "",
        f"Pool: {s['total_prospects']} prospects  |  MRR: ${s['mrr_actual']:.0f}  "
        f"|  Ceiling (5% × 20%): ${s['mrr_ceiling']:.0f}/mo",
        "",
        "── REPLIES AWAITING ONBOARDING " + ("─" * 40),
    ]
    if not s["awaiting_onboard"]:
        lines.append("  (none — inbox is clear)")
    else:
        for p in s["awaiting_onboard"]:
            pid = p.get("prospect_id", "?")
            lines.append(f"  · {pid}  {p.get('name','')[:40]:<40}  "
                         f"{p.get('email','')[:40]}")
            lines.append(f"      market={p.get('market','')}  "
                         f"product={p.get('product_pitched','saas').upper()}")
            lines.append(f"      → python3 onboard_client.py    "
                         f"(then `--mark-converted {pid} CLIENT-ID`)")

    lines += ["", "── ACTIVITY (last 24h) " + ("─" * 47)]
    lines.append(f"  Pitches sent:      {len(s['pitched_today'])}")
    lines.append(f"  Follow-ups sent:   {len(s['followed_up_today'])}")
    lines.append(f"  Marked stale:      {len(s['went_stale_today'])}")

    lines += ["", "── FUNNEL " + ("─" * 60)]
    bs = s["by_state"]
    lines.append(
        f"  new={bs['new']}  pitched={bs['pitched']}  followed_up={bs['followed_up']}  "
        f"replied={bs['replied']}  converted={bs['converted']}  stale={bs['stale']}"
    )
    lines.append(f"  pitchable: saas={s['pitchable_saas']}  oas={s['pitchable_oas']}")

    lines += ["", "Next: run --followup to nudge silent pitched prospects.", ""]
    return "\n".join(lines)


def _render_html(s: dict) -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    awaiting_html = ""
    if not s["awaiting_onboard"]:
        awaiting_html = "<p><em>None — inbox is clear.</em></p>"
    else:
        rows = []
        for p in s["awaiting_onboard"]:
            pid = p.get("prospect_id", "?")
            rows.append(
                f"<tr>"
                f"<td><strong>{pid}</strong></td>"
                f"<td>{p.get('name','')[:50]}</td>"
                f"<td>{p.get('email','')}</td>"
                f"<td>{p.get('market','')}</td>"
                f"<td>{p.get('product_pitched','saas').upper()}</td>"
                f"</tr>"
            )
        awaiting_html = (
            "<table style='border-collapse:collapse;width:100%;font-size:13px;'>"
            "<thead><tr style='background:#f1f5f9;'>"
            "<th align='left' style='padding:6px;'>ID</th>"
            "<th align='left' style='padding:6px;'>Name</th>"
            "<th align='left' style='padding:6px;'>Email</th>"
            "<th align='left' style='padding:6px;'>Market</th>"
            "<th align='left' style='padding:6px;'>Product</th>"
            "</tr></thead><tbody>"
            + "".join(rows)
            + "</tbody></table>"
            "<p style='margin-top:8px;font-size:13px;'>"
            "<strong>Next step:</strong> <code>python3 onboard_client.py</code> per reply, "
            "then <code>python3 -m client_prospector.followup mark-converted &lt;PRO-id&gt; &lt;CLIENT-id&gt;</code>."
            "</p>"
        )
    bs = s["by_state"]
    return (
        f"<h2>Client Prospector — {today}</h2>"
        f"<p>Pool: <strong>{s['total_prospects']}</strong> prospects · "
        f"MRR <strong>${s['mrr_actual']:.0f}</strong> · "
        f"Ceiling (5% × 20%): <strong>${s['mrr_ceiling']:.0f}/mo</strong></p>"
        f"<h3>Replies awaiting onboarding ({len(s['awaiting_onboard'])})</h3>"
        f"{awaiting_html}"
        f"<h3>Activity (last 24h)</h3>"
        f"<ul>"
        f"<li>Pitches sent: <strong>{len(s['pitched_today'])}</strong></li>"
        f"<li>Follow-ups sent: <strong>{len(s['followed_up_today'])}</strong></li>"
        f"<li>Marked stale: <strong>{len(s['went_stale_today'])}</strong></li>"
        f"</ul>"
        f"<h3>Funnel</h3>"
        f"<p>new={bs['new']} · pitched={bs['pitched']} · followed_up={bs['followed_up']} · "
        f"replied={bs['replied']} · converted={bs['converted']} · stale={bs['stale']}<br>"
        f"pitchable: saas={s['pitchable_saas']} · oas={s['pitchable_oas']}</p>"
    )


def _log_delivery(status: str, error: str = "", awaiting: int = 0):
    rec = _load(LOG_FILE, [])
    if not isinstance(rec, list):
        rec = []
    rec.append({
        "ts": datetime.now().isoformat(),
        "status": status,
        "awaiting": awaiting,
        "error": error or "",
    })
    _save(LOG_FILE, rec)


def send_owner_digest(dry_run: bool = False) -> dict:
    s = _collect_sections()
    body_text = _render_text(s)
    body_html = _render_html(s)
    today = datetime.now().strftime("%Y-%m-%d")
    DIGESTS_DIR.mkdir(parents=True, exist_ok=True)
    digest_path = DIGESTS_DIR / f"{today}.md"
    digest_path.write_text(body_text)

    if dry_run:
        return {"status": "dry_run", "preview_path": str(digest_path),
                "awaiting_onboard": len(s["awaiting_onboard"])}

    to = os.environ.get("CP_OWNER_EMAIL") or os.environ.get("SMTP_USER", "")
    if not to:
        return {"status": "failed", "error": "no owner email (CP_OWNER_EMAIL or SMTP_USER)"}

    suffix = f" — {len(s['awaiting_onboard'])} reply(s) to onboard" if s["awaiting_onboard"] else ""
    subject = f"[Prospector] Owner digest {today}{suffix}"
    result = send_branded_email(
        to_email=to, subject=subject,
        body_text=body_text, body_html_inner=body_html,
    )
    status = result.get("status", "failed")
    _log_delivery(status, result.get("error", ""), len(s["awaiting_onboard"]))
    return {
        "status": status,
        "awaiting_onboard": len(s["awaiting_onboard"]),
        "preview_path": str(digest_path),
        "error": result.get("error", ""),
    }


def main() -> int:
    import argparse
    p = argparse.ArgumentParser(description="Owner digest for Client Prospector")
    p.add_argument("--dry-run", action="store_true",
                   help="Write to data/cp_digests/ but don't email")
    args = p.parse_args()
    print(json.dumps(send_owner_digest(dry_run=args.dry_run), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
