# Wholesale Omniverse — Master Project Guide

**Company:** Wholesale Omniverse LLC  
**Owner:** tylumiere25@gmail.com  
**Business Email:** WholesaleOmniverse@gmail.com  
**Phone:** 207-385-4041  
**PayPal.me:** paypal.me/wholesaleomniverse

---

## Project Architecture

This repo started as a real-estate wholesale system and grew into a multi-agent autonomous revenue platform. Every agent runs without requiring manual API calls — they operate on schedules or via direct script execution.

### Core Principle
- No extra API keys required to *run* most agents (free web scraping, open data, Gmail SMTP). Paid integrations (Meta Ads, Anthropic, Twilio) are opt-in per agent and gated behind env vars.
- All agents are paywall-gated for external clients; owner bypasses paywall with `AGENT_PASSWORD`
- PayPal integration handles all billing (Invoice → Checkout → PayPal.me fallback); Stripe wired in for some agents
- All data stored as flat JSON files in `/data/` (per-agent subdirs use 2-letter prefixes, e.g. `sf_` for ShortsForge, `vr_` for ViralRecycler)
- Every agent has the same shape: `<agent>/tools.py` (logic) + `run_<agent>_auto.py` (entry point) + `paywall.agent_paywall.paywall_prompt` gate

---

## Real Estate Engines

### Cash Buyer Finder (`buyer_finder/`) — $97/mo
Scrapes REIA association websites + Hotfrog business directory to find real estate investors. Emails intro messages to build a buyers list.  
**Run:** `python3 run_buyer_finder_auto.py`  
**Data:** `data/cash_buyers.json`

### Seller Follow-Up Sequence (`followup_agent/`) — $147/mo
6-touch automated email follow-up sequence for motivated seller leads (day 3, 7, 14, 21, 30, 60). Tracks hot leads that respond.  
**Run:** `python3 run_followup_auto.py`  
**Data:** `data/leads.json`, `data/email_log.json`  
**Schedule:** `FOLLOWUP_SCHEDULE = {0: 3, 1: 4, 2: 7, 3: 7, 4: 9, 5: 30}`

### Outreach-as-a-Service (`outreach_service/`) — $300–$800/mo (tiered)
Sells prospecting campaigns to other wholesalers/investors. Runs gov-record prospecting for retainer clients and emails them weekly reports.  
**Tiers:** basic $300 (1 market), standard $500 (2 markets), premium $800 (4 markets)  
**Run:** `python3 run_outreach_auto.py`  
**Data:** `data/outreach_clients.json`, `data/outreach_campaigns.json`

### Wholesale Deal Analyzer (`agent.py`, `tools.py`) — $197/mo
AI-powered deal analysis using Claude `claude-sonnet-4-6`. Pulls comps, estimates ARV, calculates max offer, generates LOIs, emails sellers.  
**Run:** `python3 main.py` (chat) or `python3 main.py --auto` (autonomous)  
**Data:** `data/leads.json`, `data/contracts.json`

### Client Prospector (`client_prospector/`)
Finds *paying clients* for the Deal Analyzer (SAAS) and Outreach-as-a-Service (OAS). Reuses `buyer_finder` scraping infra but with sales-pitch emails.  
**Run:** `python3 run_prospector_auto.py [--product saas|oas]`  
**Data:** `data/prospects.json`, `data/pitch_log.json`

