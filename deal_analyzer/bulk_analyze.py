"""
Bulk-analyze every hot lead in one pass, rank by deal quality, email the owner
a "top deals to pursue today" digest.

This is the agent's biggest revenue accelerator. Instead of the owner opening
the chat agent and analyzing one lead at a time, every escalated hot lead with
an ARV estimate gets the analyze_deal() math applied, then sorted so the
top-spread, in-the-money deals float to the top.

Without this, a foreclosure lead flagged at 14:00 sits unworked until the owner
remembers to look — sometimes a few days, sometimes never. With this, the owner
gets a daily inbox digest with the math already done and the asking-price-vs-MAO
delta highlighted.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

DATA_DIR        = Path(__file__).parent.parent / "data"
LEADS_FILE      = DATA_DIR / "leads.json"
ANALYSIS_LOG    = DATA_DIR / "da_analysis_log.json"

sys.path.insert(0, str(Path(__file__).parent.parent))
from tools import analyze_deal as _analyze_deal
try:
    from followup_agent.escalation import ALL_DISTRESS
except Exception:
    ALL_DISTRESS = {
        "foreclosure", "pre_foreclosure", "pre-foreclosure",
        "tax_delinquent", "tax-delinquent",
        "code_violations", "code-violations",
        "vacant", "vacant_abandoned",
        "probate", "inherited", "estate",
        "divorce", "bankruptcy",
    }

DEFAULT_ASSIGNMENT_FEE = float(os.environ.get("DA_DEFAULT_ASSIGNMENT_FEE", "10000"))
DIGEST_TOP_N           = int(os.environ.get("DA_DIGEST_TOP_N", "15"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return default


def _save(path: Path, data) -> None:
    import tempfile
    path.parent.mkdir(exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except Exception:
        try: os.unlink(tmp)
        except OSError: pass
        raise


def _is_hot(lead: dict) -> bool:
    return any(t in (lead.get("motivation") or "").lower() for t in ALL_DISTRESS)


# ─────────────────────────── The core pass ───────────────────────────

def _score_deal(analysis: dict, lead: dict) -> float:
    """Higher = pursue first. Composite of equity%, spread magnitude, and contact strength."""
    score = 0.0
    score += (analysis.get("equity_pct") or 0)            # equity % is the big one
    spread = analysis.get("spread") or 0
    if spread > 0:
        # Asking price already below MAO — easy buy
        score += min(spread / 1000, 50)
    if lead.get("seller_phone"): score += 10
    if lead.get("seller_email"): score += 5
    verdict = analysis.get("verdict", "")
    if "STRONG" in verdict: score += 50
    elif "GOOD" in verdict: score += 25
    elif "BORDERLINE" in verdict: score += 5
    return round(score, 2)


def analyze_all_hot(*, assignment_fee: float = DEFAULT_ASSIGNMENT_FEE,
                     only_with_asking: bool = False,
                     statuses_to_skip: tuple = ("assigned", "dead", "cold")) -> dict:
    """Walk every hot lead with estimated_arv>0, compute analyze_deal, return ranked list."""
    leads = _load(LEADS_FILE, {})
    if not isinstance(leads, dict):
        return {"error": "leads.json shape", "ranked": [], "analyzed": 0}

    candidates = []
    for lid, l in leads.items():
        if not _is_hot(l):
            continue
        if l.get("status") in statuses_to_skip:
            continue
        arv = float(l.get("estimated_arv") or 0)
        if arv <= 0:
            continue
        asking = float(l.get("asking_price") or 0)
        if only_with_asking and asking <= 0:
            continue
        repairs = float(l.get("estimated_repairs") or 0)
        # If no asking_price, run analyze_deal with asking=MAO so verdict reflects
        # "best case if seller accepts your max" — owner sees the ceiling either way.
        ask_for_calc = asking if asking > 0 else (arv * 0.70 - repairs - assignment_fee)
        analysis = _analyze_deal(
            address=l.get("address", ""),
            arv=arv, repair_cost=repairs,
            asking_price=ask_for_calc, assignment_fee=assignment_fee,
        )
        analysis["_no_asking_price"] = asking <= 0
        analysis["lead_id"]       = lid
        analysis["city"]          = l.get("city", "")
        analysis["state"]         = l.get("state", "")
        analysis["motivation"]    = l.get("motivation", "")
        analysis["seller_name"]   = l.get("seller_name", "")
        analysis["seller_phone"]  = l.get("seller_phone", "")
        analysis["seller_email"]  = l.get("seller_email", "")
        analysis["status"]        = l.get("status", "")
        analysis["score"]         = _score_deal(analysis, l)
        candidates.append(analysis)

    candidates.sort(key=lambda x: x["score"], reverse=True)

    _log_run(len(candidates))
    return {
        "analyzed":      len(candidates),
        "assignment_fee": assignment_fee,
        "ranked":        candidates,
    }


def _log_run(n: int) -> None:
    rec = _load(ANALYSIS_LOG, [])
    if not isinstance(rec, list):
        rec = []
    rec.append({"ts": _now(), "analyzed": n})
    _save(ANALYSIS_LOG, rec)


# ─────────────────────────── Owner digest ───────────────────────────

def render_digest_text(result: dict, *, top_n: int = DIGEST_TOP_N) -> str:
    ranked = result["ranked"][:top_n]
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        f"Deal Analyzer — top {len(ranked)} hot leads by deal math",
        f"Generated: {today}   Default assignment fee: ${result['assignment_fee']:,.0f}",
        "",
        "─" * 72,
        "",
    ]
    if not ranked:
        lines.append("No analyzable hot leads in the pool right now (need estimated_arv > 0).")
        return "\n".join(lines)

    for i, a in enumerate(ranked, 1):
        verdict_emoji = {"STRONG DEAL": "🔥", "GOOD DEAL": "✓",
                         "BORDERLINE": "?", "DEAL DOES NOT WORK": "✗"}.get(a["verdict"], "·")
        no_ask = " (no asking price)" if a.get("_no_asking_price") else ""
        lines.append(f"{i:>2}. {verdict_emoji} {a['verdict']:18s} score={a['score']:>5.1f}  "
                      f"{a['address']}, {a['city']}, {a['state']}{no_ask}")
        lines.append(f"     ARV ${a['arv']:>9,.0f}  Repairs ${a['repair_cost']:>7,.0f}  "
                      f"MAO ${a['mao']:>9,.0f}  Asking ${a['asking_price']:>9,.0f}  "
                      f"Spread ${a['spread']:>+8,.0f}")
        lines.append(f"     Equity ${a['equity_after_repairs']:>9,.0f} ({a['equity_pct']:>5.1f}%)  "
                      f"Motivation: {a['motivation'][:60]}")
        seller = a.get("seller_name", "")
        phone  = a.get("seller_phone", "")
        email  = a.get("seller_email", "")
        contact = " · ".join(p for p in (seller, phone, email) if p) or "no contact info"
        lines.append(f"     {contact}   [{a['lead_id']}]")
        lines.append(f"     → {a['action']}")
        lines.append("")
    lines.append("─" * 72)
    lines.append(f"Total analyzed: {result['analyzed']}.  Run --bulk-analyze again tomorrow.")
    return "\n".join(lines)


def render_digest_html(result: dict, *, top_n: int = DIGEST_TOP_N) -> str:
    ranked = result["ranked"][:top_n]
    rows = []
    for i, a in enumerate(ranked, 1):
        verdict_color = {"STRONG DEAL": "#FDD023", "GOOD DEAL": "#7fc97f",
                         "BORDERLINE": "#cccccc", "DEAL DOES NOT WORK": "#ff7676"}.get(a["verdict"], "#cccccc")
        no_ask = " <span style='color:#9a9a9a'>(no asking price)</span>" if a.get("_no_asking_price") else ""
        contact = " · ".join(p for p in (a.get("seller_name",""), a.get("seller_phone",""),
                                            a.get("seller_email","")) if p) or "no contact"
        rows.append(
            f'<tr><td style="padding:10px;border-bottom:1px solid #2a2a2a;vertical-align:top;color:#cccccc;">'
            f'<div style="font-size:13px;"><strong style="color:{verdict_color};">{a["verdict"]}</strong>'
            f' · score {a["score"]:.1f} · {a["lead_id"]}{no_ask}</div>'
            f'<div style="font-size:14px;font-weight:bold;">{a["address"]}, {a["city"]}, {a["state"]}</div>'
            f'<div style="font-size:12px;color:#9a9a9a;">{a["motivation"][:80]}</div>'
            f'<div style="font-size:12px;color:#cccccc;margin-top:4px;">'
            f'ARV ${a["arv"]:,.0f} · Repairs ${a["repair_cost"]:,.0f} · '
            f'<strong>MAO ${a["mao"]:,.0f}</strong> · Asking ${a["asking_price"]:,.0f} · '
            f'Spread ${a["spread"]:+,.0f}</div>'
            f'<div style="font-size:12px;color:#cccccc;">Equity ${a["equity_after_repairs"]:,.0f} '
            f'({a["equity_pct"]:.1f}%) · {contact}</div>'
            f'<div style="font-size:12px;color:#FDD023;margin-top:4px;">→ {a["action"]}</div>'
            f'</td></tr>'
        )
    table = ('<table cellpadding="0" cellspacing="0" style="width:100%;border-collapse:collapse;">'
             + "".join(rows) + "</table>")
    return (
        f'<p style="margin:0 0 8px;color:#cccccc;"><strong>Deal Analyzer — top {len(ranked)} hot leads</strong></p>'
        f'<p style="margin:0 0 16px;color:#9a9a9a;font-size:12px;">'
        f'Default assignment fee ${result["assignment_fee"]:,.0f} · '
        f'Analyzed {result["analyzed"]} total</p>'
        + table
    )


def email_owner_digest(result: dict, *, top_n: int = DIGEST_TOP_N) -> dict:
    """Send the digest to FOLLOWUP_OWNER_EMAIL or SMTP_USER."""
    from email_template import send_branded_email
    owner = (os.environ.get("DA_OWNER_EMAIL")
             or os.environ.get("FOLLOWUP_OWNER_EMAIL")
             or os.environ.get("SMTP_USER", ""))
    if not owner:
        return {"status": "skipped", "reason": "no owner email configured"}
    if not result["ranked"]:
        return {"status": "skipped", "reason": "no analyzable leads"}
    subject = (f"[Deal Analyzer] {min(top_n, len(result['ranked']))} deals "
               f"of {result['analyzed']} analyzed")
    r = send_branded_email(
        to_email=owner, subject=subject,
        body_text=render_digest_text(result, top_n=top_n),
        body_html_inner=render_digest_html(result, top_n=top_n),
    )
    return {"status": r.get("status"), "to": owner, "error": r.get("error")}


# ─────────────────────────── CLI ───────────────────────────

def main() -> int:
    import argparse
    p = argparse.ArgumentParser(description="Bulk-analyze hot leads + owner digest")
    p.add_argument("--assignment-fee", type=float, default=DEFAULT_ASSIGNMENT_FEE,
                    help=f"Default assignment fee in USD (default {DEFAULT_ASSIGNMENT_FEE:.0f})")
    p.add_argument("--top", type=int, default=DIGEST_TOP_N, help="Top N to include in digest")
    p.add_argument("--with-asking-only", action="store_true",
                    help="Restrict to leads with asking_price > 0")
    p.add_argument("--send", action="store_true",
                    help="Send the owner digest email (otherwise print only)")
    args = p.parse_args()

    result = analyze_all_hot(
        assignment_fee=args.assignment_fee,
        only_with_asking=args.with_asking_only,
    )
    print(render_digest_text(result, top_n=args.top))
    if args.send:
        delivery = email_owner_digest(result, top_n=args.top)
        print("\n--- delivery ---")
        print(json.dumps(delivery, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
