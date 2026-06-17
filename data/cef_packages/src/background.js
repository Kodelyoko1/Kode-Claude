
// Service worker — no network calls needed for v1 (all math is local)
chrome.runtime.onInstalled.addListener(() => {
  console.log('Wholesale Deal Analyzer installed.');
});
