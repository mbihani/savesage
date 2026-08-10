#!/bin/bash
# Keep the SBI arms running at concurrency <=2 until all three are complete.
#
# Two jobs:
#  1. RESTART an arm that stopped before finishing. Three prior sessions died mid-run on
#     this project, and in this session harness-cancelled foreground commands twice took
#     the background arms down with them. run_arm.py is idempotent (terminal records are
#     skipped), so a restart costs zero completed work.
#  2. HOLD THE CEILING at 2 concurrent arms. Three workers share ONE workspace
#     output-TPM budget. Priority order comes from the brief:
#        refined-Luna full  >  Opus GT full  >  client-baseline-Luna full
#
# Counts are compared against a per-arm TARGET below rather than 300, because an arm can
# leave a statement in a non-terminal infrastructure-failure state (one GT statement hit
# a broken pipe); waiting for a hard 300 would spin forever.
cd /Users/mayanck.bihani/Savesage/bank_eval/sbi || exit 1
TARGET=299

# Match only MY arms. The sibling HDFC/ICICI workers run `run_arm.py --arm <name>` from
# their own directories; a bare `pgrep -f "run_arm.py luna_refined"` would not match them
# (the `--arm ` sits in between), but anchoring on this directory makes it unambiguous.
# Count only real PYTHON arm processes. A bare pgrep -f also matches any shell/monitor
# whose command line merely CONTAINS the pattern (my own progress monitor does), which
# would overcount and, worse, could make this supervisor think a slot was busy or free
# when it was not. `pgrep -x`-style filtering is not enough here because the binary is
# the framework Python, so the command text is inspected explicitly.
live() {
  local n=0 p cmd
  for p in $(pgrep -f "bank_eval/sbi/run_arm.py $1" 2>/dev/null); do
    cmd=$(ps -o command= -p "$p" 2>/dev/null)
    case "$cmd" in
      *[Pp]ython*run_arm.py*"$1"*) n=$((n + 1)) ;;
    esac
  done
  echo "$n"
}
recs() { ls "run_$2/json" 2>/dev/null | wc -l | tr -d ' '; }

# 429 SAFETY VALVE. The binding workspace limit is output tokens per minute and THREE
# workers share it. If any of my records has actually been rate-limited, drop to a single
# concurrent arm and stay there -- backing off is strictly better than three workers
# thrashing the same minute window.
max_slots() {
  local n
  n=$(grep -l '"rate_limited": [1-9]' run_*/json/*.json 2>/dev/null | wc -l | tr -d ' ')
  if [ "${n:-0}" -gt 0 ]; then echo 1; else echo 2; fi
}

while true; do
  n_live=0
  for a in luna_refined gt luna_client; do
    n_live=$((n_live + $(live "$a")))
  done
  slots=$(max_slots)

  # start the highest-priority incomplete, not-running arm if there is a free slot
  if [ "$n_live" -lt "$slots" ]; then
    for pair in "luna_refined:luna_refined" "gt:gt" "luna_client:luna_client"; do
      arm="${pair%%:*}"; dir="${pair##*:}"
      if [ "$(live "$arm")" -eq 0 ] && [ "$(recs "$arm" "$dir")" -lt "$TARGET" ]; then
        echo "$(date +%H:%M:%S) starting $arm ($(recs "$arm" "$dir") records, live=$n_live, slots=$slots)"
        python3 launch_detached.py "$arm" --par 1
        sleep 20
        break
      fi
    done
  fi

  done_all=1
  for pair in "luna_refined:luna_refined" "gt:gt" "luna_client:luna_client"; do
    arm="${pair%%:*}"; dir="${pair##*:}"
    [ "$(recs "$arm" "$dir")" -ge "$TARGET" ] || done_all=0
  done
  if [ "$done_all" -eq 1 ]; then
    echo "$(date +%H:%M:%S) all three arms at >=$TARGET records; keepalive exiting"
    exit 0
  fi
  sleep 90
done
