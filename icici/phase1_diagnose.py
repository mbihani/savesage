#!/usr/bin/env python3
"""Phase 2 diagnosis: compare the 10 Phase-1 baseline Luna results against the
incumbent CSV, and against the PDF itself where they disagree.

This is a DEFECT-PATTERN finder, not a scoreboard. The CSV is the incumbent's output,
not truth, so every disagreement is checked against the PDF text (PyMuPDF) before any
rule is written from it. Output feeds PROMPT_CHANGELOG.md -- each proposed rule must
cite a statement id and an observed defect.
"""
import json
import os
import re
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import icici_lib as L
import score_lib as S

import fitz

ARM = os.environ.get("ICICI_P1_ARM") or os.path.join(L.HERE, "phase1_luna_client")
OUT = os.environ.get("ICICI_P1_OUT") or os.path.join(L.HERE, "phase1_findings_client.json")


def pdf_text(path):
    doc = fitz.open(path)
    t = "\n".join(p.get_text("text") for p in doc)
    doc.close()
    return t


def pdf_find(path, needles, window=90):
    """-> {needle: [(page, snippet)]} for evidence. Case-insensitive substring search."""
    doc = fitz.open(path)
    hits = {}
    for n in needles:
        if n is None or str(n).strip() == "":
            continue
        pat = re.escape(str(n))
        found = []
        for pno, page in enumerate(doc, 1):
            txt = page.get_text("text")
            for m in re.finditer(pat, txt, re.I):
                a = max(0, m.start() - window)
                found.append({"page": pno,
                              "snippet": re.sub(r"\s+", " ", txt[a:m.end() + window])})
                break
        hits[str(n)] = found
    doc.close()
    return hits


