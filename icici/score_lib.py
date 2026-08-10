"""Scoring for the ICICI corpus against TWO references, with a NON-CIRCULAR txn matcher.

Normalisers (text/num/date_norm/direction/lenient_hit) are imported from the project's
canonical scorer so ICICI is judged by exactly the same yardstick as the Axis arms.

THE TWO REFERENCES ARE NOT EQUIVALENT AND ARE NEVER CONFLATED:
  * `csv` = the incumbent gemini-3-flash parser's own output (detectionSource=GEMINI,
            modelName in {gemini-3-flash-preview, databricks-gemini-3-flash}).
            A difference here is DISAGREEMENT WITH THE INCUMBENT, not an error.
  * `gt`  = the Opus-5 native-PDF extraction. This is the accuracy reference, with the
            documented caveat that it shares a prompt instrument with the challenger.

WHY A NEW MATCHER (carried over from the Axis run, and the reason it exists):
the canonical `score.match_txs` pairs rows on the composite key
(date, amount, direction) and then reports accuracy FOR date, amount and direction.
Any row that matched did so *because* those three agreed, so all three come out ~100%
by construction. This scorer admits pairs on DESCRIPTION SIMILARITY ONLY with an
enforced 1:1, order-insensitive assignment, then scores date/amount/direction/currency
inside the matched pairs -- so those four numbers are earned, not tautological.
"""

import json
import os
import re
import sys
from decimal import Decimal
from difflib import SequenceMatcher

sys.path.insert(0, "/Users/mayanck.bihani/Savesage/bakeoff/scorer")
import score as sc  # noqa: E402  canonical normalisers

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import icici_lib as L  # noqa: E402

text, num, direction, lenient_hit = (sc.text, sc.num, sc.direction, sc.lenient_hit)


# The canonical sc.date_norm tries only ["%d/%m/%Y","%d-%m-%Y","%Y-%m-%d","%d/%m/%y",
# "%d %b %Y","%d-%b-%Y"] and RETURNS THE RAW STRING on no match. The incumbent emits
# long-form English dates for its TOP-LEVEL date columns ("October 18, 2022",
# "May 2, 2026"), which match none of those -- so 22 statement-level dates that are the
# SAME DAY as the GT scored as wrong_value. That is a scorer defect, not an incumbent
# error, so the format list is widened here. Verified: it changes only the FORMAT verdict,
# never the day -- a genuinely different day still scores wrong.
_EXTRA_DATE_FMTS = ["%B %d, %Y", "%b %d, %Y", "%B %d %Y", "%b %d %Y",
                    "%d %B %Y", "%Y/%m/%d", "%d.%m.%Y"]


def date_norm(x):
    base = sc.date_norm(x)
    if base is None:
        return None
    # sc.date_norm succeeded iff it produced canonical DD/MM/YYYY different from the input,
    # or the input already was canonical. Otherwise it echoed the raw string -> try harder.
    import datetime as _dt
    import re as _re
    if _re.fullmatch(r"\d{2}/\d{2}/\d{4}", str(base)):
        return base
    s = str(x).strip()
    for f in _EXTRA_DATE_FMTS:
        try:
            return _dt.datetime.strptime(s, f).strftime("%d/%m/%Y")
        except ValueError:
            pass
    return base

