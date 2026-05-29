#!/bin/bash
# Nightly autonomous run — all Wholesale Omniverse agents
cd /home/tylumiere25/wholesale_agent
set -a; source .env; set +a

LOGFILE="/home/tylumiere25/wholesale_auto.log"
echo "" >> "$LOGFILE"
echo "========================================" >> "$LOGFILE"
echo "NIGHTLY RUN — $(date '+%Y-%m-%d %H:%M:%S')" >> "$LOGFILE"
echo "========================================" >> "$LOGFILE"

echo "[0/9] Bounce Processor — flag dead addresses before any outbound..." >> "$LOGFILE"
python3 process_bounces.py --since 2 >> "$LOGFILE" 2>&1

echo "[1/9] Cash Buyer Finder..." >> "$LOGFILE"
python3 run_buyer_finder_auto.py >> "$LOGFILE" 2>&1

echo "[2/9] Seller Follow-Ups..." >> "$LOGFILE"
python3 run_followup_auto.py >> "$LOGFILE" 2>&1

echo "[3/9] Outreach Service Campaigns..." >> "$LOGFILE"
python3 run_outreach_auto.py >> "$LOGFILE" 2>&1

echo "[4/9] Deal Analyzer Autonomous Cycle..." >> "$LOGFILE"
python3 main.py --auto >> "$LOGFILE" 2>&1

echo "[5/9] Client Prospector (SAAS/OAS sales leads)..." >> "$LOGFILE"
python3 run_prospector_auto.py >> "$LOGFILE" 2>&1

echo "[6/9] Facebook Daily Draft (emailed to you for manual posting)..." >> "$LOGFILE"
python3 run_fb_draft.py --audience sellers >> "$LOGFILE" 2>&1

echo "[7/9] Instagram Daily Draft (manual until IG Business linked + scopes added)..." >> "$LOGFILE"
python3 run_ig_draft.py --audience sellers >> "$LOGFILE" 2>&1

echo "[8/9] Pinterest Daily Draft (manual until API approved)..." >> "$LOGFILE"
python3 run_pinterest_draft.py >> "$LOGFILE" 2>&1

echo "[9/9] Netlify form bridge — pull seller leads from website..." >> "$LOGFILE"
python3 pull_netlify_leads.py --quiet >> "$LOGFILE" 2>&1

echo "DONE — $(date '+%Y-%m-%d %H:%M:%S')" >> "$LOGFILE"
