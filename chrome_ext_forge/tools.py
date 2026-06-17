"""
Chrome Extension Forge — generates a ready-to-load browser extension that
overlays wholesale deal analysis (ARV, MAO, profit margin, motivation score)
on Zillow and Redfin property pages.

The extension is a Manifest V3 Chrome extension with:
  - manifest.json
  - content.js   (injected on zillow.com/* and redfin.com/*)
  - popup.html   (quick calc panel)
  - popup.js     (popup logic)
  - background.js (service worker for API proxy)
  - styles.css   (overlay styles)

The generated package is zipped to data/cef_packages/deal-analyzer-ext-{version}.zip
and the source lands in data/cef_packages/src/.

Lead integration: reads data/ls_scored.json (Lead Sieve output) to pre-populate
known addresses in the extension's local storage at build time.

Pricing: $47/mo SaaS for other wholesalers.

Entry point: run_full_cycle()
"""
import json
import os
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from autonomous import storage, mailer, metrics, billing

AGENT_KEY   = "chrome_ext_forge"
PACKAGES_DIR = Path(__file__).parent.parent / "data" / "cef_packages"
SRC_DIR      = PACKAGES_DIR / "src"
PACKAGES_DIR.mkdir(parents=True, exist_ok=True)
SRC_DIR.mkdir(parents=True, exist_ok=True)

VERSION = "1.0.0"

# ── Extension source files ────────────────────────────────────────────────────

MANIFEST = {
    "manifest_version": 3,
    "name": "Wholesale Deal Analyzer",
    "version": VERSION,
    "description": "Overlay ARV, MAO, and profit margin on Zillow & Redfin listings.",
    "permissions": ["storage", "activeTab"],
    "host_permissions": [
        "https://www.zillow.com/*",
        "https://www.redfin.com/*",
    ],
    "content_scripts": [
        {
            "matches": ["https://www.zillow.com/*", "https://www.redfin.com/*"],
            "js": ["content.js"],
            "css": ["styles.css"],
            "run_at": "document_idle",
        }
    ],
    "action": {
        "default_popup": "popup.html",
        "default_title": "Deal Analyzer",
    },
    "background": {
        "service_worker": "background.js",
    },
    "icons": {
        "16":  "icon16.png",
        "48":  "icon48.png",
        "128": "icon128.png",
    },
}

CONTENT_JS = r"""
/* Wholesale Deal Analyzer — content script
   Injected on Zillow and Redfin property detail pages.
   Reads the listing price, estimates ARV via a 10% premium heuristic,
   and injects a deal-analysis overlay panel. */

(function () {
  'use strict';

  const REPAIR_RATE = 0.05;  // assumed repairs = 5% of list price
  const MAO_FACTOR  = 0.70;  // MAO = ARV * 70% - repairs - $10k assignment fee

  function getListingPrice() {
    const selectors = [
      '[data-testid="price"]',           // Zillow
      '.ds-price',                       // Zillow legacy
      '.statsValue',                     // Redfin
      '[data-rf-test-id="avmValue"]',    // Redfin AVM
      'span[class*="Price"]',
    ];
    for (const sel of selectors) {
      const el = document.querySelector(sel);
      if (el) {
        const text = el.textContent.replace(/[^0-9]/g, '');
        const val = parseInt(text, 10);
        if (val > 10000) return val;
      }
    }
    return null;
  }

  function getAddress() {
    const selectors = [
      '[data-testid="bdp-building-address"]',
      'h1[class*="address"]',
      '.street-address',
      'h1',
    ];
    for (const sel of selectors) {
      const el = document.querySelector(sel);
      if (el) return el.textContent.trim();
    }
    return window.location.href;
  }

  function calcDeal(listPrice) {
    const arv      = Math.round(listPrice * 1.10);
    const repairs  = Math.round(listPrice * REPAIR_RATE);
    const mao      = Math.round(arv * MAO_FACTOR - repairs - 10000);
    const spread   = listPrice - mao;
    const margin   = mao > 0 ? ((arv - listPrice - repairs - 10000) / arv * 100).toFixed(1) : 0;
    const isDeal   = listPrice <= mao;
    return { listPrice, arv, repairs, mao, spread, margin, isDeal };
  }

  function createOverlay(d) {
    const existing = document.getElementById('woa-overlay');
    if (existing) existing.remove();

    const tier  = d.isDeal ? '🔥 DEAL' : d.spread < 20000 ? '🌡 CLOSE' : '❄ PASS';
    const color = d.isDeal ? '#27ae60' : d.spread < 20000 ? '#e67e22' : '#c0392b';

    const panel = document.createElement('div');
    panel.id = 'woa-overlay';
    panel.innerHTML = `
      <div class="woa-header" style="background:${color}">
        <span class="woa-title">Deal Analyzer</span>
        <span class="woa-tier">${tier}</span>
        <button class="woa-close" onclick="document.getElementById('woa-overlay').remove()">✕</button>
      </div>
      <div class="woa-body">
        <div class="woa-row"><span>List Price</span><strong>$${d.listPrice.toLocaleString()}</strong></div>
        <div class="woa-row"><span>Est. ARV</span><strong>$${d.arv.toLocaleString()}</strong></div>
        <div class="woa-row"><span>Est. Repairs</span><strong>$${d.repairs.toLocaleString()}</strong></div>
        <div class="woa-row woa-highlight"><span>Max Offer (MAO)</span><strong>$${d.mao.toLocaleString()}</strong></div>
        <div class="woa-row"><span>Spread vs List</span><strong style="color:${d.isDeal?'#27ae60':'#c0392b'}">
          ${d.isDeal ? '+' : '-'}$${Math.abs(d.spread).toLocaleString()}</strong></div>
        <div class="woa-row"><span>Profit Margin</span><strong>${d.margin}%</strong></div>
        <div class="woa-footer">Powered by Wholesale Omniverse</div>
      </div>`;
    document.body.appendChild(panel);
  }

  function run() {
    const price = getListingPrice();
    if (!price) return;
    const deal = calcDeal(price);
    createOverlay(deal);

    // Persist to extension storage for popup access
    chrome.storage.local.set({
      lastAddress: getAddress(),
      lastDeal: deal,
      lastSeen: new Date().toISOString(),
    });
  }

  // Run on page load and observe for SPA navigation
  run();
  let lastUrl = location.href;
  new MutationObserver(() => {
    if (location.href !== lastUrl) { lastUrl = location.href; setTimeout(run, 1500); }
  }).observe(document, { subtree: true, childList: true });
})();
"""

