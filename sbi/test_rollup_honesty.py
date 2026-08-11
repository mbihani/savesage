#!/usr/bin/env python3
"""Seven publication obligations for SBI's final_scores.json.

Set SBI_INJECT_REGRESSION to ``headline_1``, ``format_fold`` or ``alignment_relabel`` to
prove the guard fails on each named defect class without modifying the artifact.
"""
import copy
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))


def walk(obj, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from walk(v, f"{path}.{k}" if path else k)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from walk(v, f"{path}[{i}]")
    else:
        yield path, obj


def main():
    fs = json.load(open(os.path.join(HERE, "final_scores.json"), encoding="utf-8"))
    mode = os.environ.get("SBI_INJECT_REGRESSION")
    if mode:
        fs = copy.deepcopy(fs)
        luna = fs["comparisons"]["luna_vs_gt"]["transaction_metrics"]
        if mode == "headline_1":
            fs["headline_verdicts"]["transactions"]["luna"]["value_correctness"][
                "transaction_accuracy"] = 1.0
        elif mode == "format_fold":
            st = luna["value_correctness"]["priority"]["amount"]
            st["wrong_value"] += st["format_only"]
        elif mode == "alignment_relabel":
            luna["row_alignment"]["is_correctness_claim"] = True
        else:
            raise AssertionError(f"unknown injection {mode}")

    # 1. No unannotated 1.0 correctness headline may coexist with underlying defects.
    total_wrong = 0
    for comp in fs["comparisons"].values():
        for tier in ("priority", "secondary"):
            total_wrong += sum(x["wrong_value"] for x in comp["transaction_metrics"][
                "value_correctness"][tier].values())
    assert total_wrong > 0
    allowed = set()
    for arm in fs["headline_verdicts"]["transactions"].values():
        allowed |= set(arm["value_correctness"]["fields_at_exactly_1_0"])
    for path, value in walk(fs["headline_verdicts"], "headline_verdicts"):
        if isinstance(value, bool) or value != 1.0:
            continue
        if not any(token in path for token in ("accuracy", "_rate", "correct_over_")):
            continue
        if "row_alignment__NOT_A_CORRECTNESS_CLAIM" in path:
            continue
        leaf = path.rsplit(".", 1)[-1]
        assert leaf in allowed, (f"{path}=1.0 is an unannotated correctness headline while "
                                 f"underlying verdicts contain {total_wrong} wrong_value")

    # 2. Every exact 1.0 accuracy is annotated, earned or explicitly an artifact.
    for name, comp in fs["comparisons"].items():
        tm = comp["transaction_metrics"]
        assert tm["row_alignment"]["is_correctness_claim"] is False, name
        if tm["row_alignment"]["exactly_1_0_metrics"]:
            assert "BY_CONSTRUCTION" in tm["row_alignment"]["exactly_1_0_annotation"]
        for tier in ("priority", "secondary"):
            for field, st in tm["value_correctness"][tier].items():
                if st["accuracy"] == 1.0:
                    assert "exactly_1_0_annotation" in st, f"{name}.{field}"
        for field, st in comp["summary_field_audit"]["audit"].items():
            assert "exactly_1_0_annotation" in st, f"{name}.{field}"

    # 3. format_only is distinct from wrong_value and all verdict buckets are exhaustive.
    for name, comp in fs["comparisons"].items():
        for tier in ("priority", "secondary"):
            for field, st in comp["transaction_metrics"]["value_correctness"][tier].items():
                where = f"{name}.{tier}.{field}"
                assert st["correct"] == st["correct_byte_identical"] + st["format_only"], where
                assert (st["correct"] + st["wrong_value"] + st["null_when_populated"] +
                        st["hallucinated_when_null"] + st["both_null"] == st["n"]), where
                assert st["format_only"] >= st["format_only_scorer_kind"], where
                if st["scored_n"]:
                    assert abs(st["accuracy"] - st["correct"] / st["scored_n"]) < 1e-12

    # 4. Alignment and correctness are separate; the 1.0 pairing figure is not copied value accuracy.
    luna = fs["comparisons"]["luna_vs_gt"]["transaction_metrics"]
    assert luna["row_alignment"]["pairing_f1"] == 1.0
    joint = luna["joint_row_correctness"]
    assert joint["all_fields_correct_rate"] < 1.0
    assert joint["all_fields_correct"] + joint["rows_with_at_least_one_defect"] == joint["matched_pairs"]

    # 5. Both strict and narration-fidelity-forgiven readings are published for both arms.
    for arm in ("luna", "incumbent_csv"):
        vc = fs["headline_verdicts"]["transactions"][arm]["value_correctness"]
        assert "joint_all_5_priority_fields_correct_over_reference_rows" in vc
        assert "joint_all_5_priority_fields_correct_over_reference_rows_excl_description_fidelity" in vc

    # 6. The self-comparison guard uses distinct artifacts.
    guard = fs["self_comparison_guard"]
    assert guard["statements_checked"] == 300
    assert guard["byte_identical_artifact_files"] == 0 and guard["is_self_comparison"] is False

    # 7. Structural contract and prose accountability.
    for key in ("status", "record_counts", "excluded_arms", "matcher", "comparisons",
                "pdf_adjudication", "gt_audit", "tokens", "notes", "discrepancies",
                "report_prose_corrections_required", "measurement_defect_correction"):
        assert key in fs, key
    assert fs["discrepancies"] and fs["report_prose_corrections_required"]["items"]
    print(f"all 7 roll-up honesty obligations hold ({total_wrong} wrong_value inspected)")


if __name__ == "__main__":
    main()
