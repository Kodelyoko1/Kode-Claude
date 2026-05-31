#!/bin/bash
# Master cron runner for all 13 autonomous revenue agents.
# Add to crontab:
#   0 9 * * *  /home/tylumiere25/wholesale_agent/run_all_autonomous_agents.sh
set -e
cd /home/tylumiere25/wholesale_agent
set -a
[ -f .env ] && . .env 2>/dev/null
set +a

log() { echo "[$(date +'%Y-%m-%d %H:%M:%S')] $1"; }

log "── DAILY AGENTS ──"
python3 run_propscout_auto.py      || log "propscout failed"
python3 run_coldcaller_auto.py     || log "coldcaller failed"
python3 run_storyforge_auto.py     || log "storyforge failed"
python3 run_pantrychef_auto.py     || log "pantrychef failed"
python3 run_careerforge_auto.py    || log "careerforge failed"
python3 run_reputation_guard_auto.py || log "reputation_guard failed"
python3 run_shortsforge_auto.py    || log "shortsforge failed"
python3 run_viral_recycler_auto.py --max-uploads 1 || log "viral_recycler failed"
python3 run_dropship_scout_auto.py     || log "dropship_scout failed"
python3 run_salespage_doctor_auto.py   || log "salespage_doctor failed"
python3 run_transcribe_auto.py         || log "transcribe failed"
python3 run_shownotes_auto.py          || log "shownotes failed"
python3 run_thumbforge_auto.py         || log "thumbforge failed"
python3 run_carouselforge_auto.py      || log "carouselforge failed"
python3 run_seowriter_auto.py          || log "seowriter failed"
python3 run_inboxzero_auto.py          || log "inboxzero failed"
python3 run_notiontemplate_auto.py     || log "notiontemplate failed"
python3 run_modbot_auto.py             || log "modbot failed"
python3 run_proofbot_auto.py           || log "proofbot failed"
python3 run_podcleaner_auto.py         || log "podcleaner failed"
python3 run_chatconfig_auto.py         || log "chatconfig failed"
python3 run_bentoforge_auto.py         || log "bentoforge failed"
python3 run_templateforge_auto.py      || log "templateforge failed"
python3 run_plannerforge_auto.py       || log "plannerforge failed"
python3 run_deckforge_auto.py          || log "deckforge failed"

# Weekly agents — Mondays only
if [ "$(date +%u)" = "1" ]; then
    log "── WEEKLY AGENTS (Monday) ──"
    python3 run_towncrier_auto.py        || log "towncrier failed"
    python3 run_trendscout_auto.py       || log "trendscout failed"
    python3 run_paperbrief_auto.py       || log "paperbrief failed"
    python3 run_nichelens_auto.py        || log "nichelens failed"
    python3 run_link_mender_auto.py      || log "link_mender failed"
    python3 run_gutenberg_voice_auto.py  || log "gutenberg_voice failed"
    python3 run_speedaudit_auto.py       || log "speedaudit failed"
    python3 run_courseforge_auto.py      || log "courseforge failed"
    python3 run_localize_auto.py         || log "localize failed"
    python3 run_domainscout_auto.py      || log "domainscout failed"
fi

log "── REFRESH DASHBOARD ──"
python3 run_ecosystem_dashboard.py --html

log "── DONE ──"
