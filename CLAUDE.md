# Wholesale Omniverse — Master Project Guide

**Company:** Wholesale Omniverse LLC  
**Owner:** tylumiere25@gmail.com  
**Business Email:** WholesaleOmniverse@gmail.com  
**Phone:** 207-385-4041  
**PayPal.me:** paypal.me/wholesaleomniverse

---

## Project Architecture

This is a multi-agent autonomous real estate wholesale business system. Every agent runs without requiring manual API calls — they operate on schedules or via direct script execution.

### Core Principle
- No extra API keys required for any agent to run (uses free web scraping, open data, and Gmail SMTP)
- All agents are paywall-gated for external clients; owner bypasses paywall with `AGENT_PASSWORD`
- PayPal integration handles all billing (Invoice → Checkout → PayPal.me fallback)
- All data stored as flat JSON files in `/data/`

---

## Active Revenue Engines

### Engine 1 — Cash Buyer Finder (`buyer_finder/`)
**Price:** $97/month  
**What it does:** Scrapes REIA association websites + Hotfrog business directory to find real estate investors. Emails intro messages to build a buyers list.  
**Run:** `python3 run_buyer_finder_auto.py`  
**Data:** `data/cash_buyers.json`

### Engine 2 — Seller Follow-Up Sequence (`followup_agent/`)
**Price:** $147/month  
**What it does:** 6-touch automated email follow-up sequence for motivated seller leads. Day 3, 7, 14, 21, 30, 60. Tracks hot leads that respond.  
**Run:** `python3 run_followup_auto.py`  
**Data:** `data/leads.json`, `data/email_log.json`  
**Schedule:** `FOLLOWUP_SCHEDULE = {0: 3, 1: 4, 2: 7, 3: 7, 4: 9, 5: 30}`

### Engine 3 — Outreach-as-a-Service (`outreach_service/`)
**Price:** $300–$800/month (tiered)  
**What it does:** Sell prospecting campaigns to other wholesalers or investors. Runs gov-record prospecting for paying retainer clients and emails them weekly reports.  
**Tiers:** basic $300 (1 market), standard $500 (2 markets), premium $800 (4 markets)  
**Run:** `python3 run_outreach_auto.py`  
**Data:** `data/outreach_clients.json`, `data/outreach_campaigns.json`

### Engine 4 — Wholesale Deal Analyzer (`agent.py`, `tools.py`)
**Price:** $197/month  
**What it does:** AI-powered deal analysis using Claude claude-sonnet-4-6. Pulls comps, estimates ARV, calculates max offer, generates LOIs, and emails sellers.  
**Run:** `python3 main.py` (chat mode) or `python3 main.py --auto` (autonomous)  
**Data:** `data/leads.json`, `data/contracts.json`

---

## Paywall System (`paywall/`)

### How It Works
1. Owner (you) always bypasses — `AGENT_PASSWORD` env var grants free access
2. External clients hit paywall on script startup
3. They enter name + email → get PayPal payment link + access key
4. After paying, they use their access key to log in

### Setup
```bash
python3 setup_paypal.py   # Interactive wizard — enter your PayPal credentials
```

### Files
- `paywall/agent_paywall.py` — per-agent subscription management
- `paywall/gate.py` — per-client SaaS/OAS payment gate
- `paywall/paypal.py` — PayPal REST API wrapper
- `data/agent_subscriptions.json` — subscriber records

### PayPal Credential Vars (in .env)
```
PAYPAL_CLIENT_ID=        # From developer.paypal.com
PAYPAL_CLIENT_SECRET=    # From developer.paypal.com
PAYPAL_MODE=live         # live or sandbox
PAYPAL_EMAIL=WholesaleOmniverse@gmail.com
PAYPAL_ME_USERNAME=wholesaleomniverse
```

---

## Data Layer

| File | Contents |
|------|----------|
| `data/leads.json` | 530+ motivated seller leads |
| `data/cash_buyers.json` | 98+ cash buyers |
| `data/email_log.json` | All sent emails |
| `data/outreach_clients.json` | OAS retainer clients |
| `data/outreach_campaigns.json` | Campaign history |
| `data/agent_subscriptions.json` | Agent paywall subscribers |

---

## Lead Sources

- **Redfin** — bounding-box API, price drops, long DOM
- **Government Records** — Socrata open data (Chicago, Kansas City, Norfolk)
- **REIA Sites** — direct scraping of local real estate investor association websites
- **Hotfrog** — business directory for finding investors by city

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

## Running the Full System (Daily Routine)

```bash
# 1. Find new cash buyers (run weekly)
export $(grep -v '^#' .env | xargs) && python3 run_buyer_finder_auto.py

# 2. Send daily follow-ups to sellers
export $(grep -v '^#' .env | xargs) && python3 run_followup_auto.py

# 3. Run client outreach campaigns (run weekly)
export $(grep -v '^#' .env | xargs) && python3 run_outreach_auto.py

# 4. Analyze a deal interactively
export $(grep -v '^#' .env | xargs) && python3 main.py
```

---

## Upcoming Engines (Planned)

### Engine 5 — Lead Sieve
Automated lead scoring and filtering. Takes raw leads from any source and scores them by motivation level (days on market, price drops, equity, distress signals). Delivers ranked hot list daily.

### Engine 6 — pSEO Factory
Programmatic SEO content generator. Produces city-by-city "We Buy Houses" landing pages automatically. Drives organic seller leads without paid ads.

### Engine 7 — Chrome Extension Forge
Browser extension that overlays deal analysis on Zillow/Redfin/MLS listings. Shows ARV, max offer, and profit margin in real time. Sell as a $47/month SaaS to other wholesalers.

### Engine 8 — Faceless Video Pipeline
Automated short-form video creation (TikTok/Reels/Shorts) for real estate content marketing. Pulls market data, generates scripts, creates videos with AI voiceover, posts on schedule.

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
- Keep all data in `data/` directory as JSON files
- Follow-up emails use the `TEMPLATES` dict in `followup_agent/tools.py` — keep them conversational, not salesy