# ------------------------------------------------------------------ the 16 client-priority fields
PRIORITY = [
    "cards[].cardMeta.cardDisplayName", "cards[].cardMeta.lastFourDigit",
    "cards[].cardMeta.network",
    "statementLevelSummary.totalAmountDue", "statementLevelSummary.availableCreditLimit",
    "statementLevelSummary.utilisationPercent", "statementLevelSummary.totalCreditLimit",
    "statementLevelSummary.totalMinimumAmountDue",
    "statementMeta.issuerName", "statementMeta.statementDate", "statementMeta.dueDate",
    "transactions[].date", "transactions[].description", "transactions[].amount",
    "transactions[].direction", "transactions[].currency",
]
SECONDARY = [
    "statementMeta.statementPeriodStart", "statementMeta.statementPeriodEnd",
    "statementMeta.rawStatementId",
    "cards[].cardMeta.productFamily", "cards[].cardMeta.isPrimaryCard",
    "cards[].bigPicture.cardCreditLimit", "cards[].bigPicture.cardAvailableCreditLimit",
    "rewards.programType", "rewards.openingPoints", "rewards.pointsEarnedThisCycle",
    "rewards.pointsRedeemedThisCycle", "rewards.closingPoints",
    "rewards.pointsExpiringNext30Days", "rewards.pointsExpiringNext60Days",
    "rewards.bonusPointsThisCycle",
    "transactions[].txnType", "transactions[].rewardPointsOnThisTransaction",
]
TXN_FIELDS = ["date", "description", "amount", "direction", "currency",
              "txnType", "rewardPointsOnThisTransaction"]

# An ISO-vs-DD/MM/YYYY difference is a FORMAT difference, not a wrong day. The
# incumbent CSV emits ISO ("2026-02-20"); the models emit DD/MM/YYYY. Omitting
# transactions[].date here scored hundreds of identical dates as wrong_value on Axis.
DATEF = {"statementMeta.statementDate", "statementMeta.dueDate",
         "statementMeta.statementPeriodStart", "statementMeta.statementPeriodEnd",
         "transactions[].date"}
NUMF = ({f for f in PRIORITY + SECONDARY
         if f.startswith("statementLevelSummary.") or f.startswith("rewards.")
         or f.startswith("cards[].bigPicture.")} - {"rewards.programType"}) | {
    "transactions[].amount", "transactions[].rewardPointsOnThisTransaction"}
# Judgement-laden derived labels. cardDisplayName is unstable run-to-run even inside
# the GT (measured on Axis), so it is scored leniently and that is reported.
LENIENT = {"cards[].cardMeta.cardDisplayName", "cards[].cardMeta.productFamily"}


# ------------------------------------------------------------------ loading
def load_arm(outdir):
    """statement_id -> the persisted record (full, including usage_raw and outcome)."""
    out = {}
    d = os.path.join(outdir, "json")
    if not os.path.isdir(d):
        return out
    for f in sorted(os.listdir(d)):
        if f.endswith(".json"):
            out[f[:-5]] = json.loads(open(os.path.join(d, f)).read())
    return out


def csv_as_extraction(entry):
    """Project one incumbent CSV row into the GT_SCHEMA shape.

    The nested `data` blob is authoritative for every scored field: it carries
    statementMeta, statementLevelSummary, cards[] and transactions[] under exactly the
    contract's key names. The top-level columns are a lossy projection of it
    (comma-grouped strings, integer-truncated numbers, a different date format) and are
    NOT used for scored values -- only retained for provenance.
    """
    b = entry.get("blob") or {}
    sm = b.get("statementMeta") or {}
    sls = b.get("statementLevelSummary") or {}
    return {
        "statementMeta": {
            "issuerName": sm.get("issuerName"),
            "statementDate": sm.get("statementDate"),
            "dueDate": sm.get("dueDate"),
            "statementPeriodStart": sm.get("statementPeriodStart"),
            "statementPeriodEnd": sm.get("statementPeriodEnd"),
            "rawStatementId": sm.get("rawStatementId"),
        },
        "statementLevelSummary": {
            "totalAmountDue": sls.get("totalAmountDue"),
            "totalMinimumAmountDue": sls.get("totalMinimumAmountDue"),
            "totalCreditLimit": sls.get("totalCreditLimit"),
            "availableCreditLimit": sls.get("availableCreditLimit"),
            # the incumbent DOES emit this key (155/304 non-null) -- unlike any model
            "utilisationPercent": sls.get("utilisationPercent"),
        },
        "cards": [{
            "cardMeta": {k: (c.get("cardMeta") or {}).get(k) for k in
                         ("cardDisplayName", "productFamily", "lastFourDigit",
                          "network", "isPrimaryCard")},
            "bigPicture": {k: (c.get("bigPicture") or {}).get(k) for k in
                           ("cardCreditLimit", "cardAvailableCreditLimit")},
        } for c in (b.get("cards") or [])],
        "transactions": [{k: t.get(k) for k in TXN_FIELDS}
                         for t in (b.get("transactions") or [])],
        "rewards": {k: (b.get("rewards") or {}).get(k) for k in
                    ("programType", "openingPoints", "pointsEarnedThisCycle",
                     "pointsRedeemedThisCycle", "closingPoints",
                     "pointsExpiringNext30Days", "pointsExpiringNext60Days",
                     "bonusPointsThisCycle")},
    }


