# FBAds — Setup & Operating Guide

The 14-ad pack is **already buildable today** without any Meta credentials.
The CSV at `data/fb_packs/<date>.csv` is import-ready for Meta Ads Manager.

```bash
python3 run_fbads_auto.py --build       # generate JSON + CSV
python3 run_fbads_auto.py --show        # summary
python3 run_fbads_auto.py --higgsfield  # Higgsfield video prompts
```

To switch from "owner manually creates campaigns in Meta UI" to
"agent pushes via API", you need three env vars in `.env`.

---

## Three env vars to unlock API push

### 1. `META_ACCESS_TOKEN`

System User token from Meta Business Manager.

1. https://business.facebook.com → Business Settings (gear icon, top right)
2. Left sidebar: Users → **System Users**
3. Click **Add** → name it "WholesaleOmniverse Ads Bot" → role "Admin"
4. Click the system user → **Add Assets** → assign your Ad Account + Page
5. Click **Generate New Token** → app = your registered Meta app → permissions:
   - `ads_management`
   - `ads_read`
   - `business_management`
   - `pages_show_list`
   - `pages_manage_metadata`
   - `pages_messaging`
6. Token expires NEVER (system user tokens are long-lived) — paste it into `.env`:
   ```
   META_ACCESS_TOKEN=<long-string-starting-with-EAA->
   ```

### 2. `META_AD_ACCOUNT_ID`

Find at business.facebook.com → Business Settings → Accounts → Ad Accounts.
The ID is numeric. Prefix it with `act_` for the env var:

```
META_AD_ACCOUNT_ID=act_1234567890
```

### 3. `META_PAGE_ID`

Open your Facebook Page → About → scroll to bottom → "Page ID".

```
META_PAGE_ID=1234567890
```

---

## Verify + push

```bash
# 1. Reload .env (your shell or .verify_paypal.sh)
./verify_paypal.sh    # just to source .env

# 2. Confirm Meta creds work
python3 run_fbads_auto.py --diagnose
# Should now report: [✓] [P0] Meta credentials

# 3. Build today's pack if not built
python3 run_fbads_auto.py --build

# 4. Dry-run launch (no Meta records created)
python3 run_fbads_auto.py --launch

# 5. Real launch — creates all campaigns/adsets/ads as PAUSED
#    Review in Meta Ads Manager, unpause manually when ready.
python3 run_fbads_auto.py --launch --live --max 3
```

Every ad is created **PAUSED** so you can review copy + targeting in Meta's
UI before unpausing. Nothing spends until you click activate.

---

## Higgsfield video creatives

Higgsfield doesn't (yet) publish a documented REST API for programmatic
generation. The integration uses the **paste-and-render** model:

```bash
python3 run_fbads_auto.py --higgsfield
# → writes data/fb_packs/<date>_higgsfield.txt with one block per ad
```

Each block is a Higgsfield-style cinematic prompt (scene + camera +
motion + mood + on-screen text overlay) tuned to the ad's audience.

For each block:
1. Open Higgsfield (https://higgsfield.ai)
2. Paste the prompt
3. Render at 9:16, 5-6s
4. Download the MP4
5. In Meta Ads Manager, replace the placeholder image with the MP4

When/if `HIGGSFIELD_API_KEY` becomes available with a documented POST
endpoint, fill in `fbads/higgsfield.py::api_push` to skip the manual step.

---

## Cron pattern (after API push works)

```bash
# Generate a fresh pack at 6am, push it (paused) at 7am
0 6 * * 1  cd /home/tylumiere25/wholesale_agent && python3 run_fbads_auto.py --build >> data/fbads_cron.log 2>&1
0 7 * * 1  cd /home/tylumiere25/wholesale_agent && python3 run_fbads_auto.py --launch --live --max 5 >> data/fbads_cron.log 2>&1
```

That ships **5 new PAUSED ads every Monday** for your weekly review.

---

## Budget control

Defaults in `fbads/tools.py::AUDIENCE_TARGETING`:

| Audience    | $/day | duration |
|---|--:|--:|
| sellers     |   7   |  3d      |
| buyers      |  10   |  5d      |
| wholesalers |  10   |  5d      |
| creators    |   8   |  5d      |
| jobseekers  |  10   |  5d      |
| podcasters  |   7   |  5d      |
| local_biz   |   7   |  5d      |
| **Total potential daily** | **$59** | |

A full pack at 3 ads per audience = **$118/day potential**. With `--max 5`
you cap to 5 ads = ~$40/day. Adjust `daily_budget` in
`AUDIENCE_TARGETING` to taste.

---

## When in doubt

- `python3 run_fbads_auto.py --diagnose` — what's blocking
- `python3 run_fbads_auto.py --show` — what's in the latest pack
- `python3 run_fbads_auto.py --launch` (no `--live`) — dry-run a launch
