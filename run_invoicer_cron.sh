#!/usr/bin/env bash
# Invoicer cron wrapper.
#
# Behavior:
#   1. Source .env (PayPal creds, SMTP, INVOICER_LIVE flag)
#   2. Probe PayPal Invoicing endpoint — if 403/auth fails, bail with
#      a log line. We do NOT want to spam empty cycles when the feature
#      is not enabled on the live app yet.
#   3. Run one cycle. Defaults to dry-run unless INVOICER_LIVE=1 is set
#      in .env or in the cron line itself.
#
# Logs go to /home/tylumiere25/wholesale_agent/data/invoicer_cron.log
# (rotated by length: head -2000 keeps it bounded).

set -u
cd /home/tylumiere25/wholesale_agent

# Source .env
if [ -f .env ]; then
    export $(grep -v '^#' .env | xargs) 2>/dev/null
fi

LOG=/home/tylumiere25/wholesale_agent/data/invoicer_cron.log
mkdir -p data

echo "==== $(date -Iseconds) ====" >> "$LOG"

# Probe — bail without running a cycle if PayPal isn't ready.
probe=$(python3 -c "
import sys
from invoicer.health import probe_paypal_invoicing
r = probe_paypal_invoicing()
print('ok' if r.get('ok') else 'fail:' + str(r.get('status_code','?')) + ':' + r.get('error','')[:60])
" 2>&1)

case "$probe" in
    ok)
        echo "PayPal Invoicing ok — running cycle" >> "$LOG"
        # Use --probe path indirectly by calling cycle directly.
        # We DON'T want paywall_prompt here (cron is non-interactive), so
        # invoke run_cycle from Python so we skip the paywall.
        python3 - >> "$LOG" 2>&1 <<'PYEOF'
from invoicer.tools import run_cycle, LIVE
import json
result = run_cycle()
print(f"mode={'LIVE' if LIVE else 'dry'}  due={result['due_found']}  "
      f"sent={result['sent']}  failed={result['failed']}")
PYEOF
        ;;
    *)
        echo "PayPal probe failed: $probe — skipping cycle" >> "$LOG"
        ;;
esac

# Bound the log to last 2000 lines
if [ "$(wc -l < "$LOG" 2>/dev/null)" -gt 2000 ]; then
    tail -2000 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
fi
