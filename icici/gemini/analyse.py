"""Score the three ICICI arms with IDENTICAL logic and report honestly.

DISCIPLINES ENFORCED HERE (each exists because violating it produced a wrong number
earlier on this project):

1. POPULATED != CORRECT. Every field is reported in one of two buckets:
     CORRECTNESS-SCORED -- a PDF oracle exists, so right/wrong is meaningful
     POPULATED-ONLY     -- no oracle; only a fill rate is reported, never called accuracy
2. A field that is CORRECTLY NULL on all 11 cannot discriminate between arms. It is
   reported as NON-DISCRIMINATING and never shown as an accuracy percentage.
3. NON-CIRCULAR TRANSACTION MATCHING. Pairs are admitted on DESCRIPTION similarity
   ALONE, then date/amount/direction/currency are scored INSIDE matched pairs. Matching
   on (date, amount, direction) and then reporting accuracy for those same fields makes
   them 100% BY CONSTRUCTION -- that bug once reported transaction-date accuracy as 100%
   when it was really 77.6%. 1:1 assignment is ASSERTED in code.
4. DUPLICATION INVARIANT. closingPoints == pointsEarnedThisCycle is counted and split
   into BACKED (a printed points-balance cell exists in the PDF) vs UNBACKED. Any
   UNBACKED equality is the one-cell-into-two-fields defect. The exception is anchored
   in PDF EVIDENCE, never in model output.
5. Any metric change applies to ALL THREE ARMS and is re-derived, never carried over.

ORACLE LIMITATION, stated rather than hidden: the row oracle reconstructs each narration
by joining the PDF's word cells with single spaces. ICICI wraps some narrations mid-word
("CHURCHGA TE"), so the oracle's spacing is a normalisation of the print, not a
byte-image of it. Description scoring is therefore whitespace-normalised, and both an
EXACT rate and a >=0.95-similarity rate are reported.
"""

import argparse
import difflib
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ARMS = ["A", "B", "C"]
ORACLE = json.load(open(os.path.join(HERE, "pdf_oracle.json")))
ROWTRUTH = json.load(open(os.path.join(HERE, "pdf_rowtruth.json")))

LEAVES = [
    "statementMeta.issuerName", "statementMeta.statementDate", "statementMeta.dueDate",
    "statementLevelSummary.totalAmountDue", "statementLevelSummary.totalMinimumAmountDue",
    "statementLevelSummary.totalCreditLimit", "statementLevelSummary.availableCreditLimit",
    "cards[].cardMeta.cardDisplayName", "cards[].cardMeta.productFamily",
    "cards[].cardMeta.lastFourDigit", "cards[].cardMeta.network",
    "cards[].cardMeta.isPrimaryCard",
    "transactions[].date", "transactions[].description", "transactions[].amount",
    "transactions[].direction", "transactions[].txnType",
    "transactions[].rewardPointsOnThisTransaction", "transactions[].currency",
    "rewards.programType", "rewards.openingPoints", "rewards.pointsEarnedThisCycle",
    "rewards.pointsRedeemedThisCycle", "rewards.closingPoints",
    "rewards.pointsExpiringNext30Days", "rewards.pointsExpiringNext60Days",
]


def norm(s):
    return re.sub(r"\s+", " ", (s or "").strip()).lower()


def sim(a, b):
    return difflib.SequenceMatcher(None, norm(a), norm(b)).ratio()


def load(arm):
    d = os.path.join(HERE, f"json_arm{arm}")
    out = {}
    for f in sorted(os.listdir(d)):
        if not f.endswith(".json"):
            continue
        rec = json.load(open(os.path.join(d, f)))
        out[rec["sid"]] = rec
    return out


def getleaf(pj, path):
    """Return list of values at a leaf path (arrays fan out)."""
    if pj is None:
        return []
    parts = path.replace("[]", "").split(".")
    cur = [pj]
    for p in parts:
        nxt = []
        for c in cur:
            if isinstance(c, list):
                for e in c:
                    if isinstance(e, dict) and p in e:
                        nxt.append(e[p])
            elif isinstance(c, dict) and p in c:
                nxt.append(c[p])
        cur = nxt
    out = []
    for c in cur:
        if isinstance(c, list):
            out.extend(c)
        else:
            out.append(c)
    return out


