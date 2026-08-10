#!/bin/bash
# Finish the two priority sweeps, GT first.
#
# GT before challenger: the GT is the reference every accuracy number is measured
# against, so a statement with a challenger record but no GT record is unscoreable
# and its model call is wasted. Sequential, not parallel: three workers share ONE
# workspace output-token-per-minute budget.
cd /Users/mayanck.bihani/Savesage/bank_eval/sbi || exit 1

echo "=== $(date +%H:%M:%S) starting GT sweep ==="
python3 -u run_arm.py gt --par 1 >> logs/gt_full.log 2>&1
echo "=== $(date +%H:%M:%S) GT sweep exited rc=$? (records=$(ls run_gt/json | wc -l | tr -d ' ')) ==="

echo "=== $(date +%H:%M:%S) starting luna_refined sweep ==="
python3 -u run_arm.py luna_refined --par 1 >> logs/luna_refined_full.log 2>&1
echo "=== $(date +%H:%M:%S) luna_refined exited rc=$? (records=$(ls run_luna_refined/json | wc -l | tr -d ' ')) ==="

echo "=== $(date +%H:%M:%S) ALL SWEEPS DONE: gt=$(ls run_gt/json | wc -l | tr -d ' ') luna=$(ls run_luna_refined/json | wc -l | tr -d ' ') ==="
