#!/bin/bash
# Which historical version of ICICI_PROMPT.txt has sha256 2ba79095...?
cd /Users/mayanck.bihani/Savesage/bank_eval
echo "=== git log for icici/ICICI_PROMPT.txt ==="
for c in $(git log --format=%H -- icici/ICICI_PROMPT.txt); do
  sha=$(git show "$c:icici/ICICI_PROMPT.txt" 2>/dev/null | shasum -a 256 | cut -c1-16)
  sz=$(git show "$c:icici/ICICI_PROMPT.txt" 2>/dev/null | wc -c | tr -d ' ')
  subj=$(git log -1 --format='%ad %s' --date=short "$c")
  echo "$sha  ${sz}B  ${c:0:8}  $subj"
done
echo
echo "=== also check GENERIC_PROMPT history ==="
for c in $(git log --format=%H -- icici/GENERIC_PROMPT.txt); do
  sha=$(git show "$c:icici/GENERIC_PROMPT.txt" 2>/dev/null | shasum -a 256 | cut -c1-16)
  echo "$sha  ${c:0:8}  $(git log -1 --format='%ad %s' --date=short "$c")"
done
