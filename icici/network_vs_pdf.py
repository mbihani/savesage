#!/usr/bin/env python3
"""Score `cards[].cardMeta.network` against the PDF, for all three sources.

Necessary because NO reference is trustworthy on this field: the Opus-5 GT emits a
non-null network 3 times and all 3 are unsupported outside the four-network
fuel-surcharge disclaimer. So the PDF is the only admissible reference here.

Ground rule, applied identically to Luna / incumbent / GT: a non-null network is
SUPPORTED only if the network token occurs somewhere OUTSIDE the sentence
"For RuPay/American Express/ Visa/Mastercard Credit Cards: Fuel surcharge ...",
which names all four networks and therefore identifies no card.
"""
import json
import os
import re
import sys
from collections import Counter

import fitz

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import icici_lib as L
import score_lib as S

HERE = L.HERE
DISC = re.compile(r"For\s+RuPay\s*/\s*American\s+Express\s*/?\s*Visa\s*/\s*Mastercard", re.I)


def supported(pages, token):
    for t in pages:
        for m in re.finditer(re.escape(str(token)), t, re.I):
            a = max(0, m.start() - 160)
            if not DISC.search(t[a:m.end() + 160]):
                return True
    return False


def main():
    corpus = {sid: p for sid, _, p in L.discover_pdfs()}
    names = {sid: f for sid, f, _ in L.discover_pdfs()}
    by_csv, _ = L.load_csv_incumbent()
    arms = {
        "luna_refined": S.load_arm(os.path.join(HERE, "luna_refined")),
        "opus_gt": S.load_arm(os.path.join(HERE, "opus_gt")),
    }

    res = {k: Counter() for k in ("luna_refined", "opus_gt", "incumbent_csv")}
    items = []
    sids = sorted(set(arms["luna_refined"]) & set(arms["opus_gt"]),
                  key=lambda s: (len(s), s))
    for sid in sids:
        doc = fitz.open(corpus[sid])
        pages = [p.get_text("text") for p in doc]
        doc.close()
        srcs = {}
        for k in ("luna_refined", "opus_gt"):
            p = S.model_as_extraction(arms[k].get(sid) or {})
            srcs[k] = [(c.get("cardMeta") or {}).get("network") for c in (p or {}).get("cards") or []]
        e = by_csv.get(names[sid])
        cc = S.csv_as_extraction(e) if e else None
        srcs["incumbent_csv"] = [(c.get("cardMeta") or {}).get("network")
                                 for c in (cc or {}).get("cards") or []]
        for k, nets in srcs.items():
            for n in nets:
                if n is None or str(n).strip() == "":
                    res[k]["null (correct on this corpus)"] += 1
                elif supported(pages, n):
                    res[k]["non-null SUPPORTED by the PDF"] += 1
                else:
                    res[k]["non-null UNSUPPORTED (fabrication)"] += 1
                    items.append({"statement_id": sid, "source": k, "network": n,
                                  "reason": "token appears only inside the four-network "
                                            "fuel-surcharge disclaimer"})
        doc = None
    out = {"rule": __doc__.strip(), "n_statements": len(sids),
           "per_source": {k: dict(v) for k, v in res.items()},
           "fabrications": items}
    dest = os.path.join(HERE, "network_vs_pdf.json")
    json.dump(out, open(dest, "w"), indent=1)
    print(f"wrote {dest}\nstatements={len(sids)}")
    for k, v in out["per_source"].items():
        tot = sum(v.values())
        fab = v.get("non-null UNSUPPORTED (fabrication)", 0)
        print(f"  {k:<16} cards={tot:>4}  fabricated={fab:>3}  "
              f"fabrication_rate={fab/tot*100 if tot else 0:.2f}%  {v}")


if __name__ == "__main__":
    main()
