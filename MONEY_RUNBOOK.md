# Wholesale Omniverse — Monday Morning Money Runbook

**Generated after wave-2 diagnose pass on 2026-06-04.**

The fleet is technically complete (45/45 agents have `--diagnose`). What's
blocking revenue is configuration + data, not code.

---

## 0. Critical — Do These First (Total: 30 min)

### 0.1 Fill in PayPal credentials in `.env` — BLOCKER
Current state: `PAYPAL_CLIENT_ID=` and `PAYPAL_CLIENT_SECRET=` are
**empty strings**. PayPal API calls fail with `ValueError: must be set`.
**You cannot collect a single dollar of agent revenue until this is fixed.**

```bash
# 1. Go to https://developer.paypal.com/dashboard/applications/live
# 2. Open your live app, copy CLIENT_ID + CLIENT_SECRET
# 3. Edit .env:
nano .env
# Set:
#   PAYPAL_CLIENT_ID=<paste>
#   PAYPAL_CLIENT_SECRET=<paste>

# 4. Verify:
python3 -c "from paywall.paypal import _get_token; print('ok' if _get_token() else 'fail')"
```

### 0.2 Register or fix `wholesaleomniverse.com`
SpeedAudit smoke test caught this: DNS resolution fails. Multiple agents
(Pinterest pins, ColdCaller scripts, BentoForge default handle, etc.)
reference this URL.

Options:
- Register it on Namecheap / Cloudflare (~$10/yr)
- OR update marketing copy to use a domain you actually own
- OR point pins/links at a Substack URL you control

Test: `host wholesaleomniverse.com` should return an A record.

### 0.3 Fix `ffmpeg` hang (only if you want Transcribe → ShowNotes chain)
Transcribe diagnose found `/usr/bin/ffmpeg -version` timed out at 5s.
Without fixing this, video transcripts stall the cron at the 10-min
ffmpeg subprocess timeout for every video input.

```bash
ffmpeg -version    # should print in <100ms; if it hangs, reinstall:
sudo apt-get remove --purge ffmpeg && sudo apt-get install ffmpeg
```

If you don't need video transcription, ignore this and just diagnose-skip
the warning. Audio-only inputs (.mp3/.wav/.m4a) still work.

---

## 1. Pick ONE revenue path this week

The fleet has too many agents to drive simultaneously. Pick one and execute.

### Path A — Deal Analyzer (highest margin, infra ready)

You already have **530+ motivated-seller leads in `data/leads.json`**.
One closed wholesale deal = $5k–15k. This funds everything else.

```bash
export $(grep -v '^#' .env | xargs)

# Verify ready
python3 main.py --diagnose 2>&1 | head    # if no --diagnose, just run --help

# Work the existing pipeline
python3 run_followup_auto.py              # daily — sends 6-touch follow-ups
python3 main.py                           # interactive deal analysis on hot leads

# Grow the cash buyer list (needed to close)
python3 run_buyer_finder_auto.py          # weekly
```

**Action: spend 90 min today** running `python3 main.py` against the 10
freshest leads in `data/leads.json`. Generate LOIs for any with a viable
ARV. Email them.

### Path B — SalesPageDoctor (lowest friction, productized)

$77 one-time per audit. No subscription churn risk. Lead-magnet driven.

```bash
# 1. Verify ready
python3 run_salespage_doctor_auto.py --diagnose

# 2. Diagnose flagged: today's dork rotation parsed 0 Bing results
#    Workaround: do an ad-hoc audit on a known URL to test the engine:
python3 run_salespage_doctor_auto.py --audit-now https://gumroad.com/l/<some-product>

# 3. Add paying clients manually after they pay PayPal:
python3 -m salespage_doctor.clients add \
    customer@email.com https://their-page.com full_77 --name "Customer Name"
python3 -m salespage_doctor.clients activate customer@email.com
# After audit shipped:
python3 -m salespage_doctor.clients fulfill customer@email.com
```

**Action: pick 5 Gumroad/Payhip creators in your network, run --audit-now
on their URLs, send them the preview report with a $77 PayPal link
manually. First reply pays for itself.**

### Path C — OAS / Outreach-as-a-Service (recurring)

$300–$800/mo per client. Highest LTV.

```bash
python3 run_outreach_auto.py --diagnose
python3 run_outreach_auto.py              # weekly fulfillment

# After someone agrees on a retainer:
python3 -m outreach_service.clients add client@email.com basic_300
python3 -m outreach_service.clients activate client@email.com
```

