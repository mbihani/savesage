"""HONEST decomposition of the transaction roll-up: ROW ADMISSION vs VALUE CORRECTNESS.

THE DEFECT THIS MODULE EXISTS TO FIX
------------------------------------
`score_phase3.run_pair` emits one `txn` block whose `micro_precision`/`micro_recall`/
`micro_f1`/`macro_f1` are computed as

    micro_precision = rows_matched / rows_pred
    micro_recall    = rows_matched / rows_ref

`rows_matched` is the output of `score_lib.match_txns_by_description`, which admits a pair
on DESCRIPTION SIMILARITY ALONE (>= 0.60, strict 1:1, order-insensitive). date / amount /
direction / currency are deliberately excluded from admission. So that block answers exactly
one question -- "did this row find a description twin?" -- and answers NOTHING about whether
the paired values agree. On a corpus whose narrations transcribe cleanly (ICICI: mean
description similarity 0.9992) every row pairs, so all four numbers saturate at 1.0 BY
CONSTRUCTION. `test_matcher_noncircular.py` proves the MATCHER is non-circular; it says
nothing about how the roll-up LABELS the matcher's output. The circularity re-entered one
level up, where `build_final_scores` hoisted `micro_f1` into `headline_verdicts` under the
name `luna_transaction_micro_f1` -- an alignment number presented as a correctness verdict.

WHAT THIS MODULE DOES
---------------------
Splits the one number into the two questions it was conflating, and refuses to emit a bare
1.0 without saying why it is 1.0:

  (a) row_alignment    -- pairing only. Explicitly flagged `is_correctness_claim: false`.
  (b) value_correctness -- per-field verdicts over the MATCHED PAIRS, using the verdicts
      `score_lib.cmp_scalar` already produced (correct / wrong_value / null_when_populated /
      hallucinated_when_null / both_null), with `correct` further split into
      `correct_byte_identical` and `format_only`.

WHY `format_only` IS SPLIT OUT AND WHY IT IS **NOT** wrong_value
----------------------------------------------------------------
The two arms serialise the same value differently. Luna emits `180` where the Opus GT emits
`180.0`; a naive `str()` comparison charges 2,347 of 4,097 ICICI amounts as differences when
every one of them is the same number. `score_lib.cmp_scalar` already normalises these
(`num()` for NUMF, `date_norm()` for DATEF, `text()` otherwise) and correctly returns
`correct` -- but it only tags `kind="FORMAT"` for the date/text/last-four branches, never for
the numeric branch, so the format-only share of a numeric field was invisible. This module
recovers it as `format_only = correct AND str(pred) != str(ref)`, a superset of the scorer's
own `kind == "FORMAT"` (kept alongside as `format_only_scorer_kind`). Nothing is reclassified:
a `wrong_value` stays a `wrong_value`.

Everything here is derived from `scores_phase3.json` (which retains the raw `pred`/`ref` of
every scored row). No model inference, no re-extraction, stdlib only.
"""

import collections
import re

# The five client-priority transaction fields. txnType and rewardPointsOnThisTransaction are
# SECONDARY in score_lib and are reported but never rolled into a headline.
TXN_PRIORITY = ["date", "amount", "direction", "currency", "description"]
TXN_SECONDARY = ["txnType", "rewardPointsOnThisTransaction"]

VERDICTS = ("correct", "wrong_value", "null_when_populated",
            "hallucinated_when_null", "both_null")

_ALIGNMENT_DISCLAIMER = (
    "PAIRING-BASED ALIGNMENT ONLY -- NOT A CORRECTNESS CLAIM. Rows are admitted on "
    "description similarity alone (>=0.60, strict 1:1, order-insensitive); date, amount, "
    "direction and currency are excluded from admission by design. A 1.0 here means every "
    "row found a description twin. It says nothing about whether the paired values agree -- "
    "read value_correctness for that."
)


