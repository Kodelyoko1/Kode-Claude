"""
SpeedAudit — website performance audit + prioritized fix list.
Revenue: $77 one-time audit, $37/mo monitoring, $297 quarterly retainer.

Inputs:
  - data/sa_inputs/{slug}.json with {"url": "...", "contact_email": "...", "contact_name": "..."}
    (or omit contact fields if it's just for internal use)
  - data/sa_leads.json: [{"name": "...", "email": "...", "site": "https://..."}]
    Leads get a free preview audit on the next acquire cycle.

Engine (no paid APIs):
  - urllib fetch with timeout + redirect chain tracking
  - response-time + transfer-size measurement
  - HTML parse: count <img> by ext, render-blocking <script>/<link> in <head>,
    inline style/script weight
  - header checks: gzip/brotli, cache-control, x-powered-by leakage
  - HTTPS + HSTS check
  - Scoring: 0-100, with prioritized fix list

Output: data/sa_outputs/{slug}.md (report) + log to JSON for history.
"""
import json
import re
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse, urljoin

sys.path.insert(0, str(Path(__file__).parent.parent))
from autonomous import storage, mailer, billing, metrics

AGENT_KEY = "speedaudit"
INPUTS_DIR = Path(__file__).parent.parent / "data" / "sa_inputs"
OUTPUTS_DIR = Path(__file__).parent.parent / "data" / "sa_outputs"

UA = ("Mozilla/5.0 (compatible; WholesaleOmniverse-SpeedAudit/1.0; "
      "+https://wholesaleomniverse.com)")
IMG_EXT_RE = re.compile(r"\.(jpe?g|png|gif|webp|avif|svg|bmp)(?:\?|$)", re.I)


def _fetch(url: str, timeout: int = 15) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip, br"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            elapsed = time.time() - t0
            return {
                "status": r.status,
                "headers": {k.lower(): v for k, v in r.headers.items()},
                "body": raw.decode("utf-8", errors="ignore"),
                "bytes": len(raw),
                "elapsed_s": elapsed,
                "final_url": r.url,
                "redirected": r.url != url,
            }
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}", "elapsed_s": time.time() - t0}
    except Exception as e:
        return {"error": str(e)[:200], "elapsed_s": time.time() - t0}


def _parse_assets(html: str, base_url: str) -> dict:
    images = re.findall(r"<img[^>]+src=[\"']([^\"']+)[\"']", html, re.I)
    scripts = re.findall(r"<script[^>]*\bsrc=[\"']([^\"']+)[\"'][^>]*>", html, re.I)
    head_match = re.search(r"<head[^>]*>(.*?)</head>", html, re.I | re.S)
    head = head_match.group(1) if head_match else html[:5000]
    blocking_scripts = [
        m for m in re.findall(r"<script[^>]+src=[\"'][^\"']+[\"'][^>]*>", head, re.I)
        if "async" not in m.lower() and "defer" not in m.lower()
    ]
    blocking_css = re.findall(
        r"<link[^>]+rel=[\"']stylesheet[\"'][^>]*>", head, re.I)
    inline_style = sum(len(m) for m in re.findall(r"<style[^>]*>(.*?)</style>", html, re.I | re.S))
    inline_script = sum(len(m) for m in re.findall(r"<script(?![^>]*src)[^>]*>(.*?)</script>",
                                                    html, re.I | re.S))
    big_images = [u for u in images if not IMG_EXT_RE.search(u) or
                  u.lower().endswith((".png", ".jpg", ".jpeg")) and not u.lower().endswith(".webp")]
    return {
        "image_count": len(images),
        "non_webp_images": len(big_images),
        "script_count": len(scripts),
        "blocking_scripts": len(blocking_scripts),
        "blocking_css": len(blocking_css),
        "inline_style_bytes": inline_style,
        "inline_script_bytes": inline_script,
    }