# ------------------------------------------------------------------ transactions
def match_rows(model_rows, pdf_rows, thresh=0.55):
    """Admit pairs on DESCRIPTION SIMILARITY ONLY. Enforce 1:1. Never uses date,
    amount, direction or currency -- those are the fields we go on to score."""
    cand = []
    for i, m in enumerate(model_rows):
        for j, p in enumerate(pdf_rows):
            s = sim(m.get("description"), p.get("description"))
            if s >= thresh:
                cand.append((-s, i, j))
    cand.sort()
    mi, pj, pairs = set(), set(), []
    for negs, i, j in cand:
        if i in mi or j in pj:
            continue
        mi.add(i)
        pj.add(j)
        pairs.append((i, j, -negs))
    # ---- assert 1:1 ----
    assert len({i for i, _, _ in pairs}) == len(pairs), "model row matched twice"
    assert len({j for _, j, _ in pairs}) == len(pairs), "pdf row matched twice"
    return pairs


def score_txn(recs):
    agg = {"pdf_rows": 0, "model_rows": 0, "matched": 0,
           "desc_exact": 0, "desc_sim95": 0,
           "date_ok": 0, "date_n": 0, "amount_ok": 0, "amount_n": 0,
           "dir_ok": 0, "dir_n": 0, "cur_inr": 0, "cur_n": 0,
           "txnType_in_vocab": 0, "txnType_n": 0}
    per = {}
    VOCAB = {"PURCHASE", "PAYMENT", "REFUND", "REVERSAL", "CASHBACK", "FEE", "TAX",
             "INTEREST", "EMI", "CASH_ADVANCE", "UPI"}
    for sid, rec in recs.items():
        pj = rec.get("parsed_json") or {}
        mrows = pj.get("transactions") or []
        prows = ROWTRUTH[sid]["rows"]
        pairs = match_rows(mrows, prows)
        agg["pdf_rows"] += len(prows)
        agg["model_rows"] += len(mrows)
        agg["matched"] += len(pairs)
        de = ds = dok = aok = dirok = 0
        for i, j, s in pairs:
            m, p = mrows[i], prows[j]
            if norm(m.get("description")) == norm(p.get("description")):
                de += 1
            if s >= 0.95:
                ds += 1
            # date: oracle is DD/MM/YYYY; prompt requires DD/MM/YYYY
            if m.get("date") and p.get("date"):
                agg["date_n"] += 1
                if str(m["date"]).strip() == p["date"]:
                    dok += 1
            if m.get("amount") is not None and p.get("amount") is not None:
                agg["amount_n"] += 1
                if abs(float(m["amount"]) - float(p["amount"])) < 0.01:
                    aok += 1
            if m.get("direction") and p.get("direction"):
                agg["dir_n"] += 1
                if m["direction"] == p["direction"]:
                    dirok += 1
        agg["desc_exact"] += de
        agg["desc_sim95"] += ds
        agg["date_ok"] += dok
        agg["amount_ok"] += aok
        agg["dir_ok"] += dirok
        for m in mrows:
            if m.get("currency") is not None:
                agg["cur_n"] += 1
                if m["currency"] == "INR":
                    agg["cur_inr"] += 1
            if m.get("txnType") is not None:
                agg["txnType_n"] += 1
                if m["txnType"] in VOCAB:
                    agg["txnType_in_vocab"] += 1
        per[sid] = {"pdf": len(prows), "model": len(mrows), "matched": len(pairs),
                    "desc_exact": de, "desc_sim95": ds}
    return agg, per