def _field_stats(rows):
    """One field's scored rows -> counts + accuracy + the format-only split.

    `rows` are the `{verdict, kind, pred, ref}` records score_lib.score_statement recorded.
    `accuracy = correct / (n - both_null)`, the denominator score_lib.aggregate already uses,
    so these numbers stay comparable with the `priority` block and with sbi/final_scores.json.
    """
    a = {v: 0 for v in VERDICTS}
    a["n"] = 0
    a["correct_byte_identical"] = 0
    a["format_only"] = 0
    a["format_only_scorer_kind"] = 0
    a["lenient_only"] = 0
    a["naive_str_mismatch"] = 0
    for r in rows:
        a["n"] += 1
        a[r["verdict"]] += 1
        same_raw = str(r.get("pred")) == str(r.get("ref"))
        if not same_raw:
            a["naive_str_mismatch"] += 1
        if r["verdict"] == "correct":
            if same_raw:
                a["correct_byte_identical"] += 1
            else:
                a["format_only"] += 1
        if r.get("kind") == "FORMAT":
            a["format_only_scorer_kind"] += 1
        if r.get("kind") == "LENIENT":
            a["lenient_only"] += 1
    den = a["n"] - a["both_null"]
    a["scored_n"] = den
    a["accuracy"] = (a["correct"] / den) if den else None
    # The same field judged with NO normalisation at all. Reported for transparency about how
    # much of `accuracy` rests on normalisation -- never used as the headline, because a
    # serialisation difference is not an extraction error.
    a["strict_serialisation_agreement"] = (
        (a["correct_byte_identical"] / den) if den else None)
    return a


def _ref_profile(rows):
    """Discriminating power of the REFERENCE side of this field, over the SCORED rows.

    A field whose reference takes one value everywhere is solved by emitting a constant, so a
    1.0 on it is a property of the corpus, not of the extractor. This is the same test
    field_entropy.py applies; it is recomputed here so every 1.0 can be classified in place.

    both_null pairs are excluded, matching the accuracy denominator: a field that is null on
    both sides 303 times out of 304 must not be able to look discriminating because `None` is
    one of its "values".
    """
    vals = [str(r.get("ref")) for r in rows if r["verdict"] != "both_null"]
    if not vals:
        return {"rows_profiled": 0, "distinct_reference_values": 0,
                "modal_reference_value": None, "modal_share": None}
    c = collections.Counter(vals)
    top, n_top = c.most_common(1)[0]
    return {"rows_profiled": len(vals),
            "distinct_reference_values": len(c),
            "modal_reference_value": top[:60],
            "modal_share": round(n_top / len(vals), 4)}


def classify_one_point_oh(stats, profile):
    """Why is this metric exactly 1.0? Every exactly-1.0 metric must carry one of these.

    Returns None when the metric is not exactly 1.0.
    """
    acc = stats["accuracy"]
    if acc is None:
        return "NOT_MEASURED__EVERY_PAIR_BOTH_NULL"
    if acc != 1.0:
        return None
    # A 1.0 over a handful of scored pairs is noise, not a result. Checked BEFORE the
    # discriminating-reference test, which is meaningless at this sample size.
    if stats["scored_n"] < 30 or stats["scored_n"] < 0.25 * stats["n"]:
        return ("EXACTLY_1_0__NEGLIGIBLE_SAMPLE: only "
                f"{stats['scored_n']} of {stats['n']} pairs were scorable "
                f"({stats['both_null']} both_null). Not a meaningful 1.0.")
    if profile["distinct_reference_values"] <= 1:
        return ("EXACTLY_1_0__BY_CONSTRUCTION: the reference takes a single value across the "
                "whole corpus, so this field is solved by emitting a constant. "
                "NON-DIFFERENTIATING -- do not read as extraction skill.")
    if stats["lenient_only"]:
        return ("EXACTLY_1_0__LENIENT: some pairs passed only under lenient (fuzzy) matching, "
                "not exact agreement.")
    if stats["format_only"]:
        return ("EXACTLY_1_0__EARNED_AFTER_NORMALISATION: reference is discriminating "
                f"({profile['distinct_reference_values']} distinct values) and zero pairs "
                f"disagree on value, but {stats['format_only']} of {stats['scored_n']} agree "
                "only after normalising serialisation (int-vs-float, date format). Earned, "
                "not by construction.")
    return ("EXACTLY_1_0__EARNED_BYTE_FOR_BYTE: reference is discriminating "
            f"({profile['distinct_reference_values']} distinct values) and every one of "
            f"{stats['scored_n']} pairs agrees on the raw value. Subject to the standing "
            "shared-prompt-instrument caveat on the GT (see notes.gt_instrument_caveat).")


