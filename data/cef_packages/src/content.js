
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
