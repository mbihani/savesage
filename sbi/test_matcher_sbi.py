#!/usr/bin/env python3
"""Prove the transaction matcher is NON-CIRCULAR and order-insensitive.

The bug this guards against already bit this project once: match rows on the
composite key (date, amount, direction), then report accuracy for date, amount and
direction. Every matched pair then agrees on those three BY CONSTRUCTION and all
three score ~100% no matter how wrong the model is.

Run: python3 test_matcher_sbi.py
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import score_lib_sbi as S  # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   {detail}" if detail else ""))
    if not cond:
        FAILED.append(name)


def tx(date, amount, desc, direction="DEBIT", currency="INR"):
    return {"date": date, "amount": amount, "description": desc,
            "direction": direction, "currency": currency, "txnType": None,
            "rewardPointsOnThisTransaction": None}


print("1. A pair whose date/amount/direction are ALL WRONG must still MATCH on")
print("   description, so the wrongness is scoreable rather than hidden.")
pred = [tx("01/01/2026", 999.0, "UPI-MONIKA LALIT CHANG", "CREDIT")]
ref = [tx("22/06/2026", 10.0, "UPI-MONIKA LALIT CHANG", "DEBIT")]
pairs, up, ur = S.match_txns_by_description(pred, ref)
check("matched despite every scored field disagreeing", len(pairs) == 1)
if pairs:
    m = pairs[0]
    check("date scores wrong_value",
          S.cmp_scalar("transactions[].date", m["pred"]["date"], m["ref"]["date"])[0]
          == "wrong_value")
    check("amount scores wrong_value",
          S.cmp_scalar("transactions[].amount", m["pred"]["amount"],
                       m["ref"]["amount"])[0] == "wrong_value")
    check("direction disagrees",
          S.direction(m["pred"]) != S.direction(m["ref"]))

print("\n2. Order-insensitivity: reversing one side must not change the pairing.")
a = [tx("22/06/2026", 10.0, "UPI-BLINKIT"), tx("23/06/2026", 48.0, "UPI-PRAHALAD DHOBI"),
     tx("24/06/2026", 90.0, "FLIPKART INTERNET PVT")]
b = list(reversed(a))
p1, _, _ = S.match_txns_by_description(a, b)
p2, _, _ = S.match_txns_by_description(a, list(a))
check("all 3 matched with ref reversed", len(p1) == 3)
check("same descriptions paired regardless of order",
      sorted(S.text(m["pred"]["description"]) for m in p1)
      == sorted(S.text(m["pred"]["description"]) for m in p2))

print("\n3. Assignment is strictly 1:1 -- no pred or ref row reused.")
dup = [tx("22/06/2026", 10.0, "UPI-BLINKIT")] * 3
one = [tx("22/06/2026", 10.0, "UPI-BLINKIT")]
pairs, up, ur = S.match_txns_by_description(dup, one)
check("3 preds vs 1 ref -> exactly 1 pair", len(pairs) == 1)
check("2 preds left unmatched (false positives)", len(up) == 2)
check("0 refs left unmatched", len(ur) == 0)
pairs2, up2, ur2 = S.match_txns_by_description(one, dup)
check("1 pred vs 3 refs -> exactly 1 pair", len(pairs2) == 1)
check("2 refs left unmatched (recall misses)", len(ur2) == 2)

print("\n4. A genuinely absent row must be a RECALL MISS, not a silent match.")
pred = [tx("22/06/2026", 10.0, "UPI-BLINKIT")]
ref = [tx("22/06/2026", 10.0, "UPI-BLINKIT"),
       tx("01/07/2026", 15050.0, "PAYMENT RECEIVED 000HD016182BALAAAEGQKTA", "CREDIT")]
pairs, up, ur = S.match_txns_by_description(pred, ref)
check("only 1 pair", len(pairs) == 1)
check("the dropped PAYMENT RECEIVED row is an unmatched ref", len(ur) == 1
      and "PAYMENT RECEIVED" in ur[0]["description"])

print("\n5. Unrelated descriptions must NOT match (threshold is a real gate).")
pairs, up, ur = S.match_txns_by_description(
    [tx("22/06/2026", 10.0, "UPI-BLINKIT")],
    [tx("22/06/2026", 10.0, "AMAZON PAY INDIA PRIVATE LTD MUMBAI")])
check("identical date+amount but unlike text -> no match", len(pairs) == 0,
      "date+amount agreement alone cannot admit a pair")

print("\n6. The date tie-break may only order EQUAL-similarity candidates; it must")
print("   never admit a pair below threshold nor invent a date agreement.")
pred = [tx("22/06/2026", 10.0, "UPI-MONIKA LALIT CHANG"),
        tx("23/06/2026", 10.0, "UPI-MONIKA LALIT CHANG")]
ref = [tx("23/06/2026", 10.0, "UPI-MONIKA LALIT CHANG"),
       tx("22/06/2026", 10.0, "UPI-MONIKA LALIT CHANG")]
pairs, up, ur = S.match_txns_by_description(pred, ref)
check("both identical-narration rows paired", len(pairs) == 2)
check("tie-break paired them on equal dates (a matcher artifact avoided, "
      "not a score inflated)",
      all(S.date_norm(m["pred"]["date"]) == S.date_norm(m["ref"]["date"]) for m in pairs))

print("\n7. SBI 2-digit-year dates must normalise (the canonical date_norm does not).")
check("'22 Jun 26' -> 22/06/2026", S.date_norm("22 Jun 26") == "22/06/2026")
check("'01 Jul 2026' -> 01/07/2026", S.date_norm("01 Jul 2026") == "01/07/2026")
check("ISO still works", S.date_norm("2026-07-22") == "22/07/2026")
check("non-date text preserved", S.date_norm("PAY IMMEDIATELY") == "PAY IMMEDIATELY")
check("a 2-digit-year date now EQUALS its 4-digit form",
      S.date_norm("22 Jun 26") == S.date_norm("22/06/2026"))

print("\n8. lastFourDigit: SBI's 2-real-digit mask must not be read as a wrong card,")
print("   and a genuinely different card must still be wrong.")
check("'XX25' vs 'XX25' correct",
      S.cmp_scalar("cards[].cardMeta.lastFourDigit", "XX25", "XX25")[0] == "correct")
check("'XX25' vs '0025' credited via MASK_DEPTH (both end in 25)",
      S.cmp_scalar("cards[].cardMeta.lastFourDigit", "XX25", "0025")
      == ("correct", "MASK_DEPTH"),
      "SBI masks to 2 real digits, so a 4-digit reference agreeing on the last 2 is a "
      "mask-depth difference, not a different card; kind=MASK_DEPTH keeps it visible")
check("'XX25' vs 'XX99' is wrong_value",
      S.cmp_scalar("cards[].cardMeta.lastFourDigit", "XX25", "XX99")[0] == "wrong_value")

print()
if FAILED:
    print(f"{len(FAILED)} CHECK(S) FAILED: {FAILED}")
    sys.exit(1)
print("all matcher checks passed")