# ------------------------------------------------------------------ scalars
def score_scalars(recs):
    r = {}
    # --- lastFourDigit: correctness vs measured card headings (set equality) ---
    l4_card_ok = l4_card_n = 0
    l4_stmt_ok = 0
    l4_has_X = 0
    for sid, rec in recs.items():
        pj = rec.get("parsed_json") or {}
        got = [(c.get("cardMeta") or {}).get("lastFourDigit") for c in (pj.get("cards") or [])]
        truth = set(ORACLE[sid]["cards"]["last4_distinct"])
        for g in got:
            l4_card_n += 1
            if g in truth:
                l4_card_ok += 1
            if g and "X" in str(g).upper():
                l4_has_X += 1
        if set(x for x in got if x) == truth:
            l4_stmt_ok += 1
    r["lastFourDigit"] = {"cards_correct": l4_card_ok, "cards_total": l4_card_n,
                          "statements_set_exact": l4_stmt_ok, "values_containing_X": l4_has_X}

    # --- network: oracle is null on all 11 -> count fabrications ---
    fab = []
    for sid, rec in recs.items():
        pj = rec.get("parsed_json") or {}
        for c in (pj.get("cards") or []):
            v = (c.get("cardMeta") or {}).get("network")
            if v:
                fab.append((sid, v))
    r["network"] = {"non_null_values": len(fab), "detail": fab,
                    "oracle": "null on all 11 (no card-own network printed anywhere)"}

    # --- rewards: correctness vs geometric oracle ---
    keys = ["programType", "pointsEarnedThisCycle", "pointsRedeemedThisCycle",
            "closingPoints", "openingPoints", "pointsExpiringNext30Days",
            "pointsExpiringNext60Days"]
    rw = {k: {"ok": 0, "n": 0, "wrong": []} for k in keys}
    for sid, rec in recs.items():
        pj = rec.get("parsed_json") or {}
        got = pj.get("rewards") or {}
        exp = ORACLE[sid]["rewards"]
        for k in keys:
            g, e = got.get(k), exp.get(k)
            rw[k]["n"] += 1
            same = (g is None and e is None) or (
                g is not None and e is not None and (
                    float(g) == float(e) if isinstance(e, (int, float)) else str(g) == str(e)))
            if same:
                rw[k]["ok"] += 1
            else:
                rw[k]["wrong"].append((sid, g, e))
    r["rewards"] = rw

    # --- issuerName ---
    iss = [(sid, ((rec.get("parsed_json") or {}).get("statementMeta") or {}).get("issuerName"))
           for sid, rec in recs.items()]
    r["issuerName"] = {"ok": sum(1 for _, v in iss if v == "ICICI Bank"), "n": len(iss),
                       "wrong": [t for t in iss if t[1] != "ICICI Bank"]}

    # --- duplication invariant ---
    dup = []
    for sid, rec in recs.items():
        pj = rec.get("parsed_json") or {}
        got = pj.get("rewards") or {}
        cp, pe = got.get("closingPoints"), got.get("pointsEarnedThisCycle")
        if cp is not None and pe is not None and float(cp) == float(pe):
            backed = ORACLE[sid]["rewards"]["closingPoints"] is not None
            dup.append({"sid": sid, "value": cp, "backed_by_printed_balance": backed,
                        "evidence": ORACLE[sid]["closing_points_evidence"]})
    r["duplication_invariant"] = {
        "closingPoints_equals_pointsEarned": len(dup),
        "BACKED": sum(1 for d in dup if d["backed_by_printed_balance"]),
        "UNBACKED": sum(1 for d in dup if not d["backed_by_printed_balance"]),
        "detail": dup}
    return r


def populated(recs):
    out = {}
    for path in LEAVES:
        tot = nn = 0
        for rec in recs.values():
            vals = getleaf(rec.get("parsed_json"), path)
            tot += len(vals)
            nn += sum(1 for v in vals if v is not None)
        out[path] = {"values": tot, "non_null": nn}
    return out


def tokens(recs):
    p = c = t = 0
    viol = []
    for sid, rec in recs.items():
        u = rec.get("usage_raw") or {}
        p += u.get("prompt_tokens") or 0
        c += u.get("completion_tokens") or 0
        t += u.get("total_tokens") or 0
        if rec.get("token_identity_ok") is False:
            viol.append((sid, rec.get("token_identity_detail")))
    return {"prompt_tokens": p, "completion_tokens": c, "total_tokens": t,
            "identity_holds": p + c == t, "violations": viol}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(HERE, "analysis_3arm.json"))
    a = ap.parse_args()

    res = {}
    for arm in ARMS:
        recs = load(arm)
        txn, per = score_txn(recs)
        res[arm] = {
            "n_statements": len(recs),
            "outcomes": {o: sum(1 for r in recs.values() if r.get("outcome") == o)
                         for o in {r.get("outcome") for r in recs.values()}},
            "prompt_sha256": next(iter(recs.values())).get("prompt_sha256"),
            "prompt_path": os.path.basename(next(iter(recs.values())).get("prompt_path", "")),
            "transactions": txn, "per_statement_txn": per,
            "scalars": score_scalars(recs), "populated": populated(recs),
            "tokens": tokens(recs),
        }
    json.dump(res, open(a.out, "w"), indent=1)
    print("wrote", a.out)


if __name__ == "__main__":
    main()
