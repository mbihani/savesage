#!/usr/bin/env python3
"""Regression suite for the description/direction adjudicator.

Every case here was verified BY HAND against the PDF text before being encoded, and
each one corresponds to a bug that actually shipped a wrong verdict during this run:

  * a plain substring test scored the incumbent's TRUNCATED value as "printed", hiding
    real fidelity defects as AMBIGUOUS;
  * requiring a single printed line then over-corrected, because HDFC genuinely wraps
    long narrations mid-"(Ref# ...)";
  * the badge list was too narrow: HDFC also prints the FOREIGN-CURRENCY amount and bare
    reward-point counts on their own lines, and swallowing those is column bleed, not a
    wrap;
  * `direction` inspected only the FIRST occurrence of a repeated narration, which let
    one row be judged by another row's marker (and produced a self-contradictory verdict).

Run: python3 test_adjudicator.py
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import adjudicate_txn as A

FAILS = []


def check(name, got, want):
    ok = got == want
    if not ok:
        FAILS.append(f"{name}: got {got!r} want {want!r}")
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")


# ---------------------------------------------------------------- description

# HDFC prints a standalone 'EMI' badge line above the merchant narration.
EMI_PDF = ("05/07/2026| 19:07\nEMI\nPRESTIGEGHAZIABAD\n+ 76\n C 3,990.00\nl\n")

# A long narration legitimately WRAPS mid-'(Ref# ...)'.
WRAP_PDF = ("26| 00:00\nIGST-VPS2718699250565-RATE 18.0 -06 (Ref#\n"
            "09999999980704001141587)\n C 27.54\nl\n")

# Foreign-currency spend: the FX amount sits on its own line, like the rupee column.
FX_PDF = ("17/03/2026 | 11:30\nCURSOR, AI POWERED IDECURSOR.COM\nUSD 20.00\n"
          " C 1,849.76\nl\n")

print("description verdicts")
check("EMI badge must NOT be joined to narration (Luna wrong)",
      A.desc_verdict(EMI_PDF, "EMI PRESTIGEGHAZIABAD", "PRESTIGEGHAZIABAD")[0],
      "LUNA_WRONG")
check("narration wrapping mid-(Ref#) IS printed (CSV truncated)",
      A.desc_verdict(WRAP_PDF,
                     "IGST-VPS2718699250565-RATE 18.0 -06 (Ref# 09999999980704001141587)",
                     "IGST-VPS2718699250565-RATE 18.0 -06")[0],
      "CSV_WRONG")
check("FX amount column must NOT be appended to narration (CSV wrong)",
      A.desc_verdict(FX_PDF, "CURSOR, AI POWERED IDECURSOR.COM",
                     "CURSOR, AI POWERED IDECURSOR.COM USD 20.00")[0],
      "CSV_WRONG")
check("a value not printed at all loses to one that is",
      A.desc_verdict("ANTHROPIC* CLAUDE SUBSAN FRANCISC\n C 100.00\n",
                     "ANTHROPIC* CLAUDE SUBSAN FRANCISCO",
                     "ANTHROPIC* CLAUDE SUBSAN FRANCISC")[0],
      "LUNA_WRONG")

# ---------------------------------------------------------------- direction

# The rupee glyph 'C' is NOT a credit marker; '+' is. Same narration twice, one of each.
REPEAT_PDF = (
    "03/02/2026| 12:40\nSWIGGYBENGALURU\n+  C 238.00\nl\n"
    "05/02/2026| 18:10\nSWIGGYBENGALURU\n C 512.00\nl\n")

print("\ndirection verdicts")
# amount pins the '+' occurrence -> that row really is a CREDIT
check("repeated narration: amount pins the '+' row (Luna wrong to say DEBIT)",
      A.direction_verdict(REPEAT_PDF, "SWIGGYBENGALURU", "DEBIT", "CREDIT",
                          amount=238.0)[0],
      "LUNA_WRONG")
# amount pins the plain-'C' occurrence -> DEBIT, so CSV's CREDIT is wrong
check("repeated narration: amount pins the plain-'C' row (CSV wrong to say CREDIT)",
      A.direction_verdict(REPEAT_PDF, "SWIGGYBENGALURU", "DEBIT", "CREDIT",
                          amount=512.0)[0],
      "CSV_WRONG")
# no amount to disambiguate and the markers disagree -> must NOT pick a side
check("repeated narration, markers disagree, no amount -> AMBIGUOUS",
      A.direction_verdict(REPEAT_PDF, "SWIGGYBENGALURU", "DEBIT", "CREDIT")[0],
      "AMBIGUOUS_IN_PDF")
check("bare 'C' amount is the rupee sign, not CREDIT",
      A.direction_verdict("17/06/2026| 16:06\nUPI-SAIMA BANU\n C 31.00\nl\n",
                          "UPI-SAIMA BANU", "CREDIT", "DEBIT", amount=31.0)[0],
      "LUNA_WRONG")
check("leading '+' IS a credit marker",
      A.direction_verdict("30/06/2026| 14:08\nCREDIT CARD PAYMENTNet Banking\n"
                          "+  C 2,600.00\nl\n",
                          "CREDIT CARD PAYMENTNet Banking", "CREDIT", "DEBIT",
                          amount=2600.0)[0],
      "CSV_WRONG")

# ---------------------------------------------------------------- amount

print("\namount verdicts")
check("negated credit violates the schema's positive-magnitude contract",
      A.amount_verdict(" C 28.03\n", 28.03, -28.03), "CSV_WRONG")
check("Indian lakh grouping is matched when checking a printed magnitude",
      A.amount_verdict("TOTAL\nC1,94,022.00\n", 194022.0, 94022.0), "CSV_WRONG")

print()
if FAILS:
    print("FAILURES:")
    for f in FAILS:
        print("  " + f)
    raise SystemExit(1)
print("ALL PASS")