def model_as_extraction(rec):
    """The parsed model JSON, or None if the call did not produce usable JSON."""
    p = rec.get("parsed_json")
    if not isinstance(p, dict):
        return None
    if not isinstance(p.get("transactions"), list):
        return None
    return p


# ------------------------------------------------------------------ compare
def norm4(v):
    """lastFourDigit: keep only real trailing digits. The incumbent leaks the mask
    ('XXXX9003', '******2288'); a mask character is not a digit."""
    if v is None:
        return None
    s = re.sub(r"[^0-9]", "", str(v))
    return s[-4:] if s else None


def cmp_scalar(field, a, g):
    """-> (verdict, kind). verdict in correct / wrong_value / null_when_populated /
    hallucinated_when_null / both_null. `kind` flags a pure-format or lenient match.

    hallucinated_when_null is deliberately its own verdict and is NEVER folded into
    wrong_value: fabricating a card `network` is a different and worse defect than
    returning null for it."""
    a_null = a is None or (isinstance(a, str) and not a.strip())
    g_null = g is None or (isinstance(g, str) and not g.strip())
    if a_null and g_null:
        return "both_null", None
    if a_null:
        return "null_when_populated", None
    if g_null:
        return "hallucinated_when_null", None

    if field == "cards[].cardMeta.lastFourDigit":
        if norm4(a) == norm4(g):
            return "correct", (None if str(a) == str(g) else "FORMAT")
        return "wrong_value", None
    if field in NUMF:
        na, ng = num(a), num(g)
        if na is not None and ng is not None and abs(na - ng) <= Decimal("0.01"):
            return "correct", None
        return "wrong_value", None
    if field in DATEF:
        da, dg = date_norm(a), date_norm(g)
        if da == dg:
            return "correct", (None if str(a) == str(g) else "FORMAT")
        return "wrong_value", None
    if field in LENIENT:
        if text(a) == text(g):
            return "correct", None
        if lenient_hit(a, g):
            return "correct", "LENIENT"
        return "wrong_value", None
    if field == "cards[].cardMeta.isPrimaryCard":
        return ("correct", None) if bool(a) == bool(g) else ("wrong_value", None)
    if text(a) == text(g):
        return "correct", (None if str(a) == str(g) else "FORMAT")
    return "wrong_value", None


def align_cards(pred_cards, ref_cards):
    """Pair cards on real last-4 digits, falling back to index for the remainder.
    Card-count mismatch is returned, never silently absorbed."""
    pc, rc = list(pred_cards or []), list(ref_cards or [])
    pairs, used = [], set()
    for r in rc:
        r4 = norm4(((r or {}).get("cardMeta") or {}).get("lastFourDigit"))
        hit = None
        if r4:
            for i, p in enumerate(pc):
                if i in used:
                    continue
                if norm4(((p or {}).get("cardMeta") or {}).get("lastFourDigit")) == r4:
                    hit = i
                    break
        if hit is None:
            for i in range(len(pc)):
                if i not in used:
                    hit = i
                    break
        if hit is not None:
            used.add(hit)
            pairs.append((pc[hit], r))
        else:
            pairs.append((None, r))
    for i, p in enumerate(pc):
        if i not in used:
            pairs.append((p, None))
    return pairs, len(pc), len(rc)


