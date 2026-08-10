#!/usr/bin/env python3
"""Deterministically pick the 10-statement Phase-1 tuning sample.

Requirement: structurally DIVERSE, not 10 near-identical statements. Diversity is
measured from the PDF and the incumbent CSV rather than guessed:

  * page_count                (6 / 7 / 8 / 9 in this corpus)
  * extracted char count      (proxy for content density)
  * incumbent txn count       (the transaction-density axis the brief flags)
  * n_cards in the incumbent blob (multi-card layouts)
  * whether the incumbent blob shows a foreign-currency / non-INR row
  * whether the statement has reward-point activity vs none

Selection is a fixed, seedless, fully-ordered rule set (strictly reproducible):
take the extreme of each axis in a fixed priority order, skipping already-taken
ids, then fill the remainder by even quantile stride over txn count.
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sbi_lib as L
import score_lib_sbi as S

ROOT = os.path.dirname(os.path.abspath(__file__))


def main():
    corpus = L.discover_pdfs()
    probe = {r["sid"]: r for r in json.load(open(os.path.join(ROOT, "corpus_probe.json")))}
    csvref, _meta = S.load_csv_incumbent()

    rows = []
    for sid, fname, path in corpus:
        if sid not in csvref:
            continue
        blob = csvref[sid]
        tx = blob.get("transactions") or []
        cards = blob.get("cards") or []
        rw = blob.get("rewards") or {}
        curr = {(t.get("currency") or "INR") for t in tx}
        rows.append({
            "sid": sid, "file": fname,
            "pages": probe[sid]["pages"], "chars": probe[sid]["chars"],
            "bytes": probe[sid]["bytes"],
            "n_txn": len(tx), "n_cards": len(cards),
            "non_inr": sorted(curr - {"INR"}),
            "has_rewards": any(rw.get(k) for k in
                               ("closingPoints", "pointsEarnedThisCycle", "openingPoints")),
            "product": ((cards[0].get("cardMeta") or {}).get("productFamily")
                        if cards else None),
            "display": ((cards[0].get("cardMeta") or {}).get("cardDisplayName")
                        if cards else None),
        })
    rows.sort(key=lambda r: r["sid"])

    picked, why = [], {}

    def take(r, reason):
        if r and r["sid"] not in picked:
            picked.append(r["sid"])
            why[r["sid"]] = reason

    def first(pred, key, reverse=True):
        c = [r for r in rows if pred(r) and r["sid"] not in picked]
        if not c:
            return None
        return sorted(c, key=lambda r: (key(r), r["sid"]), reverse=reverse)[0]

    # fixed priority order of diversity axes
    take(first(lambda r: True, lambda r: r["n_txn"]), "max incumbent txn count (density extreme)")
    take(first(lambda r: True, lambda r: r["chars"]), "max extracted chars (content extreme)")
    take(first(lambda r: r["pages"] == 9, lambda r: r["n_txn"]), "9-page layout (rarest, n=1)")
    take(first(lambda r: r["pages"] == 6, lambda r: r["n_txn"]), "6-page layout (short variant)")
    take(first(lambda r: r["n_cards"] > 1, lambda r: r["n_cards"]), "multi-card statement")
    take(first(lambda r: r["non_inr"], lambda r: r["n_txn"]), "non-INR transaction row present")
    take(first(lambda r: not r["has_rewards"], lambda r: r["n_txn"], reverse=False),
         "no reward-point activity (closingPoints trap control)")
    take(first(lambda r: True, lambda r: r["n_txn"], reverse=False),
         "min incumbent txn count (sparse extreme)")
    # distinct card products not yet represented, largest first
    seen_prod = {next(r["product"] for r in rows if r["sid"] == s) for s in picked}
    for r in sorted(rows, key=lambda r: (-r["n_txn"], r["sid"])):
        if len(picked) >= 10:
            break
        if r["product"] not in seen_prod:
            take(r, f"unrepresented card product: {r['product']}")
            seen_prod.add(r["product"])
    # fill by even stride over txn count
    if len(picked) < 10:
        pool = sorted([r for r in rows if r["sid"] not in picked],
                      key=lambda r: (r["n_txn"], r["sid"]))
        need = 10 - len(picked)
        for k in range(need):
            idx = int(round((k + 1) * len(pool) / (need + 1))) - 1
            idx = max(0, min(len(pool) - 1, idx))
            while pool[idx]["sid"] in picked:
                idx = (idx + 1) % len(pool)
            take(pool[idx], f"quantile fill over txn count (stride {k + 1}/{need})")

    assert len(picked) == 10, picked
    sample = [dict(next(r for r in rows if r["sid"] == s), reason=why[s]) for s in picked]
    out = {"n_corpus": len(rows), "sample_ids": picked, "sample": sample,
           "rule": "fixed deterministic diversity-extremes rule set, no RNG",
           "corpus_txn_stats": {
               "min": min(r["n_txn"] for r in rows),
               "max": max(r["n_txn"] for r in rows),
               "mean": round(sum(r["n_txn"] for r in rows) / len(rows), 2),
               "median": sorted(r["n_txn"] for r in rows)[len(rows) // 2],
           }}
    with open(os.path.join(ROOT, "phase1_sample.json"), "w") as f:
        json.dump(out, f, indent=1)
    print(json.dumps(out["corpus_txn_stats"], indent=1))
    for s in sample:
        print(f"{s['sid']:>12} pg={s['pages']} ch={s['chars']:>6} txn={s['n_txn']:>3} "
              f"cards={s['n_cards']} nonINR={s['non_inr']} prod={s['product']!r} :: {s['reason']}")


if __name__ == "__main__":
    main()
