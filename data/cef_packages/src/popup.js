
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
