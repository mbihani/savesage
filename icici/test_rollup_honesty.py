#!/usr/bin/env python3
"""Proof obligations for the ROLL-UP. Run: python3 test_rollup_honesty.py

Sibling to test_matcher_noncircular.py, which guards the layer BELOW this one.
test_matcher_noncircular.py proves score_lib's matcher does not admit pairs on the fields it
then scores. It passed the whole time the defect this file guards against was live, because
the defect was not in the matcher: build_final_scores republished the matcher's ROW-ADMISSION
ratios (rows_matched/rows_pred, rows_matched/rows_ref) as `transaction_metrics` and hoisted
micro_f1 into headline_verdicts as `luna_transaction_micro_f1: 1.0`, while the per-field
transaction verdicts sitting right next to it recorded 295 wrong descriptions, 5 wrong
directions and 2 wrong amounts.

THE OBLIGATION THAT CATCHES THAT CLASS OF DEFECT: a headline transaction metric may not be
exactly 1.0 while the underlying per-field verdicts contain any wrong_value -- unless it is
explicitly namespaced and annotated as an alignment/by-construction figure.

Everything is asserted against final_scores.json as published, so it fails on the artifact a
reviewer would actually read, not on some intermediate the build could bypass.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import txn_decomp as TD  # noqa: E402

# Substrings that make a numeric leaf readable as a rate/score. Matched against the WHOLE
# path, so `per_field_accuracy_over_matched_pairs.currency` counts as a metric even though its
# own leaf name is just a field name.
CORRECTNESS_NAMES = ("micro_f1", "micro_precision", "micro_recall", "macro_f1",
                     "f1", "precision", "recall", "accuracy", "_rate")
# A path segment that unambiguously declares "this is pairing/alignment, not correctness".
ALIGNMENT_MARKERS = ("NOT_A_CORRECTNESS_CLAIM", "row_alignment", "pairing_")


def walk(obj, path=""):
    """Yield (dotted_path, leaf_value) for every scalar leaf."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from walk(v, f"{path}.{k}" if path else str(k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from walk(v, f"{path}[{i}]")
    else:
        yield path, obj


