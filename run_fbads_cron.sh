#!/usr/bin/env bash
# fbads daily cron wrapper.
#
# Daily flow (each step is independent — one failing doesn't block the rest):
#   1. Push pending CAPI Lead+Purchase events to Meta (server-side attribution).
#   2. Pull Meta Insights + compute co-occurrence attribution.
#   3. Email the daily performance report to SMTP_USER.
#
# Why this order: CAPI push goes first so any activations from the last
# 24h get attributed server-side before we ask Meta for insights. Meta's
# attribution lag means today's report still reflects yesterday's events,
# but events fired today will land in tomorrow's pull — that's fine.
#
# Logs: data/fbads_cron.log (rotated to last 2000 lines).

set -u
cd /home/tylumiere25/wholesale_agent

if [ -f .env ]; then
    export $(grep -v '^#' .env | xargs) 2>/dev/null
fi

LOG=/home/tylumiere25/wholesale_agent/data/fbads_cron.log
mkdir -p data

echo "==== $(date -Iseconds) ====" >> "$LOG"

# 1. CAPI push — bail this step only if creds missing; report still runs.
if [ -n "${MB_LEADGEN_PIXEL_ID:-}" ] && [ -n "${META_ACCESS_TOKEN:-}" ]; then
    echo "[capi] firing pending events" >> "$LOG"
    python3 run_fbads_auto.py --push-conversions >> "$LOG" 2>&1 \
        || echo "[capi] FAILED (continuing)" >> "$LOG"
else
    echo "[capi] skipped — MB_LEADGEN_PIXEL_ID or META_ACCESS_TOKEN unset" >> "$LOG"
fi

# 2. Insights pull + attribution
echo "[monitor] pulling Insights + computing attribution" >> "$LOG"
python3 run_fbads_auto.py --monitor >> "$LOG" 2>&1 \
    || echo "[monitor] FAILED (continuing)" >> "$LOG"

# 3. Email the daily report
if [ -n "${SMTP_USER:-}" ] && [ -n "${SMTP_PASS:-}" ]; then
    echo "[report] emailing daily digest to $SMTP_USER" >> "$LOG"
    python3 run_fbads_auto.py --email-report >> "$LOG" 2>&1 \
        || echo "[report] FAILED" >> "$LOG"
else
    echo "[report] skipped — SMTP creds unset" >> "$LOG"
fi

# Bound the log
if [ "$(wc -l < "$LOG" 2>/dev/null)" -gt 2000 ]; then
    tail -2000 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
fi
