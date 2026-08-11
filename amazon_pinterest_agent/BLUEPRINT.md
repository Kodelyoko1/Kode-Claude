# Amazon Influencer × Pinterest Agent — Architecture Blueprint

Companion document to `amazon_pinterest_agent/` (code) and
`run_amazon_pinterest_auto.py` (entry point). This is the operational spec:
what the agent is, the exact system prompt driving its copywriting step, the
execution workflow, the compliance safeguards, and how to stand it up.

---

## 1. System Architecture & Capabilities

### Agent Name & Role
**PinCurator** — Wholesale Omniverse's autonomous Amazon Influencer ×
Pinterest agent. Its objective: keep a curated Amazon storefront collection
flowing onto Pinterest as compliant, SEO-tuned, high-intent pins — without a
human touching a keyboard between "product added to storefront" and "pin is
live" — while never risking the Associates account or the Pinterest account.

The agent explicitly does **not**:
- Auto-select products with no human curation upstream (it pins from a
  manifest the owner curates — this is a distribution engine, not a product
  discovery engine that free-lances new ASINs into the storefront).
- Self-modify its own compliance rules.
- Post faster than the cadence guard allows, even if instructed to via a
  CLI flag — `--max-pins` caps a batch, it doesn't bypass compliance.

### Core Modules

| Module | File | Responsibility |
|---|---|---|
| **Curation Engine** | `tools.py::curate_batch()` | Reads the owner-maintained storefront manifest, filters to active products outside their re-pin cooldown, rotates least-recently-pinned first |
| **Link Builder** | `tools.py::build_affiliate_link()` | Builds a direct, tagged `amazon.com/dp/<asin>/?tag=<associate_tag>` URL — never a cloaked or third-party-shortened redirect |
| **Content Creator** | `copywriter.py` | SEO title + description generation; heuristic template engine by default, upgrades to Claude `claude-sonnet-4-6` when `ANTHROPIC_API_KEY` is set |
| **Compliance & Anti-Spam Monitor** | `compliance.py::check_pin()` | Disclosure presence, direct-link check, banned-claim scan, hashtag cap, posting-cadence guard, duplicate-image throttle — runs on every pin before dispatch |
| **Pin Scheduler / Dispatcher** | `tools.py::_post_pin()`, `run_full_cycle()` | Routes each product's category to the right Pinterest board, posts via Pinterest API v5, logs every attempt (posted, blocked, skipped, failed) |
| **Performance Tracker** | `tools.py::fetch_performance()`, `top_performers()` | Pulls Pinterest pin analytics (impressions/saves/outbound clicks), ranks pins so the owner knows which storefront collections to expand |

### Tools & Integrations Required

| Integration | Required? | Purpose |
|---|---|---|
| **Pinterest API v5** (`api.pinterest.com/v5`) | Required to post | `/pins` (create), `/pins/{id}/analytics` (performance) — OAuth token with `pins:write`, `boards:read` scopes |
| **Amazon Associates / Influencer Program** | Required | Provides the `tag=` tracking parameter that turns a plain `amazon.com` link into an attributed, commissionable one. There is no public Associates *posting* API — product curation is a manifest the owner maintains (mirrors building storefront Idea Lists by hand) |
| **Anthropic API (Claude)** | Optional | `claude-sonnet-4-6` upgrades pin copy from template-based to model-drafted, same opt-in pattern as SEOWriter/ShowNotes. Falls back cleanly to heuristics when unset |
| **Gmail SMTP** (`autonomous.mailer`) | Optional | Not wired into this agent's default cycle, but available for a performance-digest email the same way every other agent in this repo sends digests |
| **n8n / Make.com** (optional, no-code layer) | Optional | See §5 — useful if the owner wants image generation (Canva API / Bannerbear) or scheduling UI on top of this Python core instead of cron |

---

## 2. System Instructions & Persona (Agent System Prompt)

This is the exact system prompt fed to Claude for the copywriting step
(`amazon_pinterest_agent/copywriter.py::SYSTEM_PROMPT` — keep this file and
that constant in sync if you edit either):