def main():
    fs = json.load(open(os.path.join(HERE, "final_scores.json"), encoding="utf-8"))
    scope = ("comparisons", "heldout_comparisons")
    checked_metrics = 0

    # ---- 1. THE HEADLINE OBLIGATION.
    # If any per-field transaction verdict block contains a wrong_value, then no metric
    # anywhere under headline_verdicts.transactions may be exactly 1.0 unless its own path
    # marks it as alignment / not-a-correctness-claim.
    total_wrong = 0
    for group in scope:
        for cmp_name, comp in (fs.get(group) or {}).items():
            vc = comp["transaction_metrics"]["value_correctness"]
            for tier in ("priority", "secondary"):
                for f, st in vc[tier].items():
                    total_wrong += st["wrong_value"]
    assert total_wrong > 0, (
        "No transaction wrong_value anywhere. Either this corpus is genuinely flawless -- "
        "verify by hand before believing it -- or the verdicts stopped being computed. "
        "This test cannot protect you in that state.")

    # The names the artifact has explicitly annotated as "1.0 for a stated reason".
    annotated = set()
    hv = fs.get("headline_verdicts") or {}
    for arm in (hv.get("transactions") or {}).values():
        annotated |= set((arm.get("value_correctness") or {}).get("fields_at_exactly_1_0") or {})
        annotated |= set(
            (arm.get("row_alignment__NOT_A_CORRECTNESS_CLAIM") or {}).get("exactly_1_0_metrics") or [])

    for path, val in walk(hv, "headline_verdicts"):
        leaf = path.rsplit(".", 1)[-1]
        if not isinstance(val, (int, float)) or isinstance(val, bool):
            continue
        if not any(n in path for n in CORRECTNESS_NAMES):
            continue
        checked_metrics += 1
        if val != 1.0:
            continue
        assert any(m in path for m in ALIGNMENT_MARKERS) or leaf in annotated, (
            f"{path} = 1.0 is published as a correctness headline while the underlying "
            f"per-field transaction verdicts hold {total_wrong} wrong_value, and nothing "
            f"annotates why it is 1.0. Either it is an alignment/pairing metric -- then move "
            f"it under a `row_alignment` / `*NOT_A_CORRECTNESS_CLAIM*` key -- or it is 1.0 by "
            f"construction and must be listed in value_correctness.fields_at_exactly_1_0 with "
            f"a reason -- or it is wrong.")

    # The specific withdrawn key must never come back.
    assert "luna_transaction_micro_f1" not in (fs.get("headline_verdicts") or {}), \
        "luna_transaction_micro_f1 is the withdrawn defect key; it is a pairing ratio."

    # ---- 2. EVERY exactly-1.0 metric is annotated as to WHY.
    for group in scope:
        for cmp_name, comp in (fs.get(group) or {}).items():
            tm = comp["transaction_metrics"]
            al = tm["row_alignment"]
            assert al["is_correctness_claim"] is False, cmp_name
            if al["exactly_1_0_metrics"]:
                assert al.get("exactly_1_0_annotation"), \
                    f"{group}.{cmp_name}: saturated pairing metrics with no annotation"
            for tier in ("priority", "secondary"):
                for f, st in tm["value_correctness"][tier].items():
                    if st["accuracy"] == 1.0:
                        assert st.get("exactly_1_0_annotation"), \
                            f"{group}.{cmp_name}.{tier}.{f}: accuracy 1.0 with no annotation"
            for f, st in (comp.get("summary_field_audit") or {}).get("audit", {}).items():
                assert st.get("exactly_1_0_annotation"), \
                    f"{group}.{cmp_name}.summary_field_audit.{f}: 1.0 with no annotation"

    # ---- 3. format_only is never folded into wrong_value, and the split is exhaustive.
    for group in scope:
        for cmp_name, comp in (fs.get(group) or {}).items():
            for tier in ("priority", "secondary"):
                for f, st in comp["transaction_metrics"]["value_correctness"][tier].items():
                    where = f"{group}.{cmp_name}.{tier}.{f}"
                    assert st["correct"] == st["correct_byte_identical"] + st["format_only"], \
                        f"{where}: correct != byte_identical + format_only"
                    assert (st["correct"] + st["wrong_value"] + st["null_when_populated"]
                            + st["hallucinated_when_null"] + st["both_null"] == st["n"]), \
                        f"{where}: verdict counts do not sum to n"
                    assert st["format_only"] >= st["format_only_scorer_kind"], \
                        f"{where}: the FORMAT-kind subset must not exceed format_only"
                    if st["scored_n"]:
                        assert abs(st["accuracy"] - st["correct"] / st["scored_n"]) < 1e-12, where

    # ---- 4. Alignment and correctness are actually SEPARATE, and correctness is not a
    #         relabelled copy of the pairing numbers.
    luna = fs["comparisons"]["luna_vs_gt"]["transaction_metrics"]
    assert luna["row_alignment"]["pairing_f1"] == 1.0, \
        "fixture assumption changed: ICICI Luna pairing F1 is no longer saturated"
    jt = luna["joint_row_correctness"]
    assert jt["all_fields_correct_rate"] < 1.0, \
        "joint row correctness saturated at 1.0 -- that is the defect signature again"
    assert jt["all_fields_correct"] + jt["rows_with_at_least_one_defect"] == jt["matched_pairs"]
    assert (jt["all_fields_correct_excluding_description_fidelity_only"]
            >= jt["all_fields_correct"]), "the fidelity-tolerant variant must be a superset"

    # ---- 5. The description defect classes still match the standalone artifact, so the
    #         fidelity-tolerant variant cannot silently drift from desc_defect_classes.py.
    ddc = json.load(open(os.path.join(HERE, "desc_defect_classes.json"), encoding="utf-8"))
    for cmp_key, art_key in (("luna_vs_gt", "luna_refined_vs_GT__all"),
                             ("csv_vs_gt", "CSV_vs_GT__all")):
        if art_key not in ddc:
            continue
        mine = fs["comparisons"][cmp_key]["transaction_metrics"][
            "joint_row_correctness"]["description_defect_classes"]
        assert mine == ddc[art_key]["classes"], \
            f"{cmp_key}: description defect classes drifted from desc_defect_classes.json"

    # ---- 6. The 1.0 summary fields were re-examined, and the self-comparison question is
    #         answered in the artifact rather than left to the reader.
    g = fs["self_comparison_guard"]
    assert g["byte_identical_artifact_files"] == 0 and g["is_self_comparison"] is False, \
        "the two arms are the same artifact -- every agreement number is meaningless"
    audit = fs["comparisons"]["luna_vs_gt"]["summary_field_audit"]
    assert audit["n_fields_at_exactly_1_0"] > 0 and audit["audit"], \
        "summary_field_audit is empty; the 1.0 statement-level fields were not re-examined"
    by_construction = [f for f, st in audit["audit"].items()
                       if "BY_CONSTRUCTION" in st["exactly_1_0_annotation"]
                       or "DERIVED_AND_DEPENDENT" in st["exactly_1_0_annotation"]
                       or "NEGLIGIBLE_SAMPLE" in st["exactly_1_0_annotation"]]
    assert by_construction, (
        "not one of the 1.0 statement-level fields is flagged as constant-corpus, derived or "
        "under-sampled -- implausible, so the classifier has probably stopped working")

    # ---- 7. Structural comparability with the sibling banks' roll-ups is preserved.
    for k in ("status", "record_counts", "excluded_arms", "matcher", "comparisons",
              "pdf_adjudication", "gt_audit", "tokens", "notes"):
        assert k in fs, f"missing top-level key {k} present in sbi/final_scores.json"
    for cmp_name, comp in fs["comparisons"].items():
        for k in ("label", "n_statements", "priority", "transaction_metrics"):
            assert k in comp, f"comparisons.{cmp_name} missing {k}"

    print(f"all 7 roll-up honesty obligations hold "
          f"({checked_metrics} headline metrics inspected, "
          f"{total_wrong} underlying wrong_value verdicts)")


if __name__ == "__main__":
    main()
