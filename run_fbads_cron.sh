#!/usr/bin/env bash
# fbads daily cron wrapper.
#
# Daily flow (each step is independent — one failing doesn't block the rest):
#   1. Push pending CAPI Lead+Purchase events to Meta (server-side attribution).
#   2. (gated) Build today's fresh pack + launch up to N new ads to Meta PAUSED.
#   3. Pull Meta Insights + compute co-occurrence attribution.
#   4. Email the daily performance report to SMTP_USER.
#
# Why this order: CAPI push goes first so any activations from the last
# 24h get attributed server-side before we ask Meta for insights. Meta's
# attribution lag means today's report still reflects yesterday's events,
# but events fired today will land in tomorrow's pull — that's fine.
#
# Launch gating (off by default):
#   FBADS_LAUNCH=1          enable the build+launch step in this cron
#   FBADS_LAUNCH_MAX=3      cap how many NEW ads/day (deduped against
#                            data/fbads_launched.json — re-runs skip
#                            ad_names already pushed)
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

# 2. (gated) Build + launch fresh ads — only if FBADS_LAUNCH=1
if [ "${FBADS_LAUNCH:-0}" = "1" ]; then
    LAUNCH_MAX="${FBADS_LAUNCH_MAX:-3}"
    echo "[launch] FBADS_LAUNCH=1 — building today's pack" >> "$LOG"
    python3 run_fbads_auto.py --build >> "$LOG" 2>&1 \
        || echo "[launch] build FAILED (continuing)" >> "$LOG"
    echo "[launch] pushing up to $LAUNCH_MAX new ads (PAUSED, deduped via ledger)" >> "$LOG"
    python3 run_fbads_auto.py --launch --live --max "$LAUNCH_MAX" >> "$LOG" 2>&1 \
        || echo "[launch] launch FAILED (continuing)" >> "$LOG"
else
    echo "[launch] skipped — set FBADS_LAUNCH=1 to enable daily auto-launch" >> "$LOG"
fi

# 3. Insights pull + attribution
echo "[monitor] pulling Insights + computing attribution" >> "$LOG"
python3 run_fbads_auto.py --monitor >> "$LOG" 2>&1 \
    || echo "[monitor] FAILED (continuing)" >> "$LOG"

# 4. Email the daily report
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
