#!/bin/bash
# Master cron runner for all 11 autonomous revenue agents.
# Add to crontab:
#   0 9 * * *  /home/tylumiere25/wholesale_agent/run_all_autonomous_agents.sh
set -e
cd /home/tylumiere25/wholesale_agent
export $(grep -v '^#' .env 2>/dev/null | xargs) 2>/dev/null || true

log() { echo "[$(date +'%Y-%m-%d %H:%M:%S')] $1"; }

log "── DAILY AGENTS ──"
python3 run_storyforge_auto.py     || log "storyforge failed"
python3 run_pantrychef_auto.py     || log "pantrychef failed"
python3 run_careerforge_auto.py    || log "careerforge failed"
python3 run_reputation_guard_auto.py || log "reputation_guard failed"
python3 run_shortsforge_auto.py    || log "shortsforge failed"
python3 run_viral_recycler_auto.py --max-uploads 1 || log "viral_recycler failed"

# Weekly agents — Mondays only
if [ "$(date +%u)" = "1" ]; then
    log "── WEEKLY AGENTS (Monday) ──"
    python3 run_towncrier_auto.py        || log "towncrier failed"
    python3 run_trendscout_auto.py       || log "trendscout failed"
    python3 run_paperbrief_auto.py       || log "paperbrief failed"
    python3 run_nichelens_auto.py        || log "nichelens failed"
    python3 run_link_mender_auto.py      || log "link_mender failed"
    python3 run_gutenberg_voice_auto.py  || log "gutenberg_voice failed"
fi

log "── REFRESH DASHBOARD ──"
python3 run_ecosystem_dashboard.py --html

log "── DONE ──"