```
You are PinCurator, the copywriting module of Wholesale Omniverse's
Amazon Influencer x Pinterest agent. You write short-form Pinterest pin copy for
real Amazon storefront products. You are not a general marketing writer — you
operate under strict Amazon Associates and Pinterest rules, and every word you
write gets programmatically checked against them before anything is posted.

TONE
- Direct, specific, benefit-first. Sound like a person who actually uses the
  product, not an ad. No exclamation-point stacking, no emoji spam, no
  clickbait ("You won't BELIEVE...").
- Write like a helpful curator pointing someone at something useful, not a
  salesperson closing a deal.

SEO RULES
- Title: <=100 characters, leads with the primary keyword/product noun,
  no ALL CAPS, no keyword-stuffing.
- Description: <=420 characters (a disclosure line gets appended after you,
  so leave room and never write your own disclosure), keyword-rich but
  readable as a sentence a human would say out loud, ends with a soft
  call-to-action ("See current price + reviews on Amazon.").
- Use the product's stated audience/benefit/keywords fields verbatim where
  they fit naturally — don't invent claims not present in the product data.

COMPLIANCE — NON-NEGOTIABLE
- Never write "guaranteed," medical/cure claims, "FDA approved," or anything
  implying you are Amazon or an Amazon employee/partner.
- Never write your own disclosure text or hashtag — the disclosure line is
  appended automatically by the compliance module after your draft.
- Never invent product specs, reviews, or ratings not given to you.

OUTPUT FORMAT
Return exactly two lines, nothing else:
TITLE: <title text>
DESCRIPTION: <description text, no disclosure line>
```

### Decision-making process (what the orchestrator does around that prompt)

1. Load the storefront manifest → filter to `status: "active"` products
   outside their `AP_PRODUCT_REPIN_DAYS` cooldown.
2. Sort least-recently-pinned first, take the top `AP_MAX_PINS_PER_RUN`.
3. For each product: build the tagged link → generate copy (Claude if
   configured, else heuristic template) → **append the disclosure line
   deterministically in code, never trust the model for it**.
4. Run `compliance.check_pin()`. Any violation blocks that pin — it is
   logged as `blocked` with the specific violation codes, never silently
   dropped and never auto-"fixed" by stripping the offending text (a
   stripped disclosure is worse than a blocked pin).
5. Dispatch pins that pass, one at a time, persisting the log after each
   post so the cadence guard sees prior pins from *this* run too.
6. Record metrics (`posted`/`blocked`/`failed` counts) to the shared
   ecosystem dashboard.

### Disclosure rules (hard-coded, not left to the model)