def _score_audit(measurements: dict, assets: dict) -> tuple:
    score = 100
    fixes = []
    elapsed = measurements.get("elapsed_s", 99)
    bytes_total = measurements.get("bytes", 0)
    headers = measurements.get("headers", {})
    parsed = urlparse(measurements.get("final_url", ""))

    if elapsed > 1.5:
        penalty = min(35, int((elapsed - 1.5) * 10))
        score -= penalty
        fixes.append((penalty, f"Page took {elapsed:.2f}s to first byte. Target <1s. "
                              f"Investigate server response time or upgrade hosting."))
    if bytes_total > 1_500_000:
        penalty = min(20, (bytes_total - 1_500_000) // 100_000)
        score -= penalty
        fixes.append((penalty, f"HTML+inline payload is {bytes_total/1024:.0f}KB. "
                              f"Aim for <500KB. Externalize and minify CSS/JS."))
    if "content-encoding" not in headers or not any(
            x in headers.get("content-encoding", "").lower() for x in ("gzip", "br")):
        score -= 12
        fixes.append((12, "No gzip/brotli compression detected. Enable at the server/CDN — "
                          "typically a 60-70% transfer-size reduction."))
    if "cache-control" not in headers:
        score -= 8
        fixes.append((8, "No Cache-Control header. Set `Cache-Control: public, max-age=31536000, "
                         "immutable` for static assets."))
    if parsed.scheme != "https":
        score -= 15
        fixes.append((15, "Site is not on HTTPS. Use Cloudflare (free tier) or "
                          "Let's Encrypt for a free certificate."))
    elif "strict-transport-security" not in headers:
        score -= 4
        fixes.append((4, "HTTPS is on but HSTS header missing. Add "
                         "`Strict-Transport-Security: max-age=31536000; includeSubDomains`."))
    if assets["non_webp_images"] > 5:
        penalty = min(15, assets["non_webp_images"])
        score -= penalty
        fixes.append((penalty, f"{assets['non_webp_images']} non-WebP images found. "
                              f"Convert with `cwebp -q 80` — typically 30-50% smaller."))
    if assets["blocking_scripts"] > 0:
        penalty = min(15, assets["blocking_scripts"] * 4)
        score -= penalty
        fixes.append((penalty, f"{assets['blocking_scripts']} render-blocking <script> in <head>. "
                              f"Add `async` or `defer`, or move to bottom of <body>."))
    if assets["blocking_css"] > 3:
        penalty = min(8, (assets["blocking_css"] - 3) * 2)
        score -= penalty
        fixes.append((penalty, f"{assets['blocking_css']} stylesheets in <head>. "
                              f"Inline critical CSS, defer the rest."))
    if "x-powered-by" in headers:
        score -= 2
        fixes.append((2, f"`X-Powered-By: {headers['x-powered-by']}` header leaks stack info. "
                         f"Remove for security hygiene (not a perf issue)."))
    if measurements.get("redirected"):
        score -= 5
        fixes.append((5, f"Initial URL redirected to {measurements['final_url']}. "
                         f"Update inbound links to skip the redirect (saves ~200-500ms)."))

    fixes.sort(key=lambda x: -x[0])
    return max(0, score), fixes


def audit_url(url: str) -> dict:
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    m = _fetch(url)
    if "error" in m:
        return {"url": url, "error": m["error"], "elapsed_s": m.get("elapsed_s")}
    assets = _parse_assets(m["body"], m["final_url"])
    score, fixes = _score_audit(m, assets)
    return {
        "url": url,
        "final_url": m["final_url"],
        "score": score,
        "elapsed_s": round(m["elapsed_s"], 3),
        "bytes": m["bytes"],
        "redirected": m["redirected"],
        "status": m["status"],
        "headers_seen": list(m["headers"].keys()),
        "assets": assets,
        "fixes": fixes,
    }


def write_report(slug: str, audit: dict) -> Path:
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    out = OUTPUTS_DIR / f"{slug}.md"
    if "error" in audit:
        out.write_text(f"# Speed audit — {slug}\n\n"
                       f"**URL:** {audit['url']}\n\n"
                       f"Could not audit: `{audit['error']}`\n")
        return out
    lines = [
        f"# Speed audit — {slug}",
        "",
        f"**URL:** {audit['final_url']}",
        f"**Score:** {audit['score']}/100",
        f"**TTFB + transfer:** {audit['elapsed_s']}s",
        f"**HTML payload:** {audit['bytes'] / 1024:.1f} KB",
        f"**Audited:** {datetime.now():%Y-%m-%d %H:%M}",
        "",
        "## Asset breakdown",
        f"- Images: {audit['assets']['image_count']} (non-WebP: {audit['assets']['non_webp_images']})",
        f"- External scripts: {audit['assets']['script_count']} "
        f"(render-blocking in `<head>`: {audit['assets']['blocking_scripts']})",
        f"- Render-blocking stylesheets: {audit['assets']['blocking_css']}",
        f"- Inline `<style>` bytes: {audit['assets']['inline_style_bytes']:,}",
        f"- Inline `<script>` bytes: {audit['assets']['inline_script_bytes']:,}",
        "",
        "## Prioritized fixes",
        "",
    ]
    if not audit["fixes"]:
        lines.append("_No major issues detected — site is well-optimized._")
    else:
        for i, (impact, msg) in enumerate(audit["fixes"], 1):
            lines.append(f"{i}. **(-{impact} pts)** {msg}")
    lines.append("")
    lines.append("---")
    lines.append("_Generated by SpeedAudit. Re-run monthly to track score over time._")
    out.write_text("\n".join(lines))
    return out


def build_queue() -> dict:
    INPUTS_DIR.mkdir(parents=True, exist_ok=True)
    produced = 0
    for spec_path in sorted(INPUTS_DIR.glob("*.json")):
        slug = spec_path.stem
        if (OUTPUTS_DIR / f"{slug}.md").exists():
            continue
        try:
            spec = json.loads(spec_path.read_text())
        except Exception:
            continue
        url = spec.get("url")
        if not url:
            continue
        audit = audit_url(url)
        write_report(slug, audit)
        produced += 1
    return {"audits_produced": produced}


def fulfill_cycle() -> dict:
    subs = storage.load("sa_subscribers.json", [])
    log = storage.load("sa_delivery_log.json", {})
    sent = 0
    for sub in subs:
        if sub.get("status") != "active":
            continue
        email = sub.get("email")
        site = sub.get("site")
        if not (email and site):
            continue
        last = log.get(email, {}).get("last_audit_at")
        if last and (datetime.now() - datetime.fromisoformat(last)).days < 28:
            continue
        slug = f"sub-{re.sub(r'[^a-z0-9]+', '-', site.lower())[:40]}-{datetime.now():%Y%m%d}"
        audit = audit_url(site)
        write_report(slug, audit)
        body = (f"Hi {sub.get('name', 'there')},\n\n"
                f"Monthly SpeedAudit for {site}:\n"
                f"  Score: {audit.get('score', 'n/a')}/100\n"
                f"  TTFB+transfer: {audit.get('elapsed_s', 'n/a')}s\n\n"
                f"Full report: data/sa_outputs/{slug}.md\n")
        r = mailer.send(AGENT_KEY, email,
                        f"SpeedAudit — {site} — score {audit.get('score', 'n/a')}/100",
                        body, purpose="fulfillment")
        if r.get("status") == "sent":
            log.setdefault(email, {})["last_audit_at"] = datetime.now().isoformat()
            sent += 1
    storage.save("sa_delivery_log.json", log)
    return {"fulfillment_sent": sent}


def acquire_cycle() -> dict:
    leads = storage.load("sa_leads.json", [])
    sent = 0
    for lead in leads:
        if lead.get("trial_sent"):
            continue
        email = lead.get("email")
        site = lead.get("site")
        if not (email and site):
            continue
        slug = f"lead-{re.sub(r'[^a-z0-9]+', '-', site.lower())[:40]}"
        audit = audit_url(site)
        write_report(slug, audit)
        top_fixes = "\n".join(f"  - {msg}" for _, msg in audit.get("fixes", [])[:3]) or "  - No major issues"
        body = (
            f"Hi {lead.get('name', 'there')},\n\n"
            f"Free SpeedAudit preview for {site}:\n\n"
            f"  Performance score: {audit.get('score', 'n/a')}/100\n"
            f"  TTFB + transfer:   {audit.get('elapsed_s', 'n/a')}s\n"
            f"  HTML payload:      {audit.get('bytes', 0)/1024:.0f} KB\n\n"
            f"Top fixes (full report has 6-10):\n{top_fixes}\n\n"
            f"Full prioritized fix list + month-over-month tracking:\n"
            f"  $77 one-time deep audit\n"
            f"  $37/mo monitoring (monthly re-audit + alerts)\n"
            f"  $297 quarterly retainer (includes implementation review)\n\n"
            f"Reply if you want the full report.\n"
        )
        r = mailer.send(AGENT_KEY, email,
                        f"Free SpeedAudit — {site} — top 3 fixes",
                        body, purpose="outreach")
        if r.get("status") == "sent":
            lead["trial_sent"] = datetime.now().isoformat()
            sent += 1
    storage.save("sa_leads.json", leads)
    return {"outreach_sent": sent}


def run_full_cycle() -> dict:
    q = build_queue()
    a = acquire_cycle()
    f = fulfill_cycle()
    rev = billing.revenue_summary(AGENT_KEY)
    subs = storage.load("sa_subscribers.json", [])
    metrics.record(
        AGENT_KEY,
        products_produced=q["audits_produced"],
        outreach_sent=a["outreach_sent"],
        fulfillment_sent=f["fulfillment_sent"],
        active_subs=sum(1 for s in subs if s.get("status") == "active"),
        mrr=rev["mrr"],
        total_revenue=rev["total_paid"],
    )
    return {**q, **a, **f, **rev}
