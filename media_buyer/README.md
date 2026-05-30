# Media Buyer — Autonomous Campaign Optimization Agent

Modular Meta-ads agent that runs two business models off the same codebase:
**Lead Gen** (real-estate motivated-seller acquisition via Instant Forms) and
**E-Com** (high-velocity product funnels with Shopify + CAPI). The profile
chosen at call time decides every threshold, rule, and prompt.

## 1. System Architecture & Tech Stack

| Layer | Choice | Why |
|---|---|---|
| Web/webhook | FastAPI + uvicorn | Native async, sub-second background tasks, BackgroundTasks for the 60s lead pipeline |
| Cron driver | plain `python3 run_media_buyer_auto.py` via system cron | Same pattern as every other autonomous agent in this repo (`run_*_auto.py`) |
| Storage | flat JSON in `data/media_buyer/` + the repo-wide `data/leads.json` | Matches the existing `autonomous.storage` helper. Swap to Postgres at any time — only `storage.load/save` calls would change |
| LLM | Anthropic Claude (`claude-sonnet-4-6`) | Already in `requirements.txt`; matches `agent.py` deal analysis |
| Phone validation | Twilio Lookup v2 (REST) | No SDK pull; ~$0.005/lookup |
| Slack / CRM | plain Incoming Webhooks (`requests.post`) | Zero dependencies; trivial to swap for Make/Zapier/n8n |
| Insights retention | append-only JSONL (`insights_history.jsonl`) | Cheap, grep-able, gives the monitor moving-average inputs without a DB |
| Decision audit | append-only JSONL (`controller_audit.jsonl`) | Every proposed action — applied or dry-run — is recorded; investigatable after the fact |

### Token storage & refresh

Two token classes — handled in `token_store.py`:

1. **System User access token** (preferred for prod). Long-lived, non-expiring,
   scoped to a Business Manager + ad account. Created once in BM, stored as
   `META_ACCESS_TOKEN` in `.env`. No refresh path needed; the token outlives
   the deployment.
2. **User OAuth long-lived token** (dev fallback). 60-day expiry. Persisted
   encrypted-at-rest under `data/media_buyer/tokens.json`. The store
   proactively re-exchanges via `/oauth/access_token?grant_type=fb_exchange_token`
   when <7 days remain so the cron never hits an expired token mid-run.

Pixel ID + CAPI access token are sourced from the same access token —
`/{pixel_id}/events` accepts the System User token directly when the Pixel
is owned by the same Business.

Secrets layout (all in `.env`, never committed):
```
META_APP_ID, META_APP_SECRET, META_ACCESS_TOKEN, META_AD_ACCOUNT_ID, META_PAGE_ID
META_WEBHOOK_VERIFY_TOKEN                  # echoed back during subscription
MB_LEADGEN_PIXEL_ID, MB_ECOM_PIXEL_ID
MB_LEADGEN_TARGET_CPL, MB_ECOM_BREAKEVEN_ROAS, MB_ECOM_TARGET_AOV
MB_LEADGEN_SLACK_WEBHOOK, MB_ECOM_SLACK_WEBHOOK
MB_LEADGEN_ALERT_EMAIL, MB_ECOM_ALERT_EMAIL
TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN
SHOPIFY_WEBHOOK_SECRET
MB_CRM_WEBHOOK_URL                          # Zapier/Make/n8n inbound
MB_LIVE=1                                   # opt OUT of dry-run
MB_MAX_DAILY_INCREASE_PCT=30                # safety cap, default 30
MB_MAX_DAILY_BUDGET_USD=500                 # safety cap on absolute daily budget
```

## 2. Dual-Model Ingestion (`ingestion.py`)

Single FastAPI app routes on path:

| Route | Verb | Purpose |
|---|---|---|
| `/webhooks/meta/leadgen` | GET | Subscription challenge (`hub.challenge` echo) |
| `/webhooks/meta/leadgen` | POST | Lead arrived → ACK in <2s → background pipeline |
| `/webhooks/shopify/orders` | POST | Order placed → record revenue + CAPI Purchase |
| `/webhooks/meta/capi` | POST | First-party CAPI passthrough (custom checkouts) |
| `/healthz` | GET | Liveness probe |

**Lead-gen pipeline** (target: clean → CRM in <60s):
1. Verify `X-Hub-Signature-256` against `META_APP_SECRET` → 403 if invalid.
2. ACK Meta immediately. Background:
3. `GET /{leadgen_id}` for the field_data answers.
4. Twilio Lookup v2 → drop invalid + nonFixedVoip burners early.
5. Claude scoring (`scoring.py`) → `{tier, urgency_signals, objection_signals}`.
6. `integrations.crm_push()` — appends to local `data/leads.json` AND posts to
   `MB_CRM_WEBHOOK_URL` if configured.
7. Slack ping for Hot/Warm only.