- **Amazon Associates**: every pin must carry either the full sentence
  *"As an Amazon Associate I earn from qualifying purchases. #ad"* (used
  whenever there's character budget) or, on tightly constrained copy,
  Amazon's own accepted short form **`#CommissionsEarned`** — this mirrors
  Amazon's actual influencer guidance for character-constrained / real-time
  posts.
- **FTC**: `#ad` or `#CommissionsEarned` must be clearly visible, not buried
  after a wall of hashtags — `compliance.py` checks presence, not just
  existence-anywhere, and the copy template always puts it in its own line
  at the end.
- **Pinterest**: descriptions capped at 500 characters total (Pinterest's
  practical limit before truncation in feed); hashtag count capped at 20.

---

## 3. Step-by-Step Execution Workflow

```
┌─────────────────────────────────────────────────────────────────────┐
│  1. PRODUCT SELECTION & LINK GENERATION                              │
│     owner curates data/ap_storefront.json (ASIN, category, summary,  │
│     audience, benefit, keywords, image_url, collection)              │
│     → curate_batch() picks N eligible products, least-recent first   │
│     → build_affiliate_link(asin, tag) → tagged amazon.com/dp/ link   │
├─────────────────────────────────────────────────────────────────────┤
│  2. VISUAL ASSET                                                     │
│     v1: uses the product's existing Amazon listing image (image_url  │
│         in the manifest — pulled once by the owner from the listing) │
│     v1.5 (optional, no-code): Canva API / Bannerbear template render │
│         a branded overlay (price, "As seen on Amazon" badge) — see   │
│         §5 for the n8n wiring; the Python core just needs a final    │
│         image_url written back into the manifest                    │
├─────────────────────────────────────────────────────────────────────┤
│  3. COPYWRITING                                                      │
│     copywriter.generate_pin_copy(product)                            │
│     → Claude (if ANTHROPIC_API_KEY set) or heuristic template        │
│     → compliance.disclosure_for() appends the disclosure line        │
├─────────────────────────────────────────────────────────────────────┤
│  4. COMPLIANCE CHECK (blocking gate)                                 │
│     compliance.check_pin(pin, dispatch_log)                          │
│     disclosure? direct link? banned claims? hashtag cap?             │
│     cadence ok? image not reused too recently?                       │
│     → any violation: pin logged as "blocked", cycle continues        │
├─────────────────────────────────────────────────────────────────────┤
│  5. SCHEDULING / POSTING                                             │
│     _resolve_board(category) → AP_BOARD_MAP or PINTEREST_BOARD_ID    │
│     → POST /v5/pins (Pinterest API)                                  │
│     → append to data/ap_pins.json immediately (cadence source)       │
├─────────────────────────────────────────────────────────────────────┤
│  6. PERFORMANCE TRACKING (separate cadence — run weekly)             │
│     fetch_performance() → GET /v5/pins/{id}/analytics per posted pin │
│     top_performers() → rank by outbound (affiliate) clicks           │
│     owner acts on the ranking: expand winning collections, retire    │
│     ASINs with zero clicks after N pins                              │
└─────────────────────────────────────────────────────────────────────┘
```

### Recommended cadence

| Job | Frequency | Command |
|---|---|---|
| Curate + pin | Daily (1x), spread naturally by the 20-min-min cadence guard if you run it more than once | `python3 run_amazon_pinterest_auto.py` |
| Performance pull | Weekly | `python3 run_amazon_pinterest_auto.py --performance` |
| Status check | Ad hoc | `python3 run_amazon_pinterest_auto.py --status` |

Cron wrapper (add to `run_all_autonomous_agents.sh` or its own line):
```bash
export $(grep -v '^#' .env | xargs) && python3 run_amazon_pinterest_auto.py
```

---

## 4. Safety, Compliance & Anti-Spam Safeguards

### Pinterest shadowban / spam avoidance
- **Cadence guard** — `AP_MIN_SECONDS_BETWEEN_PINS` (default 20 min) between
  posts. Accounts that post in bursts get algorithmically suppressed.
- **Duplicate-image throttle** — `AP_IMAGE_REUSE_COOLDOWN_DAYS` (default 14)
  blocks re-pinning the exact same product image inside the cooldown window.
  Pinterest's spam heuristics key heavily on repeated identical media.
- **Hashtag cap** — 20 max; over-tagging is a spam signal Pinterest's own
  creator guidelines flag explicitly.
- **Board routing by category** (`AP_BOARD_MAP`) — keeps pins topically
  coherent per board instead of one board absorbing every category, which
  both helps SEO relevance and avoids the "off-topic board" spam pattern.
- **Direct amazon.com / amzn.to links only** — never a link shortener or
  redirector domain Pinterest doesn't recognize; unfamiliar redirect chains
  are one of Pinterest's stronger spam signals.
- **No incentivized engagement** — the agent never generates copy asking
  people to "like/save/share to win" — a bannable pattern under Pinterest's
  spam policy.

### Amazon Influencer / Associates compliance
- **Disclosure required on every pin, always** — enforced in code
  (`compliance.check_pin`), not left to the LLM. A pin missing disclosure
  never leaves `blocked` status.
- **Direct-linking policy** — links must land on amazon's own domain with
  the tracking tag visible (`_is_direct_amazon_link()`); no cloaking, no
  affiliate-link masking services.
- **No false claims** — banned-pattern scan blocks "guaranteed," medical/cure
  language, "FDA approved," and any wording implying Amazon
  employment/partnership (`BANNED_CLAIM_PATTERNS`).
- **No fabricated specs/ratings** — the copywriting system prompt explicitly
  forbids inventing details not present in the product manifest.
- **Image usage rights** — the manifest's `image_url` should be the
  product's own Amazon listing image (which Associates are permitted to use
  in the course of promoting the linked product) or an owner-shot photo.
  Never scrape a third party's original photography.
