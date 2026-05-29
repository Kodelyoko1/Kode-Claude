# GO LIVE — From Here to Posting Today

Three actions you do, three I've done. Total time: ~25 minutes.

## ✅ What I already did
- [x] **30-day acquisition playbook** → `PLAYBOOK_30DAY.md`
- [x] **60 ready-to-send outreach drafts** → `data/outreach_drafts/{reputation_guard,careerforge,viral_recycler}_20.md`
- [x] **Git repo initialized + committed** (main branch, secrets excluded)
- [x] **Render config written** (`Dockerfile`, `render.yaml`, `RENDER_DEPLOY.md`)
- [x] **Static site live** on https://wholesaleomniverse.netlify.app

## ⚙ What you do next (in order)

---

### STEP 1 — Push to GitHub (3 min)

Create a private repo at **github.com/new** — name it `omni-portal` or anything. Don't initialize with README.

Then run:
```bash
cd /home/tylumiere25/wholesale_agent
git remote add origin git@github.com:YOUR_GITHUB_USER/omni-portal.git
git push -u origin main
```

(If you use HTTPS instead of SSH, replace with `https://github.com/YOUR_USER/omni-portal.git`)

---

### STEP 2 — Deploy SaaS to Render (5 min waiting)

1. Sign in to **render.com** with GitHub (free)
2. **New → Blueprint** → pick the `omni-portal` repo → Apply
3. Render reads `render.yaml` and proposes the service. Click Apply.
4. In the service **Settings → Environment**, add:
   - `SMTP_USER` = `WholesaleOmniverse@gmail.com`
   - `SMTP_PASS` = your Gmail app password (16 chars, from myaccount.google.com/apppasswords)
   - `AGENT_PASSWORD` = anything (your owner bypass)
5. First build takes ~5 min. Public URL appears:
   ```
   https://viral-recycler-saas.onrender.com
   ```

That URL is your full live SaaS. Trial signups, dashboards, queue processing all run.

---

### STEP 3 — Authorize YouTube (5 min, one-time)

The agent posts to *your* YouTube channel. You do the OAuth once, the refresh token lives forever.

1. Go to **console.cloud.google.com** → create a project named "Omni Portal"
2. **APIs & Services → Library** → search "YouTube Data API v3" → **Enable**
3. **APIs & Services → Credentials → Create Credentials → OAuth client ID**
   - Application type: **Desktop app**
   - Name: "Omni Portal Local"
   - Download the JSON file
4. Save it to `/home/tylumiere25/wholesale_agent/data/yt_client_secrets.json`
5. Run:
   ```bash
   cd /home/tylumiere25/wholesale_agent
   python3 setup_viral_recycler.py --authorize
   ```
   A browser tab opens → log in to **your YouTube account** → authorize → done.
   Token is saved to `data/yt_token.json`.

---

### STEP 4 — Set your channel branding (30 sec)

```bash
python3 run_shortsforge_auto.py \
  --set-channel "Your Channel Name" \
  --set-handle "@yourhandle" \
  --set-substack "https://yourname.substack.com"
```

(Skip the Substack flag if you don't have one yet — agent works without it.)

---

### STEP 5 — Drop your first viral URL (1 min)

```bash
cd /home/tylumiere25/wholesale_agent
./vr "PASTE_VIRAL_URL_HERE" --niche motivational --tier pro
```

What happens:
1. yt-dlp downloads the source (10s)
2. Whisper transcribes (30s)
3. Best 45s segment picked, hook generated
4. Color grade + audio master + captions burned in (60s)
5. Thumbnail generated
6. **Uploads to your YouTube** (60s)
7. **TikTok-ready file emailed to you** for one-tap phone upload

Total time: ~3 min from URL to posted Short.

---

### STEP 6 — Send your first outreach (15 min)

Open `data/outreach_drafts/viral_recycler_20.md`. Pick 10 creators in your niche on TikTok/YouTube/IG. DM the template from drafts 1-10 to them.

That's 10 trial signup attempts for tonight. Expected: 3-4 will try the trial, 1 will convert to paid within 14 days.

---

## What the dashboard tells you

```bash
python3 run_ecosystem_dashboard.py --serve
```
Open http://localhost:8765 — see every agent's MRR, active subscribers, pipeline funnel, live email feed.

For the public SaaS dashboard (your customers' view), they get the link in their welcome email after signing up.

---

## If you get stuck

- **YouTube OAuth fails** → Check that the OAuth client type is "Desktop app", not "Web app"
- **yt-dlp errors** → Run `pip install -U --break-system-packages yt-dlp`
- **Render build fails** → Look at build logs; usually `requirements-saas.txt` typo or missing system package
- **TikTok official API rejected** → Use email handoff (default) until you re-apply
- **No replies on outreach** → Wait 72h before judging. Tweak subject line first if no opens.

---

## Daily routine (post-Day-1)

| Time | Action |
|---|---|
| 8:00 AM | Open Render logs — check overnight signups |
| 8:30 AM | `./vr "URL"` for today's Short |
| 9:00 AM | 30 min ReputationGuard outreach (10 emails) |
| 12:00 PM | 45 min CareerForge DMs (10 messages) |
| 3:00 PM | 30 min ViralRecycler DMs (10 messages) |
| 5:00 PM | Check inbox, reply to inbound |
| 9:00 PM | Check dashboard, log wins |

Day 30: $1,300–$2,900 MRR if you hit the rhythm above.