**E-com pipeline**:
1. Verify Shopify `X-Shopify-Hmac-Sha256` HMAC.
2. ACK immediately. Background:
3. `meta_api.send_capi_event(event_id=shopify_order_<id>)` — `event_id`
   deduplicates against the browser Pixel that already fired.
4. Persist to `data/media_buyer/orders.json` keyed by order id (idempotent).
5. Increment per-product day count; Slack-ping ops when a SKU crosses
   `MB_WINNING_DAILY_THRESHOLD` (default 25 orders).

## 3. Performance Analytics Engine (`monitor.py`)

`daily_sweep(kind)` pulls Insights at three levels — **campaign / adset / ad** —
for the profile's ad account and computes a `Metrics` dataclass per row.

Per-row derivations:

| Metric | Formula | Used by |
|---|---|---|
| `hook_rate` | `video_3_sec_watched_actions / impressions` | top-funnel diagnosis (both profiles) |
| `hold_rate` | `video_thruplay_watched_actions / impressions` | top-funnel diagnosis (both profiles) |
| `cpl` | `spend / count(actions.lead)` | lead-gen scale/kill rules |
| `form_completion_rate` | `leads / link_clicks` | lead-gen creative diagnostics |
| `roas` | `sum(action_values.purchase) / spend` | ecom scale rule |
| `cpp` | `spend / count(actions.purchase)` | ecom benchmarking |
| `aov` | `revenue / count(actions.purchase)` | ecom kill rule threshold |
| `frequency` | direct from Insights | refresh trigger |

Every sweep is appended to `data/media_buyer/insights_history.jsonl` so
`history_for(object_id)` + `moving_avg()` can produce the 3-day MA ROAS the
controller's scale rule needs (no second API call needed for the lookback).

## 4. Decision & Optimization Engine (`controller.py`)

All three rules are **pure functions** (`Metrics → Action | None`) so they're
unit-testable without an HTTP layer. The controller orchestrates and `execute()`
applies — pause/budget calls flow through `meta_api` which short-circuits to
dry-run when `MB_LIVE` is unset.

| Rule | Lead Gen | E-Com |
|---|---|---|
| **SCALE** | `+15%` daily budget when `cpl < target_cpl_usd` (and spend ≥ 1× target_cpl) | `+20%` daily budget when 3-day MA ROAS > `break_even_roas` (and spend ≥ 1× AOV) |
| **KILL** | pause when `spend ≥ 2× target_cpl` AND `leads == 0` | pause when `spend ≥ 1× AOV` AND `purchases == 0` |
| **REFRESH** | alert when `frequency ≥ 3.5` AND under-performing ≥3 days | same threshold; under-perf check uses ROAS instead of CPL |

Safety:
- `MAX_DAILY_BUDGET_INCREASE_PCT` (default 30) caps any single scale.
- `MAX_ABSOLUTE_DAILY_BUDGET_USD` (default $500) hard caps any post-mutation budget.
- `controller_audit.jsonl` records every proposed action with the dry-run flag —
  flip `MB_LIVE=1` and the same logs will show live results, with no rule changes.

## 5. Creative Iteration Agent (`generator.py`)

Two-stage prompt:

1. **Analyst** ingests top-K winners + bottom-K losers + their current copy and
   extracts the **transferable pattern** (winning angle, structural elements,
   voice, things to avoid). Output is strict JSON.
2. **Writer** (prompt-within-a-prompt) takes the analyst report and drafts **3**
   variations of `{hook, primary_text, headline, cta}`. The writer's system
   prompt branches on `kind`:
   - Lead-gen voice = direct response to motivated sellers, concrete numbers
     (days/dollars), no emojis, ≤1 exclamation per variation.
   - E-com voice = benefit-first hook variations (before-after, demo moment,
     unboxing surprise), defensible claims.

`refresh_batch_for(ads, copies, kind=...)` runs the whole loop end-to-end and
returns `{"pattern": ..., "variations": [...]}`. Currently the daily cron emails
the output for human review; flipping to `meta_api.create_ad_creative()` for
auto-upload is a one-line change once you trust the output.

## Running

```bash
# install (one-time)
pip install fastapi uvicorn[standard] anthropic requests rich python-dotenv

# webhook server (long-running)
export $(grep -v '^#' .env | xargs) && python3 run_media_buyer_server.py
# default port 8087; put it behind nginx/Cloudflare with TLS

# daily cron (start in dry-run!)
export $(grep -v '^#' .env | xargs) && python3 run_media_buyer_auto.py --kind lead_gen
export $(grep -v '^#' .env | xargs) && python3 run_media_buyer_auto.py --kind ecom

# when you trust it
export MB_LIVE=1
```

Suggested crontab:
```
# Run every 24h at 4am — the controller respects MB_LIVE for actual mutations
0 4 * * * /home/tylumiere25/wholesale_agent/run_media_buyer_cron.sh
```
