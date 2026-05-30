#!/bin/bash
# Media Buyer daily cron — monitor → controller → generator → email report.
# DRY-RUN is the default; set MB_LIVE=1 in .env to actually mutate the ad account.
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

export $(grep -v '^#' .env 2>/dev/null | xargs) 2>/dev/null || true
export AGENT_PASSWORD="${AGENT_PASSWORD:-owner-cron}"

LOG_DIR="$SCRIPT_DIR/data/mb_cron_logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/$(date +%Y-%m-%d).log"

# Profile to run. Override at the cron line if you want a different one
# (e.g. add a second cron entry with MB_PROFILE=ecom for the e-com funnel).
KIND="${MB_PROFILE:-lead_gen}"

{
  echo ""
  echo "════════════════════════════════════════════"
  echo "  $(date '+%Y-%m-%d %H:%M:%S')  Media Buyer cron tick — kind=$KIND  dry_run=${MB_LIVE:+0}${MB_LIVE:-1}"
  echo "════════════════════════════════════════════"
  python3 run_media_buyer_auto.py --kind "$KIND" 2>&1
  echo ""
} >> "$LOG" 2>&1