### HUDScout (`hudscout/`) — $97/mo, $297 quarterly retainer, $497 white-label market pack
HUD Home Store REO scraper for the wholesale pipeline. HUD-owned former-FHA homes are heavily discounted and have a public bid period where investors can win below ARV. Hits the same JSON endpoint (`POST /SearchResult?handler=GetFilteredResult`) the site's own JS uses; bootstraps a session at `/searchresult` to capture the antiforgery token + cookie, then sweeps each configured state. Normalized listings land in `data/hd_leads.json` (per-agent dedupe store keyed by HUD case number) AND `data/leads.json` as `LEAD-NNNN` records with `lead_source: HUDScout`, so the wholesale Deal Analyzer picks them up. Owner + paying subs get a daily markdown digest.  
**Run:** `python3 run_hudscout_auto.py`  
**Env:** `HD_STATES="ME,NH,VT,MA"` (postal codes or full names), `HD_SEARCH_TIMEOUT=20`, `HD_DIGEST_DOW=-1` (-1 = every day; 0 = Monday only)  
**Data:** `data/hd_leads.json`, `data/hd_outputs/YYYY-MM-DD.md`, appends to `data/leads.json`  
**Resilience:** if 0 listings across all states for several consecutive runs, HUD's JSON contract or antiforgery flow changed — patch `_open_session()` (token capture) and `_normalize_property()` (JSON field mapping) in `hudscout/tools.py`.

---

## Content & Publishing Agents

### StoryForge (`storyforge/`) — $19/$49/$197
Writers' coaching agent: daily prompts, consistency tracking, full story-bible orders.  
**Run:** `python3 run_storyforge_auto.py`  
**Data:** `data/sf_projects/`

### GutenbergVoice (`gutenberg_voice/`) — $19/$97/$297/$29-mo
Turns public-domain Gutenberg texts into narration-ready scripts (chapter packs, full kits, weekly Script of the Week).  
**Run:** `python3 run_gutenberg_voice_auto.py`  
**Data:** `data/gv_texts/`, `data/gv_scripts/`, `data/gv_listings/`

### PaperBrief (`paperbrief/`) — $39/mo, $399/yr, $999/yr enterprise
Vertical research-paper summarization newsletter; ingests PDFs, emits per-vertical briefs.  
**Run:** `python3 run_paperbrief_auto.py`  
**Data:** `data/pb_pdfs/`, `data/pb_briefs/`

### Transcribe (`transcribe/`) — $19/episode, $79/mo (10 hrs), $297 bulk pack
Bulk audio/video → `.txt` + `.srt` for podcasters and video creators. Owner drops files into `data/tr_inputs/`; outputs go to `data/tr_outputs/` and are auto-consumed by ShowNotes. Uses `faster-whisper` (CPU int8) + ffmpeg.  
**Run:** `python3 run_transcribe_auto.py`  
**Data:** `data/tr_inputs/`, `data/tr_outputs/`

### ShowNotes (`shownotes/`) — $29/episode, $99/mo (4 eps), $297/mo unlimited
Transcript → structured show notes (TL;DR, key takeaways, chapter timestamps from SRT, resource links, SEO title + description). Auto-ingests from `data/tr_outputs/` (Transcribe) or owner-dropped files in `data/sn_inputs/`. Heuristics-only by default; uses Claude `claude-sonnet-4-6` for the TL;DR step if `ANTHROPIC_API_KEY` is set.  
**Run:** `python3 run_shownotes_auto.py`  
**Data:** `data/sn_inputs/`, `data/sn_outputs/`

### ThumbForge (`thumbforge/`) — $9/thumb, $49/mo (10), $199 bulk 30-pack
Pillow-based CTR-tuned thumbnail renderer. Owner drops `data/tf_inputs/{slug}.json` with `{title, subtitle, niche, accent, shorts}`. Outputs 1280×720 YouTube thumbnail and (optional) 1080×1920 Shorts variant. Niche palettes baked in: motivational, tech, wellness, comedy, finance, generic.  
**Run:** `python3 run_thumbforge_auto.py`  
**Data:** `data/tf_inputs/`, `data/tf_outputs/`

### CarouselForge (`carouselforge/`) — $29/carousel, $99/mo (4), $297/mo unlimited
Pillow-based LinkedIn/Instagram/Pinterest carousel renderer. Two input sources: owner-dropped `data/cr_inputs/{slug}.json` with `{title, slides, theme, platform, handle, cta}`, *or* auto-ingest from `data/sn_outputs/*.md` (ShowNotes takeaways → carousel slides). Outputs cover + body slides + CTA into `data/cr_outputs/{slug}/`. Themes: dark, light, brand. Platforms: ig (1080²), li (1080×1350), pinterest (1000×1500).  
**Run:** `python3 run_carouselforge_auto.py`  
**Data:** `data/cr_inputs/`, `data/cr_outputs/`, auto-source `data/sn_outputs/`

