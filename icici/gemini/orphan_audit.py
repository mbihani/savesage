"""STEP 3: find rules in ICICI_PROMPT.txt that command output of fields the 26-leaf
schema CANNOT emit (additionalProperties:false => an active instruction/schema CONFLICT,
not merely dead weight).

CRITICAL: before deleting a whole section, check EVERY line in it. On HDFC the
BONUS_POINTS_RULE looked fully deletable, yet its last lines carried a LIVE rule
governing pointsEarnedThisCycle -- which IS in the schema -- and had to be RELOCATED,
not dropped. This script prints each orphan line WITH its neighbours and flags any
line that also names an IN-SCHEMA field, so a live rule cannot be dropped by accident.
"""

import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
PROMPT = os.path.join(HERE, "..", "ICICI_PROMPT.txt")
PROV = os.path.join(HERE, "GEMINI_SCHEMA_PROVENANCE.json")

prov = json.load(open(PROV))
IN_SCHEMA = sorted({p.split(".")[-1] for p in prov["leaves"]})

ORPHANS = ["financeChargesThisCycle", "utilisationPercent", "utilisation", "utilization",
           "bonusPointsThisCycle", "rawStatementId", "statementPeriodStart",
           "statementPeriodEnd", "cardCreditLimit", "cardLevelTotalAmountDue",
           "cardAvailableCreditLimit", "bigPicture"]

lines = open(PROMPT, encoding="utf-8").read().splitlines()

print(f"prompt: {PROMPT}  ({len(lines)} lines)")
print(f"in-schema leaf names: {len(IN_SCHEMA)}\n")

flagged = {}
for n, line in enumerate(lines, 1):
    o = [f for f in ORPHANS if re.search(re.escape(f), line, re.I)]
    if o:
        ins = [f for f in IN_SCHEMA if re.search(r"\b" + re.escape(f) + r"\b", line, re.I)]
        flagged[n] = (o, ins, line)

print("=" * 104)
print("ORPHAN-BEARING LINES  (>>> = ALSO names an in-schema field: LIVE RULE, do not drop)")
print("=" * 104)
for n in sorted(flagged):
    o, ins, line = flagged[n]
    mark = ">>>" if ins else "   "
    print(f"{mark} L{n:<4} orphans={o}")
    if ins:
        print(f"       ALSO IN-SCHEMA: {ins}")
    print(f"       {line}")

print("\n" + "=" * 104)
print("SECTION VIEW: each orphan line with 2 lines of context, so section boundaries are visible")
print("=" * 104)
shown = set()
for n in sorted(flagged):
    lo, hi = max(1, n - 2), min(len(lines), n + 2)
    if n in shown:
        continue
    print()
    for k in range(lo, hi + 1):
        shown.add(k)
        tag = "ORPHAN" if k in flagged else "      "
        live = " <<< LIVE in-schema field here" if k in flagged and flagged[k][1] else ""
        print(f"  {tag} L{k:<4} {lines[k-1]}{live}")

print("\n" + "=" * 104)
print("SUMMARY")
print("=" * 104)
live = {n: v for n, v in flagged.items() if v[1]}
print(f"orphan-bearing lines      : {sorted(flagged)}")
print(f"of those, LIVE (relocate) : {sorted(live)}")
for n, (o, ins, line) in sorted(live.items()):
    print(f"   L{n}: orphan={o} but governs in-schema {ins}")
