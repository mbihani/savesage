"""Split transaction ROW ALIGNMENT from VALUE CORRECTNESS.

Port of ICICI's audited solution.  SBI's old ``txn`` block computes precision and recall as
matched/predicted and matched/reference, but a pair is admitted on description similarity
alone.  Those ratios answer whether a row found a narration twin, not whether its values are
correct.  This module derives the honest split from the already-persisted scorer cells.
"""
from collections import Counter, defaultdict
import re

TXN_PRIORITY = ["date", "amount", "direction", "currency", "description"]
TXN_SECONDARY = ["txnType", "rewardPointsOnThisTransaction"]
VERDICTS = ("correct", "wrong_value", "null_when_populated",
            "hallucinated_when_null", "both_null")

ALIGNMENT_DISCLAIMER = (
    "PAIRING-BASED ALIGNMENT ONLY -- NOT A CORRECTNESS CLAIM. Rows are admitted on "
    "description similarity alone (>=0.60, strict 1:1, order-insensitive); date, amount, "
    "direction and currency are excluded from admission. A 1.0 means every row found a "
    "description twin, not that the paired values agree. Read value_correctness instead."
)


def per_statement_from_cells(cells, comparison):
    """Convert SBI's flat persisted cell list to ICICI's per-statement field shape."""
    out = defaultdict(lambda: {"fields": defaultdict(list)})
    for row in cells:
        if row.get("ref") != comparison:
            continue
        r = dict(row)
        r["ref"] = r.pop("refv", None)
        out[row["statement_id"]]["statement_id"] = row["statement_id"]
        out[row["statement_id"]]["fields"][row["field"]].append(r)
    return list(out.values())


def _field_stats(rows):
    a = {v: 0 for v in VERDICTS}
    a.update(n=0, correct_byte_identical=0, format_only=0,
             format_only_scorer_kind=0, lenient_only=0, naive_str_mismatch=0)
    for r in rows:
        a["n"] += 1
        a[r["verdict"]] += 1
        same_raw = str(r.get("pred")) == str(r.get("ref"))
        if not same_raw:
            a["naive_str_mismatch"] += 1
        if r["verdict"] == "correct":
            a["correct_byte_identical" if same_raw else "format_only"] += 1
        if r.get("kind") == "FORMAT":
            a["format_only_scorer_kind"] += 1
        if r.get("kind") == "LENIENT":
            a["lenient_only"] += 1
    a["scored_n"] = a["n"] - a["both_null"]
    a["accuracy"] = a["correct"] / a["scored_n"] if a["scored_n"] else None
    a["strict_serialisation_agreement"] = (
        a["correct_byte_identical"] / a["scored_n"] if a["scored_n"] else None)
    a["format_only_share_of_scorable"] = (
        a["format_only"] / a["scored_n"] if a["scored_n"] else None)
    a["format_only_share_of_naive_mismatches"] = (
        a["format_only"] / a["naive_str_mismatch"] if a["naive_str_mismatch"] else None)
    return a


def _ref_profile(rows):
    vals = [str(r.get("ref")) for r in rows if r["verdict"] != "both_null"]
    if not vals:
        return {"rows_profiled": 0, "distinct_reference_values": 0,
                "modal_reference_value": None, "modal_share": None}
    c = Counter(vals)
    top, n = c.most_common(1)[0]
    return {"rows_profiled": len(vals), "distinct_reference_values": len(c),
            "modal_reference_value": top[:60], "modal_share": round(n / len(vals), 4)}


def classify_one_point_oh(stats, profile):
    if stats["accuracy"] is None:
        return "NOT_MEASURED__EVERY_PAIR_BOTH_NULL"
    if stats["accuracy"] != 1.0:
        return None
    if stats["scored_n"] < 30 or stats["scored_n"] < .25 * stats["n"]:
        return (f"EXACTLY_1_0__NEGLIGIBLE_SAMPLE: only {stats['scored_n']} of "
                f"{stats['n']} pairs were scorable ({stats['both_null']} both_null).")
    if profile["distinct_reference_values"] <= 1:
        return ("EXACTLY_1_0__BY_CONSTRUCTION: one distinct reference value across the "
                "scorable corpus; solved by emitting a constant and non-discriminating.")
    if stats["lenient_only"]:
        return ("EXACTLY_1_0__LENIENT: reference is discriminating "
                f"({profile['distinct_reference_values']} distinct values), but some pairs "
                "pass only under the scorer's fuzzy rule.")
    if stats["format_only"]:
        return ("EXACTLY_1_0__EARNED_AFTER_NORMALISATION: reference is discriminating "
                f"({profile['distinct_reference_values']} distinct values); zero value "
                f"disagreements, with {stats['format_only']} serialisation-only differences.")
    return ("EXACTLY_1_0__EARNED_BYTE_FOR_BYTE: reference is discriminating "
            f"({profile['distinct_reference_values']} distinct values) and every scorable "
            "pair agrees on the raw value.")


def field_decomposition(per_statement, fields, prefix="transactions[]."):
    out = {}
    for leaf in fields:
        key = prefix + leaf
        rows = [r for st in per_statement for r in st["fields"].get(key, [])]
        if not rows:
            continue
        stats, profile = _field_stats(rows), _ref_profile(rows)
        stats["reference_profile"] = profile
        ann = classify_one_point_oh(stats, profile)
        if ann:
            stats["exactly_1_0_annotation"] = ann
        out[leaf] = stats
    return out