### SEOWriter (`seowriter/`) — $39/article, $149/mo (5), $499/mo unlimited
Keyword → structured SEO article draft (H1 + intro + 4–6 H2 sections + FAQ + conclusion + meta tags). Owner drops `data/sw_inputs/{slug}.json`, *or* auto-ingests from `data/pb_briefs/*.md` (PaperBrief → SEO long-form). Heuristic skeleton by default; full Claude `claude-sonnet-4-6` drafts if `ANTHROPIC_API_KEY` is set.  
**Run:** `python3 run_seowriter_auto.py`  
**Data:** `data/sw_inputs/`, `data/sw_outputs/`, auto-source `data/pb_briefs/`

### SpeedAudit (`speedaudit/`) — $77 one-time, $37/mo monitoring, $297 quarterly retainer
Website performance audit. Fetches the URL, parses HTML, scores 0–100 across: TTFB, HTML payload, gzip/brotli, Cache-Control, HTTPS+HSTS, non-WebP images, render-blocking scripts/CSS, X-Powered-By leakage, redirect chains. Emits a prioritized fix list with point-impact tagging. Lead-magnet flow: drop a URL into `sa_leads.json` and a free preview audit gets emailed.  
**Run:** `python3 run_speedaudit_auto.py`  
**Data:** `data/sa_inputs/`, `data/sa_outputs/`, `data/sa_leads.json`

### InboxZero (`inboxzero/`) — $97/mo per inbox, $297/mo team, $97 deep-clean
Autonomous IMAP triage for the owner's Gmail (re-uses existing `SMTP_USER`/`SMTP_PASS` app password — no OAuth needed). Categorizes unread mail into urgent / important / promo / newsletter / social / other; archives promo+newsletter+social, flags urgent, leaves the rest. Emails a daily digest to `IZ_OWNER_EMAIL` (defaults to `SMTP_USER`). Mass-mail headers (`List-Unsubscribe`) always demote "urgent-sounding" subjects to promo. Optional Claude escalation for ambiguous "other" messages.  
**Run:** `python3 run_inboxzero_auto.py`  
**Env:** `SMTP_USER`, `SMTP_PASS`, optional `IZ_FETCH_LIMIT` (default 50), `IZ_OWNER_EMAIL`

### CourseForge (`courseforge/`) — $29 self-publish kit, $99 done-for-you, $297/mo white-label
Packages outputs from other agents (ShowNotes, Transcribe, SEOWriter, PaperBrief, GutenbergVoice, StoryForge) into upload-ready mini-courses for Gumroad / Payhip / Udemy. Owner drops a manifest in `data/co_inputs/{slug}.json` with `{title, modules: [{lesson, source}], price, platform}`. Source paths are resolved relative to `data/`. Outputs to `data/co_outputs/{slug}/` with `README.md`, `landing_page.md`, `module_NN.md` for each lesson, and `manifest.json`.  
**Run:** `python3 run_courseforge_auto.py`  
**Data:** `data/co_inputs/`, `data/co_outputs/` (note: `co_` prefix, since `cf_` is CareerForge)

### Localize (`localize/`) — $19/page, $49/mo (5 pages), $199/mo unlimited
Translation + localization for marketing/technical/conversational content. Owner drops `data/lz_inputs/{slug}.json` with `{source_text, source_lang, target_langs, purpose, audience, tone}`. Auto-source: any `data/sw_outputs/*.md` (SEOWriter) and `data/nl_newsletters/*` files when `data/lz_config.json` lists `target_langs` + `auto_sources`. 20 language codes supported with per-language culture notes (date/currency formats, RTL flags, idiom warnings). Claude `claude-sonnet-4-6` powers the translation; falls back to a `[NEEDS_TRANSLATION_TO_X]` marker file with the failure reason logged in the output (e.g. quota issues) so the owner knows exactly what went wrong.  
**Run:** `python3 run_localize_auto.py`  
**Data:** `data/lz_inputs/`, `data/lz_outputs/`, optional `data/lz_config.json`

