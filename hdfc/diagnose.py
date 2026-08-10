#!/usr/bin/env python3
"""Phase 2 diagnosis: compare a Luna run against the incumbent CSV over the sample,
and pull PDF evidence for each disagreement so refinements are derived from MEASURED
defects rather than guessed.

Also runs the two prompt-audit checks the brief calls for:
  (a) VALIDATE each HDFC clause already in the client prompt against the real PDFs.
  (b) Quantify how much of the prompt is OTHER banks' dead weight.
"""
import json
import os
import re
import sys

import fitz

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hdfc_lib as H
import score_lib as S

HERE = os.path.dirname(os.path.abspath(__file__))


def pdf_text(path):
    d = fitz.open(path)
    try:
        return "\n".join(p.get_text() for p in d)
    finally:
        d.close()


def find_evidence(path, needle, limit=3):
    """-> [{page, rect, line}] for a literal-ish search. Coordinates make the
    adjudication checkable by hand rather than asserted."""
    if needle is None or str(needle).strip() == "":
        return []
    s = str(needle).strip()
    out = []
    d = fitz.open(path)
    try:
        for pno in range(d.page_count):
            pg = d[pno]
            for r in pg.search_for(s, quads=False) or []:
                out.append({"page": pno + 1,
                            "rect": [round(v, 1) for v in (r.x0, r.y0, r.x1, r.y1)],
                            "text": s})
                if len(out) >= limit:
                    return out
    except Exception:
        pass
    finally:
        d.close()
    return out


def num_in_pdf(path, val):
    """Does this numeric value appear in the PDF text in any usual Indian format?"""
    if val is None:
        return None
    txt = pdf_text(path)
    flat = re.sub(r"[,\s]", "", txt)
    cands = set()
    for v in ({val, round(val, 2)} if isinstance(val, float) else {val}):
        cands.add(f"{v:.2f}")
        if float(v).is_integer():
            cands.add(str(int(v)))
            cands.add(f"{int(v)}.00")
        cands.add(str(v))
    return any(c.replace(",", "") in flat for c in cands)


# ---------------------------------------------------------------- clause audit
# Each HDFC clause the client prompt already asserts, plus the marker strings that
# would have to be PRESENT in the PDFs for the clause to be doing any work.
HDFC_CLAUSES = {
    "pointsEarned <- 'Feature + Bonus Reward Points Earned'":
        [r"feature\s*\+?\s*and?\s*bonus\s+reward\s+points\s+earned",
         r"feature\s*\+\s*bonus"],
    "pointsEarned <- 'NeuCoins Earned'": [r"neucoins?\s+earned"],
    "pointsRedeemed <- 'Disbursed' (priority 1)": [r"disbursed"],
    "pointsRedeemed <- 'Cash Back Summary' (priority 2)": [r"cash\s*back\s+summary"],
    "closingPoints <- 'Reward Points'": [r"reward\s+points"],
    "cardholder-name row above first txn (must NOT be prepended)":
        [r"(?m)^\s*(?:MR|MRS|MS|DR)[\s.]"],
    "'+'/'Cr'/'C'/'CREDIT' amount marker => CREDIT": [r"\bCr\b", r"\+\s*\d"],
}

# Rules in the client prompt that name a bank OTHER than HDFC -> dead weight here.
OTHER_BANK_RULES = ["ICICI", "INDUSIND", "AU Bank", "Standard Chartered",
                    "IDFC FIRST", "SBI", "RBL"]


def clause_audit(matched):
    txts = {}
    for m in matched:
        txts[m["sid"]] = pdf_text(m["path"])
    res = {}
    for label, pats in HDFC_CLAUSES.items():
        hits = 0
        examples = []
        for sid, t in txts.items():
            if any(re.search(p, t, re.I) for p in pats):
                hits += 1
                if len(examples) < 3:
                    examples.append(sid)
        res[label] = {"pdfs_with_marker": hits, "of": len(txts), "examples": examples}
    return res


def other_bank_weight():
    p = H.baseline_prompt()
    lines = p.split("\n")
    dead, dead_bytes = [], 0
    for ln in lines:
        if any(b.lower() in ln.lower() for b in OTHER_BANK_RULES):
            dead.append(ln.strip())
            dead_bytes += len(ln.encode()) + 1
    return {"prompt_bytes": len(p.encode()), "lines": len(lines),
            "other_bank_lines": len(dead), "other_bank_bytes": dead_bytes,
            "pct_bytes": round(100 * dead_bytes / len(p.encode()), 2),
            "lines_verbatim": dead}


# ---------------------------------------------------------------- main