- **Cooldown between re-pins of the same ASIN** (`AP_PRODUCT_REPIN_DAYS`,
  default 21) — keeps the account from reading as a repost bot and mirrors
  Amazon's expectation of genuine, varied content rather than mechanical
  reposting.

### Operational safety
- Every dispatch attempt — posted, blocked, skipped, or failed — is logged
  to `data/ap_pins.json` with a reason, so nothing fails silently.
- The cycle is wrapped in this repo's `autonomous.self_healing` decorator:
  transient network errors get retried with backoff, corrupted JSON gets
  partially recovered rather than nuked, and the owner gets an email after
  3 consecutive failed cycles.
- Owner always bypasses the paywall via `AGENT_PASSWORD`; external
  subscribers go through the standard `paywall.agent_paywall` gate.

---

## 5. Recommended Tech Stack & Implementation Guide

### What's already built (this repo, ready to run)
```
amazon_pinterest_agent/
  compliance.py     # disclosure, link, spam guardrails — pure functions, no network
  copywriter.py      # SEO title/description generator (heuristic + optional Claude)
  tools.py           # curation, link building, dispatch, performance tracking
run_amazon_pinterest_auto.py   # CLI entry point (paywall + self-healing wrapped)
```

**Setup:**
```bash
# 1. Pinterest OAuth token (reuses the repo's existing wizard)
python3 setup_pinterest.py

# 2. Add to .env
AMAZON_ASSOCIATE_TAG=your-associate-tag-20
PINTEREST_ACCESS_TOKEN=...        # from step 1
PINTEREST_BOARD_ID=...            # fallback board
AP_BOARD_MAP={"kitchen":"...","fitness":"...","default":"..."}

# 3. Curate your storefront manifest at data/ap_storefront.json:
{
  "associate_tag": "your-associate-tag-20",
  "products": [
    {
      "asin": "B0EXAMPLE1",
      "title": "Compact Stand Mixer",
      "category": "kitchen",
      "summary": "6-speed, dishwasher-safe bowl, fits in a small cabinet.",
      "audience": "small-kitchen home bakers",
      "benefit": "actually fits your counter",
      "keywords": ["stand mixer", "compact kitchen appliance"],
      "image_url": "https://m.media-amazon.com/images/I/EXAMPLE.jpg",
      "collection": "Kitchen Favorites",
      "status": "active"
    }
  ]
}

# 4. Dry-run first
python3 run_amazon_pinterest_auto.py --dry-run

# 5. Go live
python3 run_amazon_pinterest_auto.py
```

### Optional no-code layer (n8n / Make.com) on top of this core

The Python core is the source of truth for compliance and dispatch — keep
the compliance gate in code, never in a no-code tool, since that's the part
that must never be skipped. n8n/Make are useful for the *upstream* pieces
that benefit from a visual builder:

```
[Google Sheet: new ASIN row added]
        │  (Sheets trigger)
        ▼
[HTTP node: fetch Amazon product data via SiteStripe-copied fields]
        │
        ▼
[Canva API / Bannerbear node: render branded pin image from template]
        │
        ▼
[HTTP node: append {asin, image_url, category, ...} to
             data/ap_storefront.json via a small FastAPI shim, OR
             write directly into a shared Sheet the Python agent reads]
        │
        ▼
[Cron trigger, daily 9am] → [Execute Command node:
             `python3 run_amazon_pinterest_auto.py`]
        │
        ▼
[Weekly cron] → [Execute Command: `--performance`] →
             [Slack/Email node: post top_performers() ranking]
```

If you'd rather skip n8n entirely, cron + this repo's existing
`run_all_autonomous_agents.sh` pattern is sufficient — that's the default
this agent ships wired for.

### Why not a fully no-code build?
Amazon does not expose a public API for the Influencer Program (storefront
curation is manual by design on Amazon's side), and Pinterest's compliance
requirements (disclosure presence, link-cloaking rules, spam cadence) are
exactly the kind of logic that's fragile and hard to audit inside a
drag-and-drop tool. Keeping curation-input flexible (no-code friendly) while
keeping compliance + dispatch in versioned, testable Python is the safer
split.
