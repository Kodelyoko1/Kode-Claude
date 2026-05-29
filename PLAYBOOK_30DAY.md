# 30-Day Revenue Playbook — First $3k MRR

Three agents. Three motions. One month.

| Agent | Target | Daily commitment | Expected MRR end-of-day-30 |
|---|---|---|---|
| 🛡 ReputationGuard | local businesses w/ bad reviews | 30 min outreach | $400–800 |
| 📄 CareerForge | laid-off tech workers | 45 min posting + DM | $600–1,200 |
| ♻ ViralRecycler | aspiring creators | 30 min content + signup | $300–900 |
| **TOTAL** | | **~1.75 hr/day** | **$1,300–2,900 MRR** |

---

## 🛡 ReputationGuard — Local Business Acquisition

### Targeting
Find businesses with **≥ 5 reviews, ≥ 1 in last 30 days, rating ≤ 4.0** in **any of these high-ROI verticals**:
- Dentists, chiropractors, med spas (high CLV per client)
- Restaurants, coffee shops (review-sensitive)
- HVAC, plumbing, roofing (emergency-purchase, reviews drive choice)
- Hair salons, nail salons (reputation = livelihood)

### Daily flow (30 min)
- **Mon/Wed/Fri** — open Google Maps in your city → search "[vertical] near me" → for any business with rating < 4.0, screenshot reviews, save HTML snapshot to `data/rg_snapshots/{business-slug}.html`
- **Tue/Thu** — for each new snapshot, run `python3 run_reputation_guard_auto.py` → it generates 3 sample reply drafts → email the owner the free preview

### Email template (already drafted in agent)
The agent auto-generates the email. You manually find the owner email from their website's contact page. Average lookup: 90 seconds per business.

### Conversion math
- 10 outreach/day × 5 days = 50/week
- 8% reply rate = 4 conversations
- 35% close = 1.4 new clients/week × $79/mo
- Week 4 expected: ~5 clients = **$395 MRR**

### Pricing scripts (when they reply)
- **They ask "what does it cost"** → "$79/month, first 14 days free, cancel any time. I draft replies weekly, you approve or edit, I send."
- **They ask "is this AI"** → "I use a tool I built to draft them — you always approve every reply before it goes live."
- **They ghost after preview** → wait 7 days, send: "Just checking — did the 3 sample replies work for you? Want me to send the rest?"

---

## 📄 CareerForge — Laid-Off Tech Worker Acquisition

### Targeting
- LinkedIn hashtags: `#opentowork`, `#layoffs`, `#techlayoff`
- Reddit: r/cscareerquestions, r/jobsearchhacks, r/recruitinghell
- Facebook groups: "Laid Off Tech Workers", "[City] Tech Network"

### Daily flow (45 min)
- **Every morning** — search LinkedIn #opentowork in tech, find 10 people who posted in last 24h
- Comment something genuinely useful on their post (not a pitch) — establish presence
- DM 5 of them: "Just saw your post. I built a free ATS-match-score tool — paste a JD + your résumé, get a 0–100 score in 60 seconds. Want the link?"
- When they say yes → send your dashboard link → they get a free score → upsell to $29 tailored package

### Content motion (compounding)
- 1 LinkedIn post per day on resume tips (use ChatGPT/Claude to draft, takes 5 min)
- 1 Reddit comment per day with genuine help in r/cscareerquestions
- After 14 days, you'll have inbound asking about the tool

### Conversion math
- 5 DMs/day × 30 days = 150 outreach
- 25% reply ("yes send the link") = 38 free scores delivered
- 40% buy paid resume = 15 sales × $29 = $435 in one-time
- 10% upgrade to monthly = 1.5 monthly × $49 = $73 MRR
- Plus inbound from content = **2x by month 2**

### Pricing scripts
- **They ask "why pay when ChatGPT is free"** → "ChatGPT writes generic résumés. I tune yours to mirror the JD's exact vocabulary so ATS bots match you higher — no fabrication. $29 once, send me the JD, I send the rewritten résumé in 60 minutes."
- **They want a freebie** → free ATS-match-score is the freebie. Hard line on $29 for tailored output.

---

## ♻ ViralRecycler — Creator Acquisition

### Targeting
Other aspiring YouTubers/TikTokers in motivational, comedy, wellness niches who post inconsistently because editing is hard.

### Daily flow (30 min)
- **Mon** — post your own Short (made by your own ViralRecycler) on YouTube. Tag #shorts + your niche.
- **Tue–Thu** — DM 5 creators who posted in the last 7 days but haven't posted today. Sample DM in template below.
- **Fri** — 1 Reddit post in r/NewTubers, r/PartneredYoutube, r/Tiktokhelp showing your latest Short with the line "made it in 4 minutes using my own automation"
- **Sat/Sun** — reply to every comment + DM that came in

### Cold DM template
```
Hey [Name] — your last Short on [topic] hit different.

Quick Q: how long does it take you to make one of those? I'm building a tool that does the whole pipeline (download trending source → recut → caption → post to YT+TT) in ~5 min. 30-day free trial if you want to try it.

[your trial signup link]

No pitch beyond this — just curious if it'd save you time.
```

### Conversion math
- 5 DMs/day × 30 days = 150
- 30% try the free trial = 45 trial signups
- 25% convert to paid = 11 paid × ~$50 blended = **$550 MRR**
- Plus your own content compounds: by week 4 you have 20+ Shorts posted, growing organic traffic

### Pricing scripts
- **They ask "is this against YouTube ToS"** → "We only process Creative Commons sources by default; for others, we apply heavy transformation (recut, recolor, mirror, recaption) + auto-credit the creator. Same fair-use standard as any commentary channel."
- **They want lifetime price** → No. $79/mo Pro, no lifetime deals.

---

## Week-by-week revenue ramp

| Week | Action focus | Expected MRR end-of-week |
|---|---|---|
| **Week 1** | Build target lists, send 100 outreach, post 7 pieces of content | $158 (2 clients/3 sales) |
| **Week 2** | Follow up week-1 conversations, second batch of outreach | $475 (compounding closes) |
| **Week 3** | First inbound starts showing up; refine pitches | $1,200 |
| **Week 4** | Replies stack up, conversions accelerate | $2,400 |
| **Day 30** | — | **$1,300–2,900 MRR** |

## Daily checklist (print this)

- [ ] **8:00 AM** — Open dashboard, check overnight signups (`http://localhost:8080/`)
- [ ] **8:15 AM** — Post 1 LinkedIn + 1 TikTok of your own Short
- [ ] **9:00 AM** — 30 min ReputationGuard outreach (snapshot + email)
- [ ] **12:00 PM** — 45 min CareerForge DMs + 1 Reddit comment
- [ ] **3:00 PM** — 30 min ViralRecycler DMs + check trial dashboard
- [ ] **5:00 PM** — Reply to every inbound from today
- [ ] **9:00 PM** — Look at the dashboard. Smile or course-correct.

## Stop conditions

- If you don't have 3 paying clients across all agents by Day 21 → pivot: focus 100% on one agent and ignore the others until that one hits $1k MRR.
- If you hit $3k MRR before Day 30 → start hiring a VA to do the outreach loops on the 2 highest-converting agents.
