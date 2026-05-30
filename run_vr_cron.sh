#!/bin/bash
# ViralRecycler cron worker — runs every N minutes from crontab.
# Drains the queue at data/vr_sources.json, uploads to YouTube, logs results.
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Load env vars
export $(grep -v '^#' .env 2>/dev/null | xargs) 2>/dev/null || true
export AGENT_PASSWORD="${AGENT_PASSWORD:-owner-cron}"

# Logging
LOG_DIR="$SCRIPT_DIR/data/vr_cron_logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/$(date +%Y-%m-%d).log"

{
  echo ""
  echo "════════════════════════════════════════════"
  echo "  $(date '+%Y-%m-%d %H:%M:%S')  ViralRecycler cron tick"
  echo "════════════════════════════════════════════"
  # Process up to 1 URL per tick. Daily cap enforced in viral_recycler/tools.py
  python3 run_viral_recycler_auto.py --max-uploads 1 2>&1
  echo ""
} >> "$LOG" 2>&1

# Tail to stdout if running manually
tail -20 "$LOG"