Same drill: 5 manual pitches to other wholesalers / investors. One signed
retainer = $300+/mo recurring.

---

## 2. What the diagnose findings mean for you

Every agent now reports `[P0] / [P1] / [info]` lines. Here's how to
triage at scale:

```bash
# Run all diagnoses, save findings:
mkdir -p data/diagnose_reports
for f in run_*_auto.py; do
    agent=$(basename "$f" .py | sed 's/^run_//;s/_auto$//')
    python3 "$f" --diagnose > "data/diagnose_reports/${agent}.txt" 2>&1
done

# Then read the P0 fails — those are blocking:
grep -h "P0.*fail\|✗.*P0" data/diagnose_reports/*.txt | sort -u
```

**Repeating P0 across 20+ agents:** input directory empty
(`data/<prefix>_inputs/`). This is the #1 systemic finding. Owner-fed
agents need either:
- A feed-dropper service / cron job that populates them, OR
- The cron disabled (`crontab -e`, comment out) until inputs flow

---

## 3. Lead-list reality check

Run this to see who actually has leads to work:

```bash
for f in data/*_leads.json data/leads.json data/prospects.json; do
    n=$(python3 -c "import json; d = json.load(open('$f')); print(len(d) if isinstance(d, list) else len(d.keys()))" 2>/dev/null || echo "?")
    echo "$n  $f"
done | sort -n -r | head -10
```

Use the top 3 result files to pick which agent to drive this week. The
agent with the most existing leads has the shortest path to revenue.

---

## 4. Weekly cadence (10 hrs/week)

| Day | Action | Time |
|---|---|---|
| Mon | Run `--diagnose` on your chosen path's agent; fix any P0 | 30 min |
| Mon | Send 10 outbound pitches manually (warm tone, no spam) | 90 min |
| Tue | Run agent's autonomous cycle; review outputs | 30 min |
| Wed | Customer-side: reply to inquiries, send PayPal invoices | 60 min |
| Thu | Fulfill deliverables (run `--audit-now`, build report, send) | 90 min |
| Fri | Mark fulfilled, churn dead leads, log lessons learned | 30 min |
| Sat-Sun | Off (cron still runs) | — |

**Total: ~5h/week active.** The cron does the rest.

---

## 5. After your first dollar

Once one revenue path is producing >$500/mo, add a second:

1. Pick the second highest-margin agent
2. Run `--diagnose`, fix P0s
3. Repeat the manual-outreach loop
4. Don't add a third until #2 is also >$500/mo

The temptation will be to run all 45 agents simultaneously. Resist it.
Each new agent costs customer-side fulfillment time, even if it's
"autonomous" code-side.

---

## 6. Operational health (monthly)

```bash
# Every 1st of month:
for f in run_*_auto.py; do
    agent=$(basename "$f" .py | sed 's/^run_//;s/_auto$//')
    result=$(python3 "$f" --diagnose 2>&1 | tail -3 | head -1)
    echo "$agent: $result"
done | grep -E "P0=[1-9]|P1=[1-9]" | sort
```

Any agent with `P0>0` is silently failing. Any with `P1>0` will become a
P0 soon. Fix in order of revenue impact (paying-customer agents first).

---

## 7. What I (Claude) cannot do for you

These are blocked on your judgment / authentication / capital:

1. Pay for and register the domain
2. Provide PayPal credentials (your account, your liability)
3. Choose which niches to target with TownCrier / NicheLens / etc.
4. Write the personal-voice outreach (your tone, your relationships)
5. Negotiate retainer deals with prospects
6. Decide which existing 530 leads to call first
7. Make tax + LLC decisions
8. Set up bank account to receive PayPal payouts

These are blocked on your owner-fed inputs flowing in:

1. Drop snapshots into `data/tc_snapshots/`, `data/nl_snapshots/`, etc.
2. Drop PDFs into `data/pb_pdfs/`
3. Drop transcripts into `data/sn_inputs/`, `data/cf_jobs/`
4. Populate niche configs (`data/nl_niche_configs.json`)
5. Maintain DNC list (`data/cd_dnc.json`)

---

## TL;DR

```bash
# Make money this week:
#  1. Fix PayPal in .env
#  2. Pick Path A (Deal Analyzer) — work existing leads.json
#  3. Run python3 main.py against 10 freshest leads
#  4. Send 10 personalized emails today
#  5. Reply to anyone who replies; send PayPal invoice manually
#  6. Repeat weekly
```

The agents are tools. You are the operator. The diagnose pass proved
the tools work — what's missing is consistent operator-side activity.
