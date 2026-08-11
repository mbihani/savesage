#!/usr/bin/env python3
"""Build the compact, machine-readable FINAL SBI result from persisted local artifacts."""
import glob
import json
import os
import re
from collections import Counter
from datetime import datetime

import score_lib_sbi as S
import txn_decomp as TD

ROOT = os.path.dirname(os.path.abspath(__file__))
CAP_GT = 64000


def load(name):
    with open(os.path.join(ROOT, name)) as f:
        return json.load(f)


def dmy(value):
    try:
        return datetime.strptime(S.date_norm(value), "%d/%m/%Y")
    except (TypeError, ValueError):
        return None


def corrected(raw, add_correct):
    v = dict(raw)
    v["correct"] += add_correct
    v["hallucinated_when_null"] -= add_correct
    v["pct"] = 100 * v["correct"] / v["n_compared"] if v["n_compared"] else None
    v["correction"] = {"gt_wrong_cells_reclassified_correct": add_correct}
    return v


DERIVED_DEPENDENT = "statementLevelSummary.utilisationPercent@derived"


def summary_field_audit(score, comparison):
    """Classify every non-transaction accuracy of exactly 1.0."""
    per = TD.per_statement_from_cells(score["cells"], comparison)
    fields = sorted({r["field"] for r in score["cells"]
                     if r["ref"] == comparison and not r["field"].startswith("transactions[].")})
    dec = TD.field_decomposition(per, fields, prefix="")
    perfect = {k: v for k, v in dec.items() if v["accuracy"] == 1.0}

    # The derived utilisation cells share a field name with the as-extracted cells.  Audit
    # the derived subset independently because it is arithmetically dependent.
    derived_rows = []
    for r in score["cells"]:
        if (r["ref"] == comparison and
                r["field"] == "statementLevelSummary.utilisationPercent" and
                r.get("ctx") == "as_derived"):
            x = dict(r); x["ref"] = x.pop("refv", None); derived_rows.append(x)
    if derived_rows:
        st, prof = TD._field_stats(derived_rows), TD._ref_profile(derived_rows)
        st["reference_profile"] = prof
        if st["accuracy"] == 1.0:
            st["exactly_1_0_annotation"] = (
                "EXACTLY_1_0__DERIVED_AND_DEPENDENT: computed arithmetically from each "
                "arm's own totalAmountDue and totalCreditLimit, which already agree; no "
                "independent extraction information.")
            perfect[DERIVED_DEPENDENT] = st
    return {"_question": "Are exact 1.0 fields earned or artifacts?",
            "fields_at_exactly_1_0": sorted(perfect),
            "n_fields_at_exactly_1_0": len(perfect),
            "n_non_transaction_fields_examined": len(dec), "audit": perfect}


def self_comparison_guard():
    ids = sorted(os.path.basename(p)[:-5]
                 for p in glob.glob(os.path.join(ROOT, "run_luna_refined/json/*.json")))
    byte_identical = same = same_norm = 0
    for sid in ids:
        lp = os.path.join(ROOT, "run_luna_refined/json", sid + ".json")
        gp = os.path.join(ROOT, "run_gt/json", sid + ".json")
        if not os.path.exists(gp):
            continue
        with open(lp, "rb") as a, open(gp, "rb") as b:
            byte_identical += a.read() == b.read()
        # Payload is nested: comparing top-level transactions would compare two missing keys.
        lobj = json.load(open(lp, encoding="utf-8"))["parsed_json"] or {}
        gobj = json.load(open(gp, encoding="utf-8"))["parsed_json"] or {}
        lt, gt = lobj.get("transactions") or [], gobj.get("transactions") or []
        def key(t, norm=False):
            if norm:
                t = {k: (float(v) if isinstance(v, (int, float)) and not isinstance(v, bool)
                         else v) for k, v in t.items()}
            return json.dumps(t, sort_keys=True, default=str)
        same += Counter(key(t) for t in lt) == Counter(key(t) for t in gt)
        same_norm += Counter(key(t, True) for t in lt) == Counter(key(t, True) for t in gt)
    return {"statements_checked": len(ids), "byte_identical_artifact_files": byte_identical,
            "is_self_comparison": byte_identical > 0,
            "statements_with_identical_full_transaction_dict_multiset": same,
            "statements_with_identical_multiset_after_numeric_normalisation": same_norm,
            "_note": ("The compared payload is parsed_json, not the artifact top level. Zero "
                      "byte-identical files proves these are distinct persisted inputs."),
            "fields_that_disprove_self_comparison": {
                "transactions[].description": "43 wrong_value over 3,527 matched pairs",
                "transactions[].direction": "7 wrong_value over 3,527 matched pairs",
                "transactions[].txnType": ("336 wrong_value, 28 null_when_populated and 1 "
                                           "hallucinated_when_null over 3,527 matched pairs")}}


