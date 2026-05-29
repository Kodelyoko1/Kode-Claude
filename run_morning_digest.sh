#!/bin/bash
# Morning Facebook draft — emails you a ready-to-paste post for Meta Business Suite.
# Rotates between buyer + wholesaler audiences. Nightly run handles sellers.
cd /home/tylumiere25/wholesale_agent
set -a; source .env; set +a

LOGFILE="/home/tylumiere25/wholesale_auto.log"
echo "" >> "$LOGFILE"
echo "----------------------------------------" >> "$LOGFILE"
echo "MORNING POST — $(date '+%Y-%m-%d %H:%M:%S')" >> "$LOGFILE"
echo "----------------------------------------" >> "$LOGFILE"

# Alternate audience by day-of-week parity so the Page doesn't sound repetitive.
if [ $(($(date +%j) % 2)) -eq 0 ]; then
  AUDIENCE="wholesalers"
else
  AUDIENCE="buyers"
fi

echo "Audience for today: $AUDIENCE" >> "$LOGFILE"
python3 run_fb_draft.py --audience "$AUDIENCE" >> "$LOGFILE" 2>&1
python3 run_ig_draft.py --audience "$AUDIENCE" >> "$LOGFILE" 2>&1

echo "MORNING DRAFT DONE — $(date '+%Y-%m-%d %H:%M:%S')" >> "$LOGFILE"