def main():
    recs = S.load_arm(ARM)
    by_csv, diag = L.load_csv_incumbent()
    corpus = {sid: (f, p) for sid, f, p in L.discover_pdfs()}
    sample = json.load(open(os.path.join(L.HERE, "phase1_sample.json")))

    print(f"phase1 records: {len(recs)}  outcomes: "
          f"{Counter(r.get('outcome') for r in recs.values())}")

    findings = {"n_statements": len(recs), "per_statement": [], "patterns": {}}
    pat = defaultdict(list)

    for sid, rec in sorted(recs.items()):
        fname, path = corpus[sid]
        pred = S.model_as_extraction(rec)
        ref = S.csv_as_extraction(by_csv[fname]) if fname in by_csv else None
        row = {"statement_id": sid, "pdf": fname, "outcome": rec.get("outcome"),
               "product": L._product_from_name(fname),
               "n_txn_luna": rec.get("n_transactions"),
               "n_txn_csv": len(((by_csv.get(fname) or {}).get("blob") or {}).get("transactions") or []),
               "n_cards_luna": rec.get("n_cards"),
               "n_cards_csv": len(((by_csv.get(fname) or {}).get("blob") or {}).get("cards") or []),
               "usage": rec.get("usage_raw"),
               "finish_reason": rec.get("finish_reason")}
        if pred is None or ref is None:
            row["skipped"] = "missing_side"
            findings["per_statement"].append(row)
            continue

        sc = S.score_statement(pred, ref, sid)
        row["txn"] = sc["txn"]
        row["cards"] = sc["cards"]

        # ---- issuerName: the Axis-Bank contamination + the co-brand displacement trap
        li = S.dig(pred, "statementMeta.issuerName")
        ci = S.dig(ref, "statementMeta.issuerName")
        row["issuerName"] = {"luna": li, "csv": ci}
        if li and "axis" in str(li).lower():
            pat["issuer_says_axis_LUNA"].append({"sid": sid, "luna": li, "csv": ci})
        if li and "icici" not in str(li).lower():
            pat["issuer_not_icici_LUNA"].append({"sid": sid, "luna": li, "csv": ci})
        if ci and "icici" not in str(ci).lower():
            pat["issuer_not_icici_CSV"].append({"sid": sid, "luna": li, "csv": ci})

        # ---- closingPoints: is it printed, or was it computed?
        for src, obj in (("luna", pred), ("csv", ref)):
            cp = S.dig(obj, "rewards.closingPoints")
            pe = S.dig(obj, "rewards.pointsEarnedThisCycle")
            pr = S.dig(obj, "rewards.pointsRedeemedThisCycle")
            op = S.dig(obj, "rewards.openingPoints")
            if cp is not None:
                looks_derived = []
                for label, expr in (("earned-redeemed", (pe or 0) - (pr or 0)),
                                    ("opening+earned-redeemed",
                                     (op or 0) + (pe or 0) - (pr or 0))):
                    try:
                        if abs(float(cp) - float(expr)) < 0.01:
                            looks_derived.append(label)
                    except Exception:
                        pass
                if looks_derived:
                    pat[f"closingPoints_matches_arithmetic_{src.upper()}"].append(
                        {"sid": sid, "closingPoints": cp, "openingPoints": op,
                         "earned": pe, "redeemed": pr, "matches": looks_derived})
        row["rewards"] = {"luna": pred.get("rewards"), "csv": ref.get("rewards")}

        # is a closing/total points figure even printed?
        txt = pdf_text(path)
        row["pdf_reward_labels"] = sorted(set(
            re.findall(r"(?i)(points? transferred|total points? earned|closing (?:balance|points)"
                       r"|opening (?:balance|points)|points? redeemed|reward points? balance"
                       r"|available (?:reward )?points?|points? expir\w*|net points)", txt)))
        row["pdf_has_utilis"] = bool(re.search(r"(?i)utilis|utiliz", txt))
        row["pdf_network_tokens"] = sorted(set(
            re.findall(r"(?i)\b(visa|mastercard|master card|rupay|amex|american express|diners)\b",
                       txt)))

        # ---- network hallucination check
        ln = S.dig(pred, "cards[].cardMeta.network") if False else None
        lnet = [((c or {}).get("cardMeta") or {}).get("network") for c in (pred.get("cards") or [])]
        cnet = [((c or {}).get("cardMeta") or {}).get("network") for c in (ref.get("cards") or [])]
        row["network"] = {"luna": lnet, "csv": cnet, "pdf_tokens": row["pdf_network_tokens"]}
        for nv in lnet:
            if nv and not any(str(nv).lower().replace(" ", "") in t.lower().replace(" ", "")
                              or t.lower().replace(" ", "") in str(nv).lower().replace(" ", "")
                              for t in row["pdf_network_tokens"]):
                pat["network_not_in_pdf_LUNA"].append(
                    {"sid": sid, "luna": nv, "pdf_tokens": row["pdf_network_tokens"]})

        # ---- per-field verdicts, disagreements only
        dis = {}
        for f, rows in sc["fields"].items():
            bad = [r for r in rows if r["verdict"] in
                   ("wrong_value", "null_when_populated", "hallucinated_when_null")]
            if bad:
                dis[f] = bad[:12]
                for r in bad:
                    if not f.startswith("transactions[]"):
                        pat[f"disagree::{f}::{r['verdict']}"].append(
                            {"sid": sid, "luna": r["pred"], "csv": r["ref"]})
        row["disagreements"] = dis
        findings["per_statement"].append(row)

    findings["patterns"] = {k: v for k, v in sorted(pat.items(),
                                                    key=lambda x: (-len(x[1]), x[0]))}
    findings["pattern_counts"] = {k: len(v) for k, v in findings["patterns"].items()}
    findings["csv_join_diag"] = {k: (v if not isinstance(v, list) else len(v))
                                 for k, v in diag.items()}
    findings["sample_rule"] = sample["selection_rule"]
    with open(OUT, "w") as fh:
        json.dump(findings, fh, indent=1, default=str)

    print("\n=== PATTERN COUNTS ===")
    for k, v in findings["pattern_counts"].items():
        print(f"{v:>4}  {k}")
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