STYLES_CSS = """
#woa-overlay {
  position: fixed;
  top: 80px;
  right: 20px;
  width: 280px;
  background: #fff;
  border-radius: 10px;
  box-shadow: 0 4px 20px rgba(0,0,0,.25);
  z-index: 99999;
  font-family: 'Segoe UI', sans-serif;
  font-size: 14px;
  overflow: hidden;
}
.woa-header {
  display: flex;
  align-items: center;
  padding: 10px 12px;
  color: #fff;
  font-weight: 700;
}
.woa-title { flex: 1; }
.woa-tier  { background: rgba(255,255,255,.2); padding: 2px 8px; border-radius: 20px; font-size: 12px; }
.woa-close { background: none; border: none; color: #fff; cursor: pointer; font-size: 16px; margin-left: 8px; }
.woa-body  { padding: 12px; }
.woa-row   { display: flex; justify-content: space-between; padding: 5px 0; border-bottom: 1px solid #f0f0f0; }
.woa-highlight { background: #f4f7fb; padding: 6px 4px; border-radius: 4px; font-weight: 700; }
.woa-footer { text-align: center; color: #aaa; font-size: 11px; margin-top: 8px; }
"""

POPUP_HTML = """<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <style>
    body { width: 280px; padding: 12px; font-family: 'Segoe UI', sans-serif; font-size: 13px; }
    h2   { color: #1a3c6e; margin: 0 0 10px; }
    .row { display: flex; justify-content: space-between; padding: 5px 0; border-bottom: 1px solid #eee; }
    .row strong { color: #1a3c6e; }
    #status { color: #aaa; font-size: 11px; margin-top: 8px; }
    input  { width: 100%; padding: 6px; border: 1px solid #ddd; border-radius: 4px; margin: 4px 0 8px; }
    button { background: #1a3c6e; color: #fff; border: none; border-radius: 4px;
             padding: 7px 14px; cursor: pointer; font-size: 13px; }
  </style>
</head>
<body>
  <h2>Deal Analyzer</h2>
  <div id="last-deal"></div>
  <hr style="margin:10px 0">
  <label>Custom Price ($)</label>
  <input id="custom-price" type="number" placeholder="e.g. 150000">
  <button onclick="calcCustom()">Analyze</button>
  <div id="custom-result"></div>
  <div id="status"></div>
  <script src="popup.js"></script>
</body>
</html>"""

POPUP_JS = """
const MAO_FACTOR  = 0.70;
const REPAIR_RATE = 0.05;

function calcDeal(price) {
  const arv     = Math.round(price * 1.10);
  const repairs = Math.round(price * REPAIR_RATE);
  const mao     = Math.round(arv * MAO_FACTOR - repairs - 10000);
  const spread  = price - mao;
  const isDeal  = price <= mao;
  return { price, arv, repairs, mao, spread, isDeal };
}

function renderRows(d, container) {
  const color = d.isDeal ? '#27ae60' : '#c0392b';
  container.innerHTML = `
    <div class="row"><span>List Price</span><strong>$${d.price.toLocaleString()}</strong></div>
    <div class="row"><span>Est. ARV</span><strong>$${d.arv.toLocaleString()}</strong></div>
    <div class="row"><span>Est. Repairs</span><strong>$${d.repairs.toLocaleString()}</strong></div>
    <div class="row"><span>MAO</span><strong style="color:${color}">$${d.mao.toLocaleString()}</strong></div>
    <div class="row"><span>Spread</span><strong style="color:${color}">
      ${d.isDeal?'+':'-'}$${Math.abs(d.spread).toLocaleString()}</strong></div>`;
}

chrome.storage.local.get(['lastAddress','lastDeal','lastSeen'], (data) => {
  const el = document.getElementById('last-deal');
  if (data.lastDeal) {
    const d = data.lastDeal;
    el.innerHTML = `<div style="font-size:11px;color:#888;margin-bottom:4px">
      Last: ${(data.lastAddress||'').substring(0,35)}</div>`;
    renderRows(d, el);
    document.getElementById('status').textContent = 'Updated: ' + (data.lastSeen||'').substring(0,16);
  } else {
    el.textContent = 'Navigate to a Zillow or Redfin listing.';
  }
});

function calcCustom() {
  const price = parseInt(document.getElementById('custom-price').value, 10);
  if (!price) return;
  const d = calcDeal(price);
  renderRows(d, document.getElementById('custom-result'));
}
"""

