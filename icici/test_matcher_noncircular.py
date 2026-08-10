#!/usr/bin/env python3
"""Proof obligations for the transaction matcher. Run: python3 test_matcher_noncircular.py

The bug this guards against already bit this project once: matching rows on
(date, amount, direction) and then reporting accuracy FOR date/amount/direction makes
those three 100% by construction. These assertions fail if that ever creeps back.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import score_lib as S

def blank(txns):
    return {"cards": [], "transactions": txns, "statementMeta": {},
            "statementLevelSummary": {}, "rewards": {}}

def main():
    # 1. ADMISSION must depend on description ALONE.
    pred = [{"description": "SWIGGY IN", "date": "01/01/2026", "amount": 100,
             "direction": "DEBIT", "currency": "INR"}]
    ref = [{"description": "SWIGGY IN", "date": "09/09/2099", "amount": 999,
            "direction": "CREDIT", "currency": "USD"}]
    pairs, _, _ = S.match_txns_by_description(pred, ref)
    assert len(pairs) == 1, "identical descriptions must pair regardless of other fields"
    sc = S.score_statement(blank(pred), blank(ref), "t1")
    for f in ("date", "amount", "direction", "currency"):
        v = sc["fields"][f"transactions[].{f}"][0]["verdict"]
        assert v == "wrong_value", f"{f} scored {v}: matching made it correct by construction"

    # 2. Order-insensitive.
    a = [{"description": d} for d in ("AAA", "BBB", "CCC")]
    b = list(reversed(a))
    p, _, _ = S.match_txns_by_description(a, b)
    got = sorted((a[x["pi"]]["description"], b[x["rj"]]["description"]) for x in p)
    assert got == [("AAA", "AAA"), ("BBB", "BBB"), ("CCC", "CCC")], got

    # 3. Strict 1:1 even when one narration repeats verbatim.
    a = [{"description": "UPI NUEGO"} for _ in range(3)]
    p, _, _ = S.match_txns_by_description(a, list(a))
    assert len(p) == 3 and len({x["pi"] for x in p}) == 3 and len({x["rj"] for x in p}) == 3

    # 4. A shared (date, amount) must NOT force a match when descriptions differ.
    p, _, _ = S.match_txns_by_description(
        [{"description": "AMAZON RETAIL", "date": "01/01/2026", "amount": 500}],
        [{"description": "ZOMATO LIMITED", "date": "01/01/2026", "amount": 500}])
    assert len(p) == 0, "dissimilar descriptions must not match on date/amount agreement"

    print("all 4 non-circularity proof obligations hold")

if __name__ == "__main__":
    main()
