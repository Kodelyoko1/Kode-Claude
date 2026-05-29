// ─── Mobile menu toggle ──────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", () => {
  const toggle = document.querySelector(".menu-toggle");
  const nav = document.querySelector("nav.site-nav");
  if (toggle && nav) {
    toggle.addEventListener("click", () => nav.classList.toggle("open"));
  }

  // ─── Seller intake form ────────────────────────────────────────────────
  const form = document.querySelector("form.lead");
  if (form) {
    // Prefill city from ?city= URL param (used by Pinterest pins)
    const params = new URLSearchParams(window.location.search);
    const cityParam = params.get("city");
    if (cityParam) {
      const cityInput = form.querySelector('input[name="city"]');
      if (cityInput && !cityInput.value) {
        cityInput.value = cityParam.replace(/-/g, " ")
          .replace(/\b\w/g, c => c.toUpperCase());
      }
    }

    const alertOk    = form.querySelector(".alert.ok");
    const alertError = form.querySelector(".alert.error");
    const setAlert = (which, msg) => {
      if (alertOk)    { alertOk.style.display    = which === "ok"    ? "block" : "none"; if (msg) alertOk.textContent = msg; }
      if (alertError) { alertError.style.display = which === "error" ? "block" : "none"; if (msg) alertError.textContent = msg; }
    };

    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      setAlert("none");

      const formData = new FormData(form);
      const data = Object.fromEntries(formData.entries());
      const submitBtn = form.querySelector('button[type="submit"]');
      if (submitBtn) { submitBtn.disabled = true; submitBtn.textContent = "Sending..."; }

      // Netlify Forms expects URL-encoded body to "/"; legacy Flask /leads accepts JSON.
      const isNetlify = form.hasAttribute("data-netlify");
      const endpoint = form.dataset.endpoint || (isNetlify ? "/" : "/leads");
      const fetchOpts = isNetlify
        ? {
            method: "POST",
            headers: { "Content-Type": "application/x-www-form-urlencoded" },
            body: new URLSearchParams(formData).toString(),
          }
        : {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(data),
          };
      try {
        const resp = await fetch(endpoint, fetchOpts);
        if (resp.ok) {
          setAlert("ok",
            "Thanks! We received your info. Expect a call or text within 24 hours " +
            "with a cash offer. (You'll also get a confirmation email if you provided one.)");
          form.reset();
          if (submitBtn) submitBtn.textContent = "Sent ✓";
        } else {
          throw new Error(`HTTP ${resp.status}`);
        }
      } catch (err) {
        // Graceful fallback — open user's mail client with prefilled body
        const subject = encodeURIComponent("Cash offer request — " +
          (data.address || "property"));
        const body = encodeURIComponent(
          `Address: ${data.address || ""}\n` +
          `City: ${data.city || ""}, ${data.state || ""}  ${data.zip || ""}\n` +
          `Name: ${data.seller_name || ""}\n` +
          `Phone: ${data.seller_phone || ""}\n` +
          `Email: ${data.seller_email || ""}\n` +
          `Timeline: ${data.timeline || ""}\n` +
          `Condition: ${data.condition || ""}\n` +
          `Reason: ${data.reason || ""}\n\n` +
          `(Submitted from wholesaleomniverse.com)`);
        const mailto = `mailto:WholesaleOmniverse@gmail.com?subject=${subject}&body=${body}`;
        setAlert("ok",
          "Submitting via email instead — your default mail client should open. " +
          "If it doesn't, send your info to WholesaleOmniverse@gmail.com");
        window.location.href = mailto;
        if (submitBtn) { submitBtn.disabled = false; submitBtn.textContent = "Get my cash offer"; }
      }
    });
  }
});
