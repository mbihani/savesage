#!/bin/bash
# Run the whole measurement chain over whatever is on disk and assemble the report.
# Safe to re-run; every step is deterministic and reads only persisted artifacts.
set -e
cd /Users/mayanck.bihani/Savesage/bank_eval/sbi

echo "### matcher self-tests (non-circularity)"
python3 test_matcher_sbi.py | tail -3

echo "### scoring refined arm"
python3 score.py --luna run_luna_refined --gt run_gt --out scores_refined.json --tag refined | head -4

echo "### scoring client-baseline arm (full, if present)"
if [ -d run_luna_client/json ] && [ "$(ls run_luna_client/json | wc -l)" -gt 0 ]; then
  python3 score.py --luna run_luna_client --gt run_gt --out scores_client_full.json --tag client_full | head -4
fi

echo "### scoring the Phase-1 client-prompt run (the 10 tuning statements)"
python3 score.py --luna run_p1_client --gt run_gt --out scores_phase1_client.json --tag phase1_client | head -4

echo "### adjudicating Luna-vs-incumbent against the PDFs"
python3 adjudicate.py --luna run_luna_refined --out adjudication_refined.json | tail -12

echo "### token accounting"
python3 tokens.py --out tokens.json | tail -20

echo "### glaring misses"
python3 glaring.py --scores scores_refined.json --out glaring_misses.json \
  --md GLARING_MISSES.md --evidence-limit 80 | tail -12

echo "### report tables"
BASE=scores_client_full.json
[ -f "$BASE" ] || BASE=scores_phase1_client.json
python3 make_report.py --scores scores_refined.json --scores-client "$BASE" \
  --adj adjudication_refined.json --tokens tokens.json --out REPORT_TABLES.md > /dev/null

echo "### input integrity (READ-ONLY inputs must be unchanged)"
python3 verify_inputs.py

echo "done. artifacts: scores_*.json adjudication_refined.json tokens.json \
glaring_misses.json REPORT_TABLES.md"
