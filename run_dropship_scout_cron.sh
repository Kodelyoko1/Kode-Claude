#!/bin/bash
# DropshipScout cron worker — refreshes the public lead-magnet page hourly.
# Scrapes Amazon Best Sellers (5 categories) and updates website/dropship_scout_trends.html.
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

export $(grep -v '^#' .env 2>/dev/null | xargs) 2>/dev/null || true
export AGENT_PASSWORD="${AGENT_PASSWORD:-owner-cron}"

LOG_DIR="$SCRIPT_DIR/data/ds_cron_logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/$(date +%Y-%m-%d).log"

{
  echo ""
  echo "════════════════════════════════════════════"
  echo "  $(date '+%Y-%m-%d %H:%M:%S')  DropshipScout cron tick"
  echo "════════════════════════════════════════════"
  python3 run_dropship_scout_auto.py 2>&1
  echo ""
} >> "$LOG" 2>&1