def dig(obj, path):
    cur = obj
    for p in path.split("."):
        cur = cur.get(p) if isinstance(cur, dict) else None
    return cur


def util_derive(obj):
    """utilisationPercent AS-DERIVED from the source's OWN totalAmountDue and
    totalCreditLimit, under the contract's pinned formula. Each source is derived from
    its own values, so this measures the source's arithmetic self-consistency, not a
    borrowed figure."""
    tad = dig(obj, "statementLevelSummary.totalAmountDue")
    tcl = dig(obj, "statementLevelSummary.totalCreditLimit")
    for v in (tad, tcl):
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            return None
    if not tcl:
        return None
    return round(tad / tcl * 100, 2)


# ------------------------------- NON-CIRCULAR transaction matcher
def desc_sim(a, b):
    return SequenceMatcher(None, text(a) or "", text(b) or "").ratio()


def match_txns_by_description(pred, ref, threshold=0.60):
    """DESCRIPTION-ONLY, order-insensitive, provably 1:1.

    Every (pred_i, ref_j) pair is scored on description similarity alone; pairs are then
    consumed in globally-descending similarity order and taken only if BOTH sides are
    still free. That makes the assignment 1:1 by construction and independent of row
    order on either side.

    date / amount / direction / currency are DELIBERATELY excluded from admission --
    they are the fields being scored, and matching on them would make them perfect by
    construction.

    The date tie-break applies ONLY at EQUAL similarity and cannot manufacture a match
    (`s >= threshold` is the sole admission test). Without it, statements repeating one
    narration verbatim get an arbitrary pairing and the scorer charges the arbitrary
    permutation as date/amount defects -- a matcher artifact, not an extraction error.
    A wrong date still scores wrong; the tie-break only decides WHICH of several
    equally-similar rows to pair.
    """
    pred, ref = list(pred or []), list(ref or [])
    cand = []
    for i, p in enumerate(pred):
        for j, r in enumerate(ref):
            s = desc_sim(p.get("description"), r.get("description"))
            if s >= threshold:
                tb = 0 if (date_norm(p.get("date")) == date_norm(r.get("date"))) else 1
                cand.append((-s, tb, i, j))
    cand.sort()
    fp, fr, pairs = set(), set(), []
    for negs, _tb, i, j in cand:
        if i in fp or j in fr:
            continue
        fp.add(i)
        fr.add(j)
        pairs.append({"pi": i, "rj": j, "sim": -negs, "pred": pred[i], "ref": ref[j]})
    assert len({x["pi"] for x in pairs}) == len(pairs), "pred index reused"
    assert len({x["rj"] for x in pairs}) == len(pairs), "ref index reused"
    return (pairs,
            [pred[i] for i in range(len(pred)) if i not in fp],
            [ref[j] for j in range(len(ref)) if j not in fr])


# ------------------------------------------------------------------ per-statement scoring
VERDICTS = ("correct", "wrong_value", "null_when_populated",
            "hallucinated_when_null", "both_null")