### NotionTemplate (`notiontemplate/`) — $19/template, $49/mo (3), $149/mo unlimited
Productized Notion template generator. Owner drops `data/nt_inputs/{slug}.json` (full manifest) or `{slug}.preset` (single-line preset name). 8 bundled presets: `habit_tracker`, `crm`, `project_management`, `content_calendar`, `meeting_notes`, `reading_list`, `expense_tracker`, `ooo_documentation`. Output bundle per template: `template_spec.md` (manual build steps), `template.json` (Notion-API-shaped schema), `landing_page.md` (Gumroad-ready copy), `sample_data.csv` (importable seed data). Optional: if `NOTION_API_KEY` + `NOTION_PARENT_PAGE_ID` are set, the template is also pushed live into the connected workspace.  
**Run:** `python3 run_notiontemplate_auto.py`  
**Data:** `data/nt_inputs/`, `data/nt_outputs/`

### PodCleaner (`podcleaner/`) — $9/episode, $49/mo (10), $199 bulk 30-pack
Autonomous podcast audio cleanup. Drop raw audio into `data/pd_inputs/{slug}.{mp3,wav,m4a,flac}`; agent runs an ffmpeg chain (silenceremove + highpass 80Hz + 2:1 acompressor + EBU R128 loudnorm to -16 LUFS / -1.5 TP) and writes a cleaned 192k MP3 master to `data/pd_outputs/{slug}.mp3` + `.meta.json` with before/after durations and removed-silence percentage. Auto-detects matching `data/tr_outputs/{slug}.txt` (Transcribe) to add words-per-minute stats to the meta.  
**Run:** `python3 run_podcleaner_auto.py`  
**Data:** `data/pd_inputs/`, `data/pd_outputs/`

### ProofBot (`proofbot/`) — $15/page, $39/mo (10), $129/mo unlimited
Proofreader + copyeditor. Uses LanguageTool's public API (no key required, ~20 req/min) **plus** always-on heuristics for homophones (their/there/they're, your/you're, its/it's, should-of, alot, etc.), repeated words, double spaces, trailing whitespace, heading-style mistakes. Output report has a markdown issues table + a "cleaned text" block with the safe fixes (whitespace, repeats) already applied. Auto-sources from `sw_outputs/`, `sn_outputs/`, `pb_briefs/`.  
**Run:** `python3 run_proofbot_auto.py`  
**Data:** `data/pf_inputs/`, `data/pf_outputs/`

### ModBot (`modbot/`) — $97/mo per account, $297/mo team (5 accounts), $497 one-time audit
Comment moderation classifier for IG / TikTok / YouTube / LinkedIn / X / Reddit. Owner drops a batch in `data/cm_inputs/{slug}.json` with `{account, platform, comments: [...]}`; agent classifies each as `hide` / `reply` / `flag` / `leave` with confidence + reason + suggested reply. Heuristic rules: profanity/slurs auto-hide, scam tells (DM/crypto/whatsapp) auto-hide, multi-URL spam auto-hide, character-repetition spam auto-hide, positive-engagement markers get a reply, questions get a question-reply template. Optional Claude escalation for low-confidence "leave" cases. Output: `data/cm_outputs/{slug}.json`.  
**Run:** `python3 run_modbot_auto.py`  
**Data:** `data/cm_inputs/`, `data/cm_outputs/`

### ChatConfig (`chatconfig/`) — $99 one-time setup, $49/mo maintenance, $297 multi-bot
Builds an importable FAQ chatbot for any small business from a manifest. Owner drops `data/cc_inputs/{slug}.json` with `{business_name, hours, contact, faqs: [{q,a},...], escalation}`; agent generates a 4-file bundle: `voiceflow_flow.json` (Voiceflow import), `botpress_flow.json` (Botpress import), `simple_responses.json` (platform-agnostic intent→response map for Drift/Intercom/custom widgets), `setup_guide.md` (3-min install per platform). Greetings/hours/contact intents auto-generated from the manifest; each FAQ becomes a triggered intent with auto-expanded trigger phrases.  
**Run:** `python3 run_chatconfig_auto.py`  
**Data:** `data/cc_inputs/`, `data/cc_outputs/`