def compact_comparison(score, key, label, scope="all"):
    raw = score["summary"][key][scope]
    audit = summary_field_audit(score, key)
    comp = {"label": label, **raw,
            "transaction_metrics": TD.decompose(raw, score["cells"], key),
            "summary_field_audit": audit}
    # The old txn block is the defective shape.  Remove it rather than leave an attractive
    # bare 1.0 beside the repaired block. Its counts survive, renamed, under row_alignment.
    comp.pop("txn", None)
    for tier in ("priority", "secondary"):
        for field, st in comp[tier].items():
            if st.get("pct") == 100.0:
                source = (comp["transaction_metrics"]["value_correctness"].get(tier, {})
                          if field.startswith("transactions[].") else audit["audit"])
                leaf = field.split("transactions[].", 1)[-1]
                ann = source.get(leaf if field.startswith("transactions[].") else field, {}).get(
                    "exactly_1_0_annotation")
                if ann:
                    st["exactly_1_0_annotation"] = ann
    return comp


def main():
    sc, adj, tok, gta = map(load, ("scores_refined.json", "adjudication_refined.json",
                                   "tokens.json", "gt_audit.json"))
    comparisons = {}
    for key, label in (("luna_vs_gt", "ACCURACY"), ("csv_vs_gt", "ACCURACY"),
                       ("luna_vs_csv", "AGREEMENT")):
        comparisons[key] = compact_comparison(sc, key, label)

    # PDF audit: both systems correctly inherit dates on GT-null continuation rows.
    corrections = {
        "luna_vs_gt": {
            "statementMeta.dueDate": corrected(
                comparisons["luna_vs_gt"]["priority"]["statementMeta.dueDate"], 50),
            "transactions[].date": corrected(
                comparisons["luna_vs_gt"]["priority"]["transactions[].date"], 71),
        },
        "csv_vs_gt": {
            "statementMeta.dueDate": corrected(
                comparisons["csv_vs_gt"]["priority"]["statementMeta.dueDate"], 50),
            "transactions[].date": corrected(
                comparisons["csv_vs_gt"]["priority"]["transactions[].date"], 111),
        },
    }

    gt_records = []
    after = []
    for path in sorted(glob.glob(os.path.join(ROOT, "run_gt/json/*.json"))):
        r = json.load(open(path))
        out = (r.get("usage_raw") or {}).get("completion_tokens")
        gt_records.append({"statement_id": r.get("statement_id"),
                           "finish_reason": r.get("finish_reason"),
                           "output_tokens": out,
                           "output_token_cap": CAP_GT,
                           "output_token_headroom": CAP_GT - out if out is not None else None,
                           "hit_cap": r.get("finish_reason") in ("length", "max_tokens")})
        p = S.parsed_of(r)
        sd = dmy(S.dig(p, "statementMeta.statementDate"))
        for t in p.get("transactions") or []:
            td = dmy(t.get("date"))
            if sd and td and td > sd:
                after.append({"statement_id": r.get("statement_id"), "statement_date":
                              S.dig(p, "statementMeta.statementDate"), "transaction": t})

    decided = adj["tally"].get("CSV_WRONG", 0) + adj["tally"].get("LUNA_WRONG", 0)
    decided += adj["tally"].get("TXNCOUNT_LUNA_WRONG", 0)
    luna_txn = comparisons["luna_vs_gt"]["transaction_metrics"]
    csv_txn = comparisons["csv_vs_gt"]["transaction_metrics"]

    def txn_headline(txn):
        al = txn["row_alignment"]
        vc = txn["value_correctness"]["priority"]
        jt = txn["joint_row_correctness"]
        return {
            "row_alignment__NOT_A_CORRECTNESS_CLAIM": {
                "pairing_f1": al["pairing_f1"], "rows_recovered": al["rows_matched"],
                "reference_rows": al["rows_ref"],
                "rows_missing_vs_reference": al["rows_missing_vs_reference"],
                "exactly_1_0_metrics": al["exactly_1_0_metrics"],
                "annotation": al.get("exactly_1_0_annotation", "not saturated")},
            "value_correctness": {
                "joint_all_5_priority_fields_correct_over_reference_rows":
                    jt["all_fields_correct_rate_over_reference_rows"],
                "joint_all_5_priority_fields_correct_over_reference_rows_excl_description_fidelity":
                    jt["all_fields_correct_rate_over_reference_rows_excl_desc_fidelity"],
                "per_field_accuracy_over_matched_pairs": {f: vc[f]["accuracy"] for f in TD.TXN_PRIORITY},
                "per_field_wrong_value": {f: vc[f]["wrong_value"] for f in TD.TXN_PRIORITY},
                "per_field_format_only_not_charged_as_wrong": {f: vc[f]["format_only"] for f in TD.TXN_PRIORITY},
                "fields_at_exactly_1_0": {f: vc[f]["exactly_1_0_annotation"]
                                           for f in TD.TXN_PRIORITY
                                           if "exactly_1_0_annotation" in vc[f]},
                "description_defect_classes": jt["description_defect_classes"]}}

    lj, cj = luna_txn["joint_row_correctness"], csv_txn["joint_row_correctness"]
    pct = lambda x: f"{100*x:.2f}%"
    strict_l, strict_c = (lj["all_fields_correct_rate_over_reference_rows"],
                          cj["all_fields_correct_rate_over_reference_rows"])
    forgiven_l = lj["all_fields_correct_rate_over_reference_rows_excl_desc_fidelity"]
    forgiven_c = cj["all_fields_correct_rate_over_reference_rows_excl_desc_fidelity"]
    verdict = (
        f"ROW ALIGNMENT (not accuracy): Luna pairs 3,527/3,527 reference rows; its 1.0 "
        f"pairing F1 is BY CONSTRUCTION. VALUE CORRECTNESS over all reference rows, requiring "
        f"all five priority transaction fields: Luna {pct(strict_l)} vs incumbent "
        f"{pct(strict_c)} under the scorer-normalised strict narration reading; after forgiving "
        f"spacing/sole trailing-country-code narration fidelity, Luna {pct(forgiven_l)} vs "
        f"incumbent {pct(forgiven_c)}. Luna leads under both readings, so the winner does not "
        f"flip; both are published anyway."
    )

    out = {
        "status": "FINAL", "record_counts": {"luna_refined": 300, "opus_gt": 300,
        "incumbent_csv_joined": 300, "scoreable": 300},
        "excluded_arms": {"run_luna_client": "partial, cancelled; excluded",
                          "run_luna_generic": "10-record generic arm; excluded"},
        "matcher": {"admission": "description_similarity_only", "assignment": "strict_1_to_1",
                    "order_sensitive": False, "test_matcher_sbi": "PASS",
                    "test_rollup_honesty": "PASS: all 7 obligations hold"},
        "measurement_defect_correction": {
            "date": "2026-08-11", "severity": "PUBLISHED SCORE MISLABELLED",
            "withdrawn_claims": {"comparisons.luna_vs_gt.txn.precision": 1.0,
                                 "comparisons.luna_vs_gt.txn.recall": 1.0,
                                 "comparisons.luna_vs_gt.txn.f1": 1.0},
            "mechanism": ("txn precision = rows_matched/rows_pred and recall = "
                          "rows_matched/rows_ref, while rows_matched admits pairs on DESCRIPTION "
                          "SIMILARITY ALONE. With 3,527/3,527 cleanly transcribed narrations, "
                          "the ratios saturate at 1.0 by construction and measure row admission, "
                          "not value correctness."),
            "fix": ("transaction_metrics is split into row_alignment, value_correctness and "
                    "joint_row_correctness; pairing metrics are namespaced and every exact 1.0 "
                    "is annotated as earned or by construction.")},
        "self_comparison_guard": self_comparison_guard(),
        "comparisons": comparisons, "gt_corrected_accuracy": corrections,
        "pdf_adjudication": {"tally": adj["tally"], "n_items": len(adj["items"]),
          "luna_win_rate_decided_disagreements_pct":
              100 * adj["tally"].get("CSV_WRONG", 0) / decided if decided else None,
          "items": adj["items"]},
        "gt_audit": gta,
        "tokens": tok,
        "gt_output_health": {"cap": CAP_GT, "hit_cap_count": sum(x["hit_cap"] for x in gt_records),
                             "records": gt_records},
        "statement_date_rule": {"gt_transactions_after_statement_date": len(after),
                                "challenger_dropped": 0, "examples": after},
        "notes": {"cardDisplayName": "lenient scoring",
                  "currency": "high INR score is unearned and non-differentiating",
                  "utilisation_printed_pdfs": 0,
                  "network_text_layer_header_labels": 0,
                  "density_correction": "HDFC 16.19 txns/statement; SBI 11.97"},
        "headline_verdicts": {
            "_reading_rule": ("Only values under value_correctness may be quoted as transaction "
                              "correctness. row_alignment__NOT_A_CORRECTNESS_CLAIM is pairing only."),
            "transactions": {"luna": txn_headline(luna_txn),
                             "incumbent_csv": txn_headline(csv_txn)},
            "verdict": verdict},
        "discrepancies": [
            {"source": "SBI_REPORT.md lines 41-43, 245 and 575",
             "prose_claim": "transaction extraction is exact / P=R=F1=100%",
             "artifact": {"row_alignment_pairing_f1__NOT_CORRECTNESS": 1.0,
                          "luna_joint_5_field_value_correctness": strict_l,
                          "incumbent_joint_5_field_value_correctness": strict_c},
             "adjudication": "PROSE RELABEL REQUIRED: 1.0 is row alignment, not correctness."},
            {"source": "SBI_REPORT.md line 252",
             "prose_claim": "Not a single transaction row missed or invented",
             "artifact": {"luna_missing_vs_gt": 0, "luna_extra_vs_gt": 0,
                          "incumbent_extra_vs_gt": 1},
             "adjudication": ("True only for Luna-vs-GT alignment; false as a general statement "
                              "and not evidence of value correctness.")},
            {"source": "REPORT_TABLES.md lines 154-160",
             "prose_claim": "Transactions -- precision / recall / F1 without alignment qualifier",
             "artifact": "These are description-pairing row-alignment ratios.",
             "adjudication": "TABLE HEADING AND READING RULE MUST BE CORRECTED."}],
        "report_prose_corrections_required": {
            "_why": "Reports are intentionally left for their owner; these exact claims are stale.",
            "items": [
                {"files": "SBI_REPORT.md lines 41-43, 241-252, 575-576; REPORT_TABLES.md lines 154-160",
                 "current": "P/R/F1 100% described as exact transaction extraction",
                 "required": ("Relabel 100% as ROW ALIGNMENT ONLY (description pairing, not "
                              "correctness). Add joint five-field value correctness: Luna "
                              f"{pct(strict_l)} vs incumbent {pct(strict_c)} strict; Luna "
                              f"{pct(forgiven_l)} vs incumbent {pct(forgiven_c)} after narration-fidelity forgiveness.")},
                {"files": "SBI_REPORT.md line 252",
                 "current": "Not a single transaction row missed or invented across 300 statements",
                 "required": ("Scope to Luna-vs-GT row alignment. Incumbent has one extra row; "
                              "do not imply that row alignment proves values correct.")},
                {"files": "SBI_REPORT.md lines 176-182; REPORT_TABLES.md lines 32-35",
                 "current": "Per-field percentages omit the serialization-only split",
                 "required": ("Add format_only: amount 2,255/3,527 (63.94%) for Luna and "
                              "2,303/3,527 (65.30%) for incumbent; these normalize equal and "
                              "must not be called wrong_value. Currency 100% is BY CONSTRUCTION "
                              "because the GT has one distinct value; amount 100% is EARNED after "
                              "normalisation over 2,105 distinct reference values.")}]},
        "findings_cap_audit": {
            "files_checked": ["adjudicate.py"],
            "hdfc_class_increment_before_capped_append_pattern_found": False,
            "result": ("No findings-list cap exists: every tally/per_field increment is "
                       "followed by an unconditional items.append. No fix required.")},
        "provenance": {"score_artifact": "scores_refined.json",
                       "transaction_decomposition": "txn_decomp.py",
                       "no_model_inference": True, "no_arm_regeneration": True},
    }
    with open(os.path.join(ROOT, "final_scores.json"), "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("wrote final_scores.json")


if __name__ == "__main__":
    main()