def field_decomposition(per_statement, fields, prefix="transactions[]."):
    """{field: stats + reference_profile + exactly_1_0_annotation} over the matched pairs."""
    out = {}
    for leaf in fields:
        key = prefix + leaf
        rows = []
        for st in per_statement:
            rows.extend((st.get("fields") or {}).get(key) or [])
        if not rows:
            continue
        stats = _field_stats(rows)
        prof = _ref_profile(rows)
        stats["reference_profile"] = prof
        ann = classify_one_point_oh(stats, prof)
        if ann:
            stats["exactly_1_0_annotation"] = ann
        out[leaf] = stats
    return out


_SQ = lambda s: re.sub(r"\s+", "", str(s or "")).casefold()


def desc_defect_class(pred, ref):
    """Severity class of one description defect. MIRRORS desc_defect_classes.py exactly.

    Kept here so the joint row metric can offer a fidelity-tolerant variant without a second,
    divergent definition of "same narration"; test_rollup_honesty.py asserts the class totals
    computed here still match desc_defect_classes.json.
    """
    a, b = _SQ(pred), _SQ(ref)
    if a == b:
        return "spacing_only"
    if b == a + "in":
        return "dropped_trailing_country_code"
    if a == b + "in":
        return "added_trailing_country_code"
    return "real_character_difference"


_FIDELITY_ONLY = {"spacing_only", "dropped_trailing_country_code",
                  "added_trailing_country_code"}


def joint_row_correctness(per_statement, fields=TXN_PRIORITY):
    """Fraction of MATCHED PAIRS on which ALL of `fields` are simultaneously `correct`.

    score_statement appends one row per field per matched pair in a single pass over the
    pairs, so index i is the same pair in every field's list -- the zip below is row-aligned
    by construction of that loop, and the equal-length assertion enforces it.

    This is the honest single-number transaction headline: it cannot saturate because a row
    with a clean description but a wrong amount fails it.

    A second, WIDER variant is reported alongside it: `..._excluding_description_fidelity_only`
    forgives description defects that desc_defect_class calls pure text fidelity (intra-cell
    line-wrap spacing, a dropped trailing country code) while still charging every real
    character difference and every date/amount/direction/currency defect. Both are reported
    because the strict number and the fidelity-tolerant number tell materially different
    stories on this corpus, and choosing one silently would be the same species of error this
    module exists to fix.
    """
    total = clean = clean_fid = 0
    breakdown = collections.Counter()
    desc_classes = collections.Counter()
    for st in per_statement:
        cols = [((st.get("fields") or {}).get("transactions[]." + f) or []) for f in fields]
        if not cols or not cols[0]:
            continue
        n = len(cols[0])
        assert all(len(c) == n for c in cols), \
            f"transaction field lists out of alignment in {st.get('statement_id')}"
        for i in range(n):
            total += 1
            bad = [f for f, c in zip(fields, cols) if c[i]["verdict"] != "correct"]
            if not bad:
                clean += 1
                clean_fid += 1
                continue
            breakdown["+".join(bad)] += 1
            fatal = [f for f in bad if f != "description"]
            if "description" in bad:
                row = cols[fields.index("description")][i]
                klass = desc_defect_class(row.get("pred"), row.get("ref"))
                desc_classes[klass] += 1
                if klass not in _FIDELITY_ONLY:
                    fatal.append("description")
            if not fatal:
                clean_fid += 1
    return {
        "fields_required_correct": list(fields),
        "matched_pairs": total,
        "all_fields_correct": clean,
        "all_fields_correct_rate": (clean / total) if total else None,
        "rows_with_at_least_one_defect": total - clean,
        "defect_signature_counts": dict(breakdown.most_common(12)),
        "all_fields_correct_excluding_description_fidelity_only": clean_fid,
        "all_fields_correct_rate_excluding_description_fidelity_only": (
            (clean_fid / total) if total else None),
        "description_defect_classes": dict(desc_classes),
        "_what_this_measures": (
            "Row-level joint correctness over the description-matched pairs. This is the "
            "transaction headline to quote: unlike the pairing F1 it cannot be satisfied by "
            "a clean narration alone. The *_excluding_description_fidelity_only variant "
            "forgives spacing / dropped-trailing-'IN' narration slips only (per "
            "desc_defect_classes.py) and still charges every real character difference."
        ),
    }


