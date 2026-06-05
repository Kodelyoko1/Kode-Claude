#!/usr/bin/env bash
# Run --diagnose on every agent in the fleet, print a one-line summary per agent.
# Usage: ./run_fleet_diagnose.sh [--save]   (--save writes per-agent reports under data/diagnose_reports/)

set -u
cd "$(dirname "$0")"

# Source .env if present so SMTP/PayPal/ANTHROPIC creds are loaded
[ -f .env ] && export $(grep -v '^#' .env | xargs) 2>/dev/null

SAVE=0
[ "${1:-}" = "--save" ] && SAVE=1
[ "$SAVE" = "1" ] && mkdir -p data/diagnose_reports

printf "%-22s  %s\n" "AGENT" "DIAGNOSE SUMMARY"
printf "%-22s  %s\n" "------" "----------------"

p0_total=0
p1_total=0
agent_total=0
agent_failing=0
agent_passing=0

for f in run_*_auto.py; do
    agent=$(basename "$f" .py | sed 's/^run_//;s/_auto$//')
    agent_total=$((agent_total + 1))
    if [ "$SAVE" = "1" ]; then
        out=$(python3 "$f" --diagnose 2>&1 | tee "data/diagnose_reports/${agent}.txt")
    else
        out=$(python3 "$f" --diagnose 2>&1)
    fi
    # Extract the "Result: X/Y passed · P0=A · P1=B" line
    summary=$(echo "$out" | grep -E "Result:.*P0" | head -1)
    if [ -z "$summary" ]; then
        summary="(no result line — diagnose may have crashed)"
    fi
    printf "%-22s  %s\n" "$agent" "$summary"

    # Tally
    p0=$(echo "$summary" | grep -oE "P0[_= ]*[0-9]+" | grep -oE "[0-9]+" | head -1)
    p1=$(echo "$summary" | grep -oE "P1[_= ]*[0-9]+" | grep -oE "[0-9]+" | head -1)
    p0=${p0:-0}
    p1=${p1:-0}
    p0_total=$((p0_total + p0))
    p1_total=$((p1_total + p1))
    if [ "$p0" -gt 0 ]; then
        agent_failing=$((agent_failing + 1))
    else
        agent_passing=$((agent_passing + 1))
    fi
done

echo ""
echo "Fleet summary: $agent_passing/$agent_total agents pass · P0_fails=$p0_total · P1_warns=$p1_total"
if [ "$SAVE" = "1" ]; then
    echo "Per-agent reports saved to data/diagnose_reports/"
fi