_SQ = lambda s: re.sub(r"\s+", "", str(s or "")).casefold()


def desc_defect_class(pred, ref):
    a, b = _SQ(pred), _SQ(ref)
    if a == b:
        return "spacing_only"
    if b == a + "in":
        return "dropped_trailing_country_code"
    if a == b + "in":
        return "added_trailing_country_code"
    return "real_character_difference"


FIDELITY_ONLY = {"spacing_only", "dropped_trailing_country_code",
                 "added_trailing_country_code"}


def joint_row_correctness(per_statement, fields=TXN_PRIORITY):
    total = clean = clean_fid = 0
    breakdown, desc_classes = Counter(), Counter()
    for st in per_statement:
        cols = [st["fields"].get("transactions[]." + f, []) for f in fields]
        if not cols[0]:
            continue
        assert all(len(c) == len(cols[0]) for c in cols), st["statement_id"]
        for rows in zip(*cols):
            total += 1
            bad = [f for f, r in zip(fields, rows) if r["verdict"] != "correct"]
            if not bad:
                clean += 1
                clean_fid += 1
                continue
            breakdown["+".join(bad)] += 1
            fatal = [f for f in bad if f != "description"]
            if "description" in bad:
                r = rows[fields.index("description")]
                klass = desc_defect_class(r.get("pred"), r.get("ref"))
                desc_classes[klass] += 1
                if klass not in FIDELITY_ONLY:
                    fatal.append("description")
            if not fatal:
                clean_fid += 1
    return {
        "fields_required_correct": list(fields), "matched_pairs": total,
        "all_fields_correct": clean,
        "all_fields_correct_rate": clean / total if total else None,
        "rows_with_at_least_one_defect": total - clean,
        "defect_signature_counts": dict(breakdown.most_common()),
        "all_fields_correct_excluding_description_fidelity_only": clean_fid,
        "all_fields_correct_rate_excluding_description_fidelity_only": (
            clean_fid / total if total else None),
        "description_defect_classes": dict(desc_classes),
        "_what_this_measures": ("Joint value correctness over description-matched pairs. "
                                "The fidelity-tolerant reading forgives spacing and a solely "
                                "added/dropped trailing country code, while charging every "
                                "real character or non-description defect."),
    }


def row_alignment(txn):
    out = {
        "_what_this_measures": ALIGNMENT_DISCLAIMER, "is_correctness_claim": False,
        "admission_rule": "description similarity >=0.60; strict 1:1; order-insensitive",
        "statements": None, "rows_pred": txn["n_pred_total"],
        "rows_ref": txn["n_ref_total"], "rows_matched": txn["matched"],
        "rows_missing_vs_reference": txn["n_ref_total"] - txn["matched"],
        "pairing_precision": txn["precision"], "pairing_recall": txn["recall"],
        "pairing_f1": txn["f1"], "pairing_match_rate_vs_ref": txn["match_rate_vs_ref"],
        "row_count_exact_match_statements": txn["statements_txn_count_equal"],
        "mean_desc_sim": txn["desc_mean_similarity"],
        "desc_exact_char_for_char": txn["desc_exact"],
        "desc_exact_whitespace_insensitive": txn["desc_exact_ws_insensitive"],
        "renamed_from": {"pairing_precision": "precision", "pairing_recall": "recall",
                         "pairing_f1": "f1", "pairing_match_rate_vs_ref": "match_rate_vs_ref"},
    }
    names = ("pairing_precision", "pairing_recall", "pairing_f1",
             "pairing_match_rate_vs_ref")
    out["exactly_1_0_metrics"] = [k for k in names if out[k] == 1.0]
    if out["exactly_1_0_metrics"]:
        out["exactly_1_0_annotation"] = (
            "EXACTLY_1_0__BY_CONSTRUCTION: description-based admission saturates because "
            f"the mean admitted-pair description similarity is {out['mean_desc_sim']}. "
            "These are alignment figures and never transaction accuracy.")
    return out


def decompose(summary, cells, comparison):
    per = per_statement_from_cells(cells, comparison)
    txn = summary["txn"]
    joint = joint_row_correctness(per)
    nref = txn["n_ref_total"]
    joint["reference_rows"] = nref
    joint["all_fields_correct_rate_over_reference_rows"] = joint["all_fields_correct"] / nref
    joint["all_fields_correct_rate_over_reference_rows_excl_desc_fidelity"] = (
        joint["all_fields_correct_excluding_description_fidelity_only"] / nref)
    return {
        "_schema": ("SPLIT 2026-08-11: old txn ratios measured description-based row "
                    "admission. Alignment and correctness are now separate."),
        "row_alignment": row_alignment(txn),
        "value_correctness": {
            "_what_this_measures": ("Per-field scorer verdicts over matched pairs. "
                                    "format_only agrees after existing scorer normalisation "
                                    "and is never folded into wrong_value."),
            "denominator_note": "matched pairs only; unpaired rows remain in row_alignment",
            "priority": field_decomposition(per, TXN_PRIORITY),
            "secondary": field_decomposition(per, TXN_SECONDARY),
        },
        "joint_row_correctness": joint,
    }