def row_alignment(txn):
    """`score_phase3` txn block -> the same counts, renamed and labelled as ALIGNMENT.

    The saturating ratios are renamed micro_precision -> pairing_precision etc. so no
    downstream consumer can mistake them for correctness, and every one that lands on
    exactly 1.0 is listed in `exactly_1_0_metrics`.
    """
    if not txn:
        return None
    out = {
        "_what_this_measures": _ALIGNMENT_DISCLAIMER,
        "is_correctness_claim": False,
        "admission_rule": ("description similarity >= 0.60, strict 1:1, order-insensitive; "
                           "date/amount/direction/currency excluded from admission"),
        "statements": txn["statements"],
        "rows_pred": txn["rows_pred"],
        "rows_ref": txn["rows_ref"],
        "rows_matched": txn["rows_matched"],
        "rows_missing_vs_reference": txn["rows_ref"] - txn["rows_matched"],
        "pairing_precision": txn["micro_precision"],
        "pairing_recall": txn["micro_recall"],
        "pairing_f1": txn["micro_f1"],
        "pairing_macro_f1": txn["macro_f1"],
        "row_count_exact_match_statements": txn["row_count_exact_match_statements"],
        "mean_desc_sim": txn["mean_desc_sim"],
        "desc_exact_char_for_char": txn["desc_exact_char_for_char"],
        "desc_exact_casefold": txn["desc_exact_casefold"],
        "renamed_from": {"pairing_precision": "micro_precision",
                         "pairing_recall": "micro_recall",
                         "pairing_f1": "micro_f1",
                         "pairing_macro_f1": "macro_f1"},
    }
    sat = [k for k in ("pairing_precision", "pairing_recall", "pairing_f1", "pairing_macro_f1")
           if out[k] == 1.0]
    out["exactly_1_0_metrics"] = sat
    if sat:
        out["exactly_1_0_annotation"] = (
            "BY CONSTRUCTION on this corpus: "
            f"mean description similarity is {out['mean_desc_sim']}, so every row finds a "
            "description twin and the pairing ratios saturate. These are NOT correctness "
            "numbers and must never be quoted as transaction accuracy."
        )
    return out


def decompose(comparison):
    """One `scores_phase3.comparisons[*]` block -> the honest transaction metrics block."""
    per = comparison.get("per_statement") or []
    joint = joint_row_correctness(per)
    txn = comparison.get("txn") or {}
    # END-TO-END denominator: every reference row, not just the ones this arm managed to
    # produce. Over MATCHED PAIRS an arm is rewarded for the rows it dropped (they are simply
    # absent from the denominator), so the two arms are only comparable over rows_ref.
    nref = txn.get("rows_ref")
    if nref:
        joint["reference_rows"] = nref
        joint["all_fields_correct_rate_over_reference_rows"] = (
            joint["all_fields_correct"] / nref)
        joint["all_fields_correct_rate_over_reference_rows_excl_desc_fidelity"] = (
            joint["all_fields_correct_excluding_description_fidelity_only"] / nref)
        dropped = nref - txn.get("rows_matched", nref)
        joint["_denominator_warning"] = (
            "Compare arms on the *_over_reference_rows figures. The plain "
            "all_fields_correct_rate uses matched pairs only, which flatters an arm that "
            + (f"dropped rows: {dropped} reference rows never paired here and are excluded "
               "from it." if dropped else
               "dropped rows. This arm dropped none, so the two denominators coincide here -- "
               "they do NOT coincide for the incumbent arm.")
        )
    return {
        "_schema": ("SPLIT 2026-08-11 to repair a measurement defect: the previous single "
                    "block reported description-pairing ratios (micro_precision/recall/f1, "
                    "macro_f1) as transaction correctness. Alignment and correctness are now "
                    "separate and separately labelled."),
        "row_alignment": row_alignment(comparison.get("txn")),
        "value_correctness": {
            "_what_this_measures": (
                "Per-field verdicts over the MATCHED PAIRS ONLY, using score_lib.cmp_scalar's "
                "established normalisation and verdict vocabulary. `format_only` = agrees "
                "after normalisation but serialised differently; it is NOT wrong_value. "
                "accuracy = correct / (n - both_null), the same denominator as the priority "
                "block."
            ),
            "denominator_note": ("Matched pairs only. Rows that never paired are counted in "
                                 "row_alignment.rows_missing_vs_reference, not here."),
            "priority": field_decomposition(per, TXN_PRIORITY),
            "secondary": field_decomposition(per, TXN_SECONDARY),
        },
        "joint_row_correctness": joint,
    }
