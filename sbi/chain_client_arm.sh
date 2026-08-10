#!/bin/bash
# Wait for the Opus GT arm to finish, then start the CLIENT-baseline Luna arm.
#
# Why chained rather than launched now: three workers share ONE workspace
# output-TPM budget and this SBI worker is capped at 2 concurrent arms. The GT arm
# and the refined-Luna arm already occupy both slots. The client-baseline arm is the
# brief's 3rd priority (refined-Luna full > Opus GT full > baseline-Luna full), so it
# takes the slot the GT arm releases instead of over-subscribing the budget.
#
# Chained in a detached script rather than by hand because three prior sessions died
# mid-run on this project; run_arm.py is idempotent, so a re-launch costs nothing.
cd /Users/mayanck.bihani/Savesage/bank_eval/sbi || exit 1

# 297 rather than 300: run_arm.py only retries NON-terminal records, so a statement
# left in an infrastructure-failure state (e.g. the one NETWORK_ERROR) keeps the count
# below 300 forever. Waiting for a hard 300 would deadlock this script.
while true; do
  n=$(ls run_gt/json 2>/dev/null | wc -l | tr -d ' ')
  live=$(pgrep -f "run_arm.py gt" | wc -l | tr -d ' ')
  if [ "$live" -eq 0 ]; then
    echo "$(date +%H:%M:%S) gt arm no longer running (records=$n) -> starting client arm"
    break
  fi
  sleep 60
done

exec python3 launch_detached.py luna_client --par 1
