#!/usr/bin/env python3
"""Guard tests for the transaction matcher.

The matcher is the load-bearing piece of the whole evaluation: if it admits pairs
using the same fields it later scores, every one of those fields reads 100% by
construction. These tests assert the two properties that keep the numbers honest:

  1. NON-CIRCULARITY -- date/amount/direction must not influence matching.
  2. ORDER-INSENSITIVITY -- shuffling the input must not change which rows pair,
     except inside groups of genuinely identical descriptions where no
     description-only signal can distinguish the rows.
"""
import collections
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import score_lib as S

HERE = os.path.dirname(os.path.abspath(__file__))
fails = []


def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}  {detail}")
    if not cond:
        fails.append(name)


# ---- 1. non-circularity: identical descriptions, totally different date/amount
a = [{"description": "AMAZON PAY GROCERIES", "date": "01/07/2026", "amount": 100.0,
      "direction": "DEBIT", "currency": "INR"}]
b = [{"description": "AMAZON PAY GROCERIES", "date": "29/12/1999", "amount": 999999.0,
      "direction": "CREDIT", "currency": "INR"}]
pairs, up, ug = S.match_transactions(a, b)
check("matches on description despite every scored field differing", len(pairs) == 1)
check("and then REPORTS those fields as wrong (not silently correct)",
      not S.txn_field_equal("date", a[0]["date"], b[0]["date"])
      and not S.txn_field_equal("amount", a[0]["amount"], b[0]["amount"])
      and not S.txn_field_equal("direction", a[0]["direction"], b[0]["direction"]))

# ---- 2. a genuinely different description must NOT pair
c = [{"description": "SWIGGY BANGALORE", "date": "01/07/2026", "amount": 100.0}]
d = [{"description": "IGST-VPS2600287300576-RATE 18.0", "date": "01/07/2026", "amount": 100.0}]
pairs, up, ug = S.match_transactions(c, d)
check("unrelated descriptions do not pair even with identical date+amount",
      len(pairs) == 0 and len(up) == 1 and len(ug) == 1)

# ---- 3. real-corpus order-insensitivity ------------------------------------
sid = "decrypt_705330814_19c81ac46a73163b_0036XXXXXXXXXX87_20_02_2026_641"
# `phase1_generic/` is a STALE path -- the Phase 1 run was renamed phase1_baseline/ when
# the baseline was corrected to the client's own prompt. The old name silently SKIPped
# this whole check (the most valuable one: real HDFC rows with heavily repeated
# narrations). Fall back across the run dirs that actually exist rather than pinning one.
path = None
for _d in ("phase1_baseline", "phase2_refined", "phase3_refined"):
    _p = os.path.join(HERE, _d, "json", f"{sid}.json")
    if os.path.exists(_p):
        path = _p
        break
if path:
    lt = json.load(open(path))["parsed_json"]["transactions"]
    dupdesc = {k for k, v in collections.Counter(
        S.norm_desc(t["description"]) for t in lt).items() if v > 1}
    n_dup_rows = sum(1 for t in lt if S.norm_desc(t["description"]) in dupdesc)

    p, _, _ = S.match_transactions(lt, lt)
    check("self-match is perfectly diagonal", len(p) == len(lt) and all(i == j for i, j, _ in p),
          f"n={len(lt)}")

    worst = 0
    for seed in (1, 7, 42, 1234):
        sh = lt[:]
        random.Random(seed).shuffle(sh)
        p, _, _ = S.match_transactions(sh, lt)
        uniq_bad = sum(
            1 for i, j, _ in p for f in ("date", "amount", "direction")
            if S.norm_desc(sh[i]["description"]) not in dupdesc
            and not S.txn_field_equal(f, sh[i].get(f), lt[j].get(f)))
        worst = max(worst, uniq_bad)
    check("shuffling inputs introduces ZERO errors on unique-description rows",
          worst == 0,
          f"{len(lt)} rows, {n_dup_rows} of them inside {len(dupdesc)} duplicate-narration "
          f"groups (unresolvable by description alone, documented as a known limit)")
else:
    print("SKIP  real-corpus checks (phase1 record absent)")

print("\n" + ("ALL PASS" if not fails else f"FAILURES: {fails}"))
sys.exit(1 if fails else 0)