---

## Short-Form Video & Social

### ShortsForge (`shortsforge/`)
YouTube Shorts content architect (Motivational / Comedy / Men's Wellness niches). Turns owner-supplied transcripts into hook + trim plan + captioning strategy + SEO pack + storyboard. Revenue: AdSense, paid Substack tier, affiliate links.  
**Run:** `python3 run_shortsforge_auto.py`  
**Data:** `data/sf_transcripts/`, `data/sf_briefs/`, `data/sf_newsletters/`

### ViralRecycler (`viral_recycler/`)
Autonomous video repurposing pipeline: yt-dlp download → segment → hook + SEO → ffmpeg transform → YouTube + TikTok upload. Cron-driven, queue in `data/vr_sources.json`. (Resolution capped at 1080×1920 — 4K libx264 medium encodes OOM-kill on the host.)  
**Run:** `python3 run_viral_recycler_auto.py` (or `run_vr_cron.sh`)  
**Server:** `viral_recycler_server.py`

### Social Agent (`social_agent/`)
Multi-platform poster + paid-ads dispatcher. Adapters: Reddit, X, LinkedIn, Pinterest, Meta Ads, Google Ads, TikTok Ads. Filterable by audience (sellers / buyers / wholesalers).  
**Run:** `python3 run_social_auto.py [--platforms reddit,x] [--audience wholesalers]`  
**Data:** `data/social_posts.json`  
**Setup helpers:** `setup_meta.py`, `setup_pinterest.py`, `setup_reddit.py`

---

## Local & Niche Media

### TownCrier (`towncrier/`) — $50–$200/slot sponsors + $25 featured events
Hyper-local event newsletter. Parses snapshot HTML of local event sources, categorizes (Family/Music/Food/Outdoors/Civic/Free), emits digest, pitches sponsors.  
**Run:** `python3 run_towncrier_auto.py`  
**Data:** `data/tc_snapshots/`, `data/tc_digests/`

### NicheLens (`nichelens/`) — $7/mo per niche, $59/yr
Hyper-niche curation newsletters with affiliate injection. Parses snapshot HTML, emits per-niche digests.  
**Run:** `python3 run_nichelens_auto.py`  
**Data:** `data/nl_snapshots/`, `data/nl_newsletters/`

### TrendScout (`trendscout/`) — $29/mo basic, $79/mo pro, $497/yr
Paid weekly digital-product-niche newsletter. Pulls keywords from input feeds, filters blocked niches (crypto, celebs, supplements), emits ranked report.  
**Run:** `python3 run_trendscout_auto.py`  
**Data:** `data/ts_inputs/`, `data/ts_reports/`

---

## E-commerce & Paid Marketing

### DropshipScout (`dropship_scout/`) — $47/mo
Weekly digest of viral TikTok-shop products + Amazon Best Sellers (no API keys; free preview shows top 3, paid tier gets full digest + historical trends + supplier links). Publishes a public page at `website/dropship_scout_trends.html`. Hourly cron.  
**Run:** `python3 run_dropship_scout_auto.py` (or `run_dropship_scout_cron.sh`)  
**Data:** `data/ds_digests/`

### Media Buyer (`media_buyer/`)
Autonomous Meta-ads optimization agent. Two profiles off one codebase: **Lead Gen** (Instant Forms → real-estate seller pipeline, 60s lead processing via FastAPI BackgroundTasks) and **E-Com** (Shopify + CAPI funnels). FastAPI + uvicorn for webhooks; daily cron via `run_media_buyer_cron.sh`. Pure-function decision rules with dry-run by default (`MB_LIVE=1` to push live). Uses Claude `claude-sonnet-4-6` for creative generation, Twilio Lookup v2 for phone validation.  
**Run:** `python3 run_media_buyer_auto.py [--kind lead_gen|ecom]`  
**Server:** `run_media_buyer_server.py`  
**Data:** `data/media_buyer/` (incl. `insights_history.jsonl`, `controller_audit.jsonl`)  
**Token storage:** `media_buyer/token_store.py` (System User token or 60-day OAuth with auto-refresh)

### SalesPageDoctor (`salespage_doctor/`) — $77 one-time, $37/mo monitoring, $147 launch pkg
Audits Gumroad/Payhip/Sellfy/Ko-fi/Lemon Squeezy creator sales pages for conversion-killing issues. Heuristics-only (no LLM): trust signals, CTA clarity, social proof, copy length, mobile viewport, urgency cues. Free preview shows top 3 fixes.  
**Run:** `python3 run_salespage_doctor_auto.py`  
**Data:** `data/spd_reports/`, public page at `website/salespage_doctor.html`

---

## SEO & Reputation

### LinkMender (`link_mender/`) — $97 audit, $47/mo monitoring, $197 agency lead list
SEO dead-link audit reports. Discovers high-density "useful links / helpful resources" pages via dorks, scans, sends preview reports to curators.  
**Run:** `python3 run_link_mender_auto.py`  
**Data:** `data/lm_snapshots/`, `data/lm_reports/`

### ReputationGuard (`reputation_guard/`) — $79/mo per location, $497 deep audit
Review management: owner drops Google/Yelp HTML snapshots; agent identifies negative-review-heavy targets for acquisition outreach, drafts reply digests for active clients.  
**Run:** `python3 run_reputation_guard_auto.py`  
**Data:** `data/rg_snapshots/`, `data/rg_replies/`

---

## Vertical SaaS

### CareerForge (`careerforge/`) — $29/tailoring, $49/mo unlimited, $147 career pkg
Autonomous resume tailoring — extracts required/preferred keywords from a JD, rewrites profile.  
**Run:** `python3 run_careerforge_auto.py`  
**Data:** `data/cf_jobs/`, `data/cf_profiles/`, `data/cf_resumes/`

### PantryChef (`pantrychef/`) — $14/mo basic, $29/mo full+family, $79 30-day pkg
Personalized weekly meal plans from user's pantry contents.  
**Run:** `python3 run_pantrychef_auto.py`  
**Data:** `data/pc_users/`, `data/pc_plans/`

---

## Paywall System (`paywall/`)

### How It Works
1. Owner always bypasses — `AGENT_PASSWORD` env var grants free access
2. External clients hit paywall on script startup
3. They enter name + email → get PayPal payment link + access key
4. After paying, they log in with their access key

### Setup
```bash
python3 setup_paypal.py    # PayPal credentials
python3 setup_stripe.py    # Stripe (alternative)
```

### Files
- `paywall/agent_paywall.py` — per-agent subscription management (every `run_*_auto.py` calls `paywall_prompt`)
- `paywall/gate.py` — per-client SaaS/OAS payment gate
- `paywall/paypal.py` — PayPal REST API wrapper
- `data/agent_subscriptions.json` — subscriber records

### PayPal Credential Vars (.env)
```
PAYPAL_CLIENT_ID=
PAYPAL_CLIENT_SECRET=
PAYPAL_MODE=live
PAYPAL_EMAIL=WholesaleOmniverse@gmail.com
PAYPAL_ME_USERNAME=wholesaleomniverse
```

---

## Shared Infrastructure (`autonomous/`)

Most newer agents import from `autonomous/`:
- `autonomous.storage` — flat-JSON load/save helper
- `autonomous.mailer` — Gmail-SMTP send helper
- `autonomous.billing` — paywall/upgrade helpers
- `autonomous.metrics` — append-only metrics for ecosystem dashboard

Ecosystem dashboard: `python3 run_ecosystem_dashboard.py`  
Run-everything wrappers: `run_all_agents.sh`, `run_all_autonomous_agents.sh`, `run_morning_digest.sh`

---

## Data Layer

Real-estate / shared:
| File | Contents |
|------|----------|
| `data/leads.json` | 530+ motivated seller leads |
| `data/cash_buyers.json` | 98+ cash buyers |
| `data/email_log.json` | All sent emails |
| `data/outreach_clients.json` | OAS retainer clients |
| `data/outreach_campaigns.json` | Campaign history |
| `data/agent_subscriptions.json` | Agent paywall subscribers |
| `data/prospects.json` | Client prospector targets |

Per-agent: each agent uses its own 2-letter prefix subdir under `data/` (e.g. `sf_`, `vr_`, `tc_`, `nl_`, `ts_`, `ds_`, `spd_`, `lm_`, `rg_`, `cf_`, `pc_`, `pb_`, `gv_`).

---

## Lead Sources

- **Redfin** — bounding-box API, price drops, long DOM
- **Government Records** — Socrata open data (Chicago, Kansas City, Norfolk)
- **REIA Sites** — direct scraping of local real estate investor association websites
- **Hotfrog** — business directory for finding investors/clients by city
- **Amazon Best Sellers / TikTok Creative Center** — DropshipScout product trends
- **Gumroad/Payhip/Sellfy/Ko-fi/Lemon Squeezy** — SalesPageDoctor creator-page discovery
- **Google search dorks** — LinkMender resource-page discovery

---

## Email System (SMTP / Gmail)

All outbound email uses Gmail App Password (no SendGrid/Mailgun needed):
```
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=WholesaleOmniverse@gmail.com
SMTP_PASS=<gmail-app-password>   # 16-char app password from myaccount.google.com/apppasswords
```

---

## Daily Routine

```bash
# Real estate
export $(grep -v '^#' .env | xargs) && python3 run_buyer_finder_auto.py   # weekly
export $(grep -v '^#' .env | xargs) && python3 run_followup_auto.py       # daily
export $(grep -v '^#' .env | xargs) && python3 run_outreach_auto.py       # weekly
export $(grep -v '^#' .env | xargs) && python3 main.py                    # interactive deal analysis

# Everything else — run all autonomous agents
./run_all_autonomous_agents.sh
```

Cron wrappers already wired:
- `run_dropship_scout_cron.sh` — hourly
- `run_media_buyer_cron.sh` — daily
- `run_vr_cron.sh` — ViralRecycler

---

## Planned / Upcoming

### Lead Sieve
Automated lead scoring/filtering — rank leads by motivation (DOM, price drops, equity, distress signals). Deliver ranked hot list daily.

### pSEO Factory
Programmatic SEO content generator — city-by-city "We Buy Houses" landing pages.

### Chrome Extension Forge
Browser extension overlaying deal analysis (ARV, max offer, profit margin) on Zillow/Redfin/MLS — $47/mo SaaS for other wholesalers.

---

## Key Business Metrics

| Metric | Target |
|--------|--------|
| Cash buyers | 50+ to close deals consistently |
| Follow-up response rate | 5–15% |
| OAS clients | 5+ at $500/mo = $2,500 MRR |
| Wholesale deals | 1–2/month at $5,000–$15,000/deal |

---

## Important Notes for Claude

- Never add API keys or credentials inline in code — always load from `.env`
- All agents must run with `python3 run_*.py` — no interactive steps should be required for autonomous runs
- The owner bypasses paywall via `AGENT_PASSWORD` env var; never prompt owner for payment
- Free scraping sources only — no paid data APIs unless user explicitly adds a key
- Keep all data in `data/` directory as JSON files (use the per-agent prefix subdir for new agent data)
- Follow-up emails use the `TEMPLATES` dict in `followup_agent/tools.py` — keep them conversational, not salesy
- Media Buyer defaults to dry-run; set `MB_LIVE=1` only when the user explicitly asks to push actions to Meta
- When adding a new agent, follow the existing shape: `<agent>/tools.py` exposing `run_full_cycle()`, plus a `run_<agent>_auto.py` entry point that calls `paywall.agent_paywall.paywall_prompt` first