def main():
    run_dir = sys.argv[1] if len(sys.argv) > 1 else "phase1_baseline"
    matched, _, _ = H.build_join()
    prof = json.load(open(os.path.join(HERE, "corpus_profile.json")))
    keep = {p["sid"] for p in prof["sample"]}
    sample = [m for m in matched if m["sid"] in keep]
    run = S.load_run(os.path.join(HERE, run_dir))

    report = {
        "run_dir": run_dir,
        "prompt_audit_hdfc_clauses": clause_audit(sample),
        "prompt_audit_other_bank_deadweight": other_bank_weight(),
        "statements": [],
    }

    for m in sample:
        r = run.get(m["sid"])
        if not r:
            continue
        luna = r.get("parsed_json") or {}
        csv_x = S.csv_extraction(m["csv_row"])
        ent = {"sid": m["sid"], "file": m["filename"], "outcome": r.get("outcome"),
               "luna_txn": len(luna.get("transactions") or []),
               "csv_txn": len(csv_x.get("transactions") or []),
               "stmt_diffs": [], "txn_diffs": []}

        # statement-level disagreements + PDF adjudication
        for name, scope, path, kind in S.STMT_FIELDS:
            lv = S.get_field(luna, scope, path)
            cv = S.get_field(csv_x, scope, path)
            if S.values_equal(kind, lv, cv):
                continue
            d = {"field": name, "luna": lv, "csv": cv, "kind": kind}
            if kind == "num":
                d["luna_in_pdf"] = num_in_pdf(m["path"], S.norm_num(lv))
                d["csv_in_pdf"] = num_in_pdf(m["path"], S.norm_num(cv))
            else:
                d["luna_evidence"] = find_evidence(m["path"], lv)
                d["csv_evidence"] = find_evidence(m["path"], cv)
            ent["stmt_diffs"].append(d)

        # transaction-level: description-only 1:1 matching, then field comparison
        pairs, un_l, un_c = S.match_transactions(luna.get("transactions"),
                                                 csv_x.get("transactions"))
        ent["pairs"] = len(pairs)
        ent["luna_only"] = len(un_l)
        ent["csv_only"] = len(un_c)
        ent["luna_only_desc"] = [
            (luna["transactions"][i] or {}).get("description") for i in un_l[:12]]
        ent["csv_only_desc"] = [
            (csv_x["transactions"][j] or {}).get("description") for j in un_c[:12]]
        for i, j, sim in pairs:
            lt = luna["transactions"][i] or {}
            ct = csv_x["transactions"][j] or {}
            for f in S.TXN_FIELDS:
                if not S.txn_field_equal(f, lt.get(f), ct.get(f)):
                    ent["txn_diffs"].append(
                        {"field": f, "sim": round(sim, 3),
                         "luna": lt.get(f), "csv": ct.get(f),
                         "luna_desc": lt.get("description"),
                         "csv_desc": ct.get("description")})
        report["statements"].append(ent)

    H.G.atomic_write_json(os.path.join(HERE, f"diagnosis_{run_dir}.json"), report)

    # ---- console summary
    print("=== HDFC clause audit (does the client's HDFC rule apply to this corpus?) ===")
    for k, v in report["prompt_audit_hdfc_clauses"].items():
        print(f"  {v['pdfs_with_marker']:2d}/{v['of']}  {k}")
    dw = report["prompt_audit_other_bank_deadweight"]
    print(f"\n=== other-bank dead weight: {dw['other_bank_lines']} lines, "
          f"{dw['other_bank_bytes']}B = {dw['pct_bytes']}% of {dw['prompt_bytes']}B ===")

    from collections import Counter
    fc = Counter()
    tc = Counter()
    print("\n=== per-statement ===")
    for e in report["statements"]:
        print(f"  {e['outcome']:4s} luna_txn={e['luna_txn']:4d} csv_txn={e['csv_txn']:4d} "
              f"pairs={e['pairs']:4d} L_only={e['luna_only']:3d} C_only={e['csv_only']:3d} "
              f"stmt_diffs={len(e['stmt_diffs']):2d} txn_diffs={len(e['txn_diffs']):3d}  "
              f"{e['sid'][:44]}")
        for d in e["stmt_diffs"]:
            fc[d["field"]] += 1
        for d in e["txn_diffs"]:
            tc[d["field"]] += 1
    print("\n=== statement-field disagreement counts (Luna vs CSV) ===")
    for k, v in fc.most_common():
        print(f"  {v:3d}  {k}")
    print("=== txn-field disagreement counts ===")
    for k, v in tc.most_common():
        print(f"  {v:4d}  {k}")


if __name__ == "__main__":
    main()
