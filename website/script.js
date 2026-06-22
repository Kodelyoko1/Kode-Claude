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

  // ─── 1. Canvas Particle System ───────────────────────────────────────────
  const canvas = document.querySelector("canvas#hero-canvas");
  if (canvas) {
    const ctx = canvas.getContext("2d");
    const PARTICLE_COUNT = 80;
    const CONNECT_DIST   = 120;
    const REPEL_DIST     = 100;
    const REPEL_STRENGTH = 0.3;
    const COLORS         = ["#06b6d4", "#8b5cf6"];

    let mouse = { x: null, y: null };
    let W, H;

    const resize = () => {
      const hero = canvas.parentElement;
      W = canvas.width  = hero ? hero.offsetWidth  : window.innerWidth;
      H = canvas.height = hero ? hero.offsetHeight : window.innerHeight;
    };
    resize();
    window.addEventListener("resize", resize);

    canvas.addEventListener("mousemove", (e) => {
      const rect = canvas.getBoundingClientRect();
      mouse.x = e.clientX - rect.left;
      mouse.y = e.clientY - rect.top;
    });
    canvas.addEventListener("mouseleave", () => { mouse.x = null; mouse.y = null; });

    const rand = (min, max) => Math.random() * (max - min) + min;

    const particles = Array.from({ length: PARTICLE_COUNT }, () => ({
      x:       rand(0, W),
      y:       rand(0, H),
      vx:      rand(-0.4, 0.4),
      vy:      rand(-0.4, 0.4),
      radius:  rand(1, 2.5),
      opacity: rand(0.2, 0.6),
      color:   COLORS[Math.random() < 0.5 ? 0 : 1],
    }));

    const hexToRgb = (hex) => {
      const r = parseInt(hex.slice(1, 3), 16);
      const g = parseInt(hex.slice(3, 5), 16);
      const b = parseInt(hex.slice(5, 7), 16);
      return `${r},${g},${b}`;
    };

    const animate = () => {
      ctx.clearRect(0, 0, W, H);

      for (let i = 0; i < particles.length; i++) {
        const p = particles[i];

        // Mouse repel
        if (mouse.x !== null) {
          const dx = p.x - mouse.x;
          const dy = p.y - mouse.y;
          const dist = Math.sqrt(dx * dx + dy * dy);
          if (dist < REPEL_DIST && dist > 0) {
            const force = (REPEL_DIST - dist) / REPEL_DIST * REPEL_STRENGTH;
            p.vx += (dx / dist) * force;
            p.vy += (dy / dist) * force;
          }
        }

        // Speed cap
        const speed = Math.sqrt(p.vx * p.vx + p.vy * p.vy);
        if (speed > 1.5) { p.vx = (p.vx / speed) * 1.5; p.vy = (p.vy / speed) * 1.5; }

        p.x += p.vx;
        p.y += p.vy;

        // Bounce off walls
        if (p.x < p.radius)     { p.x = p.radius;      p.vx *= -1; }
        if (p.x > W - p.radius) { p.x = W - p.radius;  p.vx *= -1; }
        if (p.y < p.radius)     { p.y = p.radius;       p.vy *= -1; }
        if (p.y > H - p.radius) { p.y = H - p.radius;  p.vy *= -1; }

        // Draw particle
        ctx.beginPath();
        ctx.arc(p.x, p.y, p.radius, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(${hexToRgb(p.color)},${p.opacity})`;
        ctx.fill();

        // Draw connecting lines to nearby particles
        for (let j = i + 1; j < particles.length; j++) {
          const q = particles[j];
          const dx = p.x - q.x;
          const dy = p.y - q.y;
          const dist = Math.sqrt(dx * dx + dy * dy);
          if (dist < CONNECT_DIST) {
            const lineOpacity = (1 - dist / CONNECT_DIST) * 0.35;
            ctx.beginPath();
            ctx.moveTo(p.x, p.y);
            ctx.lineTo(q.x, q.y);
            ctx.strokeStyle = `rgba(${hexToRgb(p.color)},${lineOpacity})`;
            ctx.lineWidth = 0.8;
            ctx.stroke();
          }
        }
      }

      requestAnimationFrame(animate);
    };

    animate();
  }

  // ─── 2. Typewriter Effect ────────────────────────────────────────────────
  const typewriterEl = document.querySelector("span#typewriter");
  if (typewriterEl) {
    const strings = [
      "autonomous revenue agents.",
      "40+ AI agents. Zero manual work.",
      "real estate. content. e-commerce.",
      "your business on autopilot.",
    ];
    const TYPE_SPEED   = 60;
    const DELETE_SPEED = 30;
    const PAUSE_MS     = 2000;

    let strIndex  = 0;
    let charIndex = 0;
    let deleting  = false;

    // Inject blinking cursor as a sibling <span> so the typewriter text stays clean
    const cursorSpan = document.createElement("span");
    cursorSpan.textContent = "|";
    cursorSpan.style.cssText =
      "display:inline-block;margin-left:1px;animation:twBlink 0.75s step-end infinite;";
    typewriterEl.insertAdjacentElement("afterend", cursorSpan);

    // Inject keyframes if not already present
    if (!document.querySelector("style#tw-blink-style")) {
      const style = document.createElement("style");
      style.id = "tw-blink-style";
      style.textContent = "@keyframes twBlink { 0%,100%{opacity:1} 50%{opacity:0} }";
      document.head.appendChild(style);
    }

    const tick = () => {
      const current = strings[strIndex];
      if (deleting) {
        charIndex--;
        typewriterEl.textContent = current.slice(0, charIndex);
        if (charIndex === 0) {
          deleting = false;
          strIndex = (strIndex + 1) % strings.length;
          setTimeout(tick, TYPE_SPEED);
        } else {
          setTimeout(tick, DELETE_SPEED);
        }
      } else {
        charIndex++;
        typewriterEl.textContent = current.slice(0, charIndex);
        if (charIndex === current.length) {
          deleting = true;
          setTimeout(tick, PAUSE_MS);
        } else {
          setTimeout(tick, TYPE_SPEED);
        }
      }
    };

    setTimeout(tick, TYPE_SPEED);
  }

  // ─── 3. Scroll-Reveal ────────────────────────────────────────────────────
  const revealEls = document.querySelectorAll(".reveal");
  if (revealEls.length > 0) {
    const revealObserver = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            entry.target.classList.add("revealed");
            revealObserver.unobserve(entry.target);
          }
        });
      },
      { threshold: 0.1, rootMargin: "0px 0px -60px 0px" }
    );
    revealEls.forEach((el) => revealObserver.observe(el));
  }

  // ─── 4. Animated Stats Counters ──────────────────────────────────────────
  const counterEls = document.querySelectorAll("span.counter[data-target]");
  if (counterEls.length > 0) {
    const easeOutQuart = (t) => 1 - Math.pow(1 - t, 4);

    const animateCounter = (el) => {
      const target   = parseFloat(el.dataset.target) || 0;
      const suffix   = el.dataset.suffix || "";
      const duration = 1800;
      const start    = performance.now();

      const step = (now) => {
        const elapsed  = now - start;
        const progress = Math.min(elapsed / duration, 1);
        const value    = Math.round(easeOutQuart(progress) * target);
        el.textContent = value + suffix;
        if (progress < 1) requestAnimationFrame(step);
      };

      requestAnimationFrame(step);
    };

    const counterObserver = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            animateCounter(entry.target);
            counterObserver.unobserve(entry.target);
          }
        });
      },
      { threshold: 0.15 }
    );

    counterEls.forEach((el) => counterObserver.observe(el));
  }

  // ─── 5. Active Nav Highlight ─────────────────────────────────────────────
  const navLinks = document.querySelectorAll("nav.site-nav a");
  if (navLinks.length > 0) {
    const currentPath = window.location.pathname.replace(/\/$/, "") || "/";
    navLinks.forEach((link) => {
      const linkPath =
        new URL(link.href, window.location.origin).pathname.replace(/\/$/, "") || "/";
      if (linkPath === currentPath) {
        link.classList.add("nav-active");
        link.style.color      = "#06b6d4";
        link.style.fontWeight = "700";
      }
    });
  }

  // ─── 6. Header scroll effect ─────────────────────────────────────────────
  const siteHeader = document.querySelector("header.site");
  if (siteHeader) {
    // Inject scrolled styles dynamically so no CSS file edits are required
    if (!document.querySelector("style#header-scroll-style")) {
      const style = document.createElement("style");
      style.id = "header-scroll-style";
      style.textContent =
        "header.site.scrolled{" +
        "background:rgba(3,7,18,0.97)!important;" +
        "box-shadow:0 1px 0 rgba(6,182,212,0.2)!important;" +
        "}";
      document.head.appendChild(style);
    }

    const onScroll = () => {
      if (window.scrollY > 40) {
        siteHeader.classList.add("scrolled");
      } else {
        siteHeader.classList.remove("scrolled");
      }
    };

    window.addEventListener("scroll", onScroll, { passive: true });
    onScroll(); // Run once on load in case page is already scrolled
  }
});