BACKGROUND_JS = """
// Service worker — no network calls needed for v1 (all math is local)
chrome.runtime.onInstalled.addListener(() => {
  console.log('Wholesale Deal Analyzer installed.');
});
"""

README_MD = f"""# Wholesale Deal Analyzer — Chrome Extension

Version: {VERSION}
Built by: Wholesale Omniverse

## Install (Developer Mode)

1. Unzip this package
2. Open Chrome → `chrome://extensions`
3. Enable **Developer mode** (top right toggle)
4. Click **Load unpacked** → select the `src/` folder
5. Navigate to any Zillow or Redfin listing — the overlay appears automatically

## What It Shows

| Field | Description |
|-------|-------------|
| List Price | Current asking price from the listing |
| Est. ARV | List price × 1.10 (conservative 10% upside estimate) |
| Est. Repairs | List price × 5% |
| **MAO** | ARV × 70% − repairs − $10,000 assignment fee |
| Spread | List price vs MAO (positive = deal, negative = pass) |

## Pricing

- $47/mo individual
- $97/mo team (up to 5 users)

Contact: WholesaleOmniverse@gmail.com | 207-385-4041
"""


# ── Build logic ───────────────────────────────────────────────────────────────

def _write_src():
    (SRC_DIR / "manifest.json").write_text(
        json.dumps(MANIFEST, indent=2), encoding="utf-8"
    )
    (SRC_DIR / "content.js").write_text(CONTENT_JS,   encoding="utf-8")
    (SRC_DIR / "styles.css").write_text(STYLES_CSS,   encoding="utf-8")
    (SRC_DIR / "popup.html").write_text(POPUP_HTML,   encoding="utf-8")
    (SRC_DIR / "popup.js").write_text(POPUP_JS,       encoding="utf-8")
    (SRC_DIR / "background.js").write_text(BACKGROUND_JS, encoding="utf-8")

    # Stub PNG icons (1×1 transparent PNG — replace with real icons before publishing)
    PNG_STUB = bytes([
        0x89,0x50,0x4e,0x47,0x0d,0x0a,0x1a,0x0a,0x00,0x00,0x00,0x0d,0x49,0x48,0x44,0x52,
        0x00,0x00,0x00,0x01,0x00,0x00,0x00,0x01,0x08,0x06,0x00,0x00,0x00,0x1f,0x15,0xc4,
        0x89,0x00,0x00,0x00,0x0a,0x49,0x44,0x41,0x54,0x78,0x9c,0x62,0x00,0x00,0x00,0x02,
        0x00,0x01,0xe2,0x21,0xbc,0x33,0x00,0x00,0x00,0x00,0x49,0x45,0x4e,0x44,0xae,0x42,
        0x60,0x82,
    ])
    for size in (16, 48, 128):
        (SRC_DIR / f"icon{size}.png").write_bytes(PNG_STUB)


def _build_zip() -> Path:
    zip_path = PACKAGES_DIR / f"deal-analyzer-ext-{VERSION}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for fpath in SRC_DIR.iterdir():
            zf.write(fpath, fpath.name)
        zf.writestr("README.md", README_MD)
    return zip_path


def run_full_cycle() -> dict:
    _write_src()
    zip_path = _build_zip()

    built_at = datetime.now(timezone.utc).isoformat()
    index = {
        "version":    VERSION,
        "zip_path":   str(zip_path),
        "src_dir":    str(SRC_DIR),
        "built_at":   built_at,
        "files":      [f.name for f in SRC_DIR.iterdir()],
    }
    storage.save("cef_index.json", index)

    rev  = billing.revenue_summary(AGENT_KEY)
    subs = storage.load("cef_subscribers.json", [])
    metrics.record(
        AGENT_KEY,
        build_version=VERSION,
        active_subs=sum(1 for s in subs if s.get("status") == "active"),
        mrr=rev["mrr"],
    )

    return {
        "version":     VERSION,
        "zip_path":    str(zip_path),
        "src_dir":     str(SRC_DIR),
        "files_built": len(index["files"]),
        "mrr":         rev["mrr"],
    }