def score_statement(pred, ref, sid):
    """One (prediction, reference) pair -> per-field verdicts + transaction metrics.

    Returns {"fields": {field: [ {verdict, kind, pred, ref} ]},
             "txn": {...}, "cards": {...}}
    """
    out = {"fields": {}, "statement_id": sid}
    if pred is None or ref is None:
        out["skipped"] = "missing_side"
        return out

    def rec(field, a, g, extra=None):
        v, k = cmp_scalar(field, a, g)
        row = {"verdict": v, "kind": k, "pred": a, "ref": g}
        if extra:
            row.update(extra)
        out["fields"].setdefault(field, []).append(row)

    # ---- flat scalars
    for f in PRIORITY + SECONDARY:
        if f.startswith("cards[]") or f.startswith("transactions[]"):
            continue
        if f == "statementLevelSummary.utilisationPercent":
            continue  # handled separately, as-extracted AND as-derived
        rec(f, dig(pred, f), dig(ref, f))

    # ---- utilisationPercent, both ways
    up = "statementLevelSummary.utilisationPercent"
    rec(up + "@extracted", dig(pred, up), dig(ref, up))
    rec(up + "@derived", util_derive(pred), util_derive(ref))

    # ---- cards
    pairs, npc, nrc = align_cards(pred.get("cards"), ref.get("cards"))
    out["cards"] = {"n_pred": npc, "n_ref": nrc, "count_match": npc == nrc}
    for p, r in pairs:
        for leaf in ("cardDisplayName", "productFamily", "lastFourDigit",
                     "network", "isPrimaryCard"):
            rec(f"cards[].cardMeta.{leaf}",
                ((p or {}).get("cardMeta") or {}).get(leaf),
                ((r or {}).get("cardMeta") or {}).get(leaf))
        for leaf in ("cardCreditLimit", "cardAvailableCreditLimit"):
            rec(f"cards[].bigPicture.{leaf}",
                ((p or {}).get("bigPicture") or {}).get(leaf),
                ((r or {}).get("bigPicture") or {}).get(leaf))

    # ---- transactions
    tp, tr = pred.get("transactions") or [], ref.get("transactions") or []
    mpairs, upred, uref = match_txns_by_description(tp, tr)
    tpn, fpn, fnn = len(mpairs), len(upred), len(uref)
    prec = tpn / len(tp) if tp else (1.0 if not tr else 0.0)
    reca = tpn / len(tr) if tr else (1.0 if not tp else 0.0)
    f1 = (2 * prec * reca / (prec + reca)) if (prec + reca) else 0.0
    sims = [m["sim"] for m in mpairs]
    exact = sum(1 for m in mpairs
                if str(m["pred"].get("description") or "") == str(m["ref"].get("description") or ""))
    ci = sum(1 for m in mpairs
             if text(m["pred"].get("description")) == text(m["ref"].get("description")))
    out["txn"] = {
        "n_pred": len(tp), "n_ref": len(tr), "matched": tpn,
        "unmatched_pred": fpn, "unmatched_ref": fnn,
        "precision": prec, "recall": reca, "f1": f1,
        "mean_desc_sim": (sum(sims) / len(sims)) if sims else None,
        "desc_exact_char_for_char": exact,
        "desc_exact_casefold": ci,
    }
    for m in mpairs:
        for leaf in TXN_FIELDS:
            rec(f"transactions[].{leaf}", m["pred"].get(leaf), m["ref"].get(leaf),
                {"sim": round(m["sim"], 3)})
    return out


def aggregate(per_statement):
    """field -> {n, correct, accuracy, wrong_value, null_when_populated,
    hallucinated_when_null, both_null}.

    `accuracy` = correct / (n - both_null): a both_null pair is agreement on absence and
    is reported, but scoring it as a hit would inflate every mostly-null field. The raw
    counts are all kept so any other denominator can be recomputed.
    """
    agg = {}
    for st in per_statement:
        for f, rows in (st.get("fields") or {}).items():
            a = agg.setdefault(f, {v: 0 for v in VERDICTS})
            a.setdefault("n", 0)
            a.setdefault("format_only", 0)
            a.setdefault("lenient_only", 0)
            for r in rows:
                a["n"] += 1
                a[r["verdict"]] += 1
                if r.get("kind") == "FORMAT":
                    a["format_only"] += 1
                if r.get("kind") == "LENIENT":
                    a["lenient_only"] += 1
    for f, a in agg.items():
        den = a["n"] - a["both_null"]
        a["scored_n"] = den
        a["accuracy"] = (a["correct"] / den) if den else None
    return agg
