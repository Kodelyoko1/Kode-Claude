# ViralRecycler — Deploy to Render (free tier)

## Step 1 — Push to GitHub (one-time)

```bash
cd /home/tylumiere25/wholesale_agent
git init  # if not already a repo
git add .
git commit -m "Initial commit"
# Create a GitHub repo at github.com/new (private is fine)
git remote add origin git@github.com:YOUR_USER/wholesale_agent.git
git push -u origin main
```

## Step 2 — Connect Render to your repo

1. Go to **render.com** → Sign in with GitHub (free)
2. New → **Blueprint**
3. Connect the repo you just pushed
4. Render reads `render.yaml` and proposes the service — click **Apply**

## Step 3 — Set secret env vars in the Render dashboard

In the Render dashboard for the service, set:

| Variable | What to put |
|----------|-------------|
| `SMTP_USER` | `WholesaleOmniverse@gmail.com` |
| `SMTP_PASS` | your 16-char Gmail app password (from myaccount.google.com/apppasswords) |
| `AGENT_PASSWORD` | anything (owner bypass token) |
| `TIKTOK_ACCESS_TOKEN` | leave blank until TikTok approves your app |

## Step 4 — First deploy

Render builds the Docker image (≈5 min the first time), starts the server, and
gives you a public URL like:

```
https://viral-recycler-saas.onrender.com
```

That URL serves:
- `/` — marketing site
- `/viral-recycler/signup` — trial signup
- `/viral-recycler/dashboard?key=…` — customer dashboard
- `/api/*` — JSON API endpoints

## Step 5 — Point a custom domain (optional)

Render → Settings → Custom Domains → add `viralrecycler.com` (or any domain you own).

## Free-tier notes

- 750 hours/month free (≈one always-on service)
- Service spins down after 15 min idle, spins back up on first request (≈30s cold start)
- 1 GB persistent disk for `/app/data` (trials, customers, uploads)
- HTTPS auto-provisioned

## YouTube OAuth on Render

Render is headless — you can't run the browser-based OAuth flow there.
Authorize once locally, then upload `data/yt_token.json` to Render via:

```bash
# Run locally first
python3 setup_viral_recycler.py --authorize
# Then commit the token (it's a refresh token, not a password)
git add data/yt_token.json
git commit -m "Add YouTube refresh token"
git push
```

Render redeploys automatically and the worker has YouTube access on every customer's queue.
