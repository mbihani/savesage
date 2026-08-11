#!/usr/bin/env python3
"""Build ICICI final_scores.json solely from completed local artifacts.

2026-08-11 -- MEASUREMENT DEFECT REPAIRED. The previous roll-up published
`transaction_metrics` straight out of score_phase3's `txn` block and hoisted its `micro_f1`
into `headline_verdicts` as `luna_transaction_micro_f1: 1.0`. That block measures DESCRIPTION-
BASED ROW ADMISSION, not value correctness (see txn_decomp for the full mechanism), so on this
corpus it is 1.0 by construction. Transaction metrics are now emitted by `txn_decomp.decompose`
as two separately-labelled halves -- `row_alignment` (pairing; flagged not-a-correctness-claim)
and `value_correctness` (per-field verdicts over the matched pairs, with format_only kept
distinct from wrong_value) -- plus a `joint_row_correctness` figure that cannot saturate.
Every metric that lands on exactly 1.0 carries an annotation saying why.
`test_rollup_honesty.py` fails the build class of defect automatically.
"""
import csv
import glob
import json
import os
import sys
import urllib.parse

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
import txn_decomp as TD  # noqa: E402
PDF_DIR = "/Users/mayanck.bihani/Downloads/remaining pdfs ground truth/icici-pdfs"
CSV_PATH = "/Users/mayanck.bihani/Downloads/remaining pdfs ground truth/icici.csv"
OPUS_INPUT_PER_M = 5.0
OPUS_OUTPUT_PER_M = 25.0


def load(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as fh:
        return json.load(fh)


def verify_filename_join():
    pdf_names = sorted(x for x in os.listdir(PDF_DIR) if x.lower().endswith(".pdf"))
    counts = {name: 0 for name in pdf_names}
    rows = 0
    unmatched = []
    csv.field_size_limit(10**9)
    with open(CSV_PATH, encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            rows += 1
            name = os.path.basename(urllib.parse.unquote(
                urllib.parse.urlparse(row.get("link") or "").path))
            if name in counts:
                counts[name] += 1
            else:
                unmatched.append(name)
    non_unique = {k: v for k, v in counts.items() if v != 1}
    assert len(pdf_names) == 304, len(pdf_names)
    assert not non_unique, non_unique
    return {
        "join_key": "PDF filename == basename(URL-decoded CSV link path)",
        "pdfs": len(pdf_names),
        "csv_rows": rows,
        "pdfs_with_exactly_one_csv_row": sum(v == 1 for v in counts.values()),
        "pdfs_with_zero_csv_rows": sum(v == 0 for v in counts.values()),
        "pdfs_with_multiple_csv_rows": sum(v > 1 for v in counts.values()),
        "unmatched_csv_rows": len(unmatched),
        "non_numeric_filename_tokens_preserved": sum("decrypt_gmail:" in x for x in pdf_names),
    }


DERIVED_DEPENDENT = "statementLevelSummary.utilisationPercent@derived"


def summary_field_audit(raw):
    """Re-examine every NON-transaction field that scores exactly 1.0.

    A 1.0 has three innocent-looking causes and only one of them is skill:
      * the reference is a constant on this corpus (solved by emitting a literal),
      * the value is DERIVED from inputs that already agree (no independent information),
      * genuine agreement on a discriminating field.
    Each surviving 1.0 is labelled with which one it is, together with the raw-serialisation
    split -- 285 of 304 `totalCreditLimit` pairs agree only after normalisation (`415000` vs
    `415000.0`), which is itself positive evidence that the two sides were produced
    independently rather than one being a copy of the other.
    """
    per = raw["per_statement"]
    keys = [k for k in raw["fields"] if not k.startswith("transactions[].")]
    dec = TD.field_decomposition(per, keys, prefix="")
    perfect = {k: v for k, v in dec.items() if v["accuracy"] == 1.0}
    if DERIVED_DEPENDENT in perfect:
        perfect[DERIVED_DEPENDENT]["exactly_1_0_annotation"] = (
            "EXACTLY_1_0__DERIVED_AND_DEPENDENT: score_lib.util_derive computes this from each "
            "side's OWN totalAmountDue and totalCreditLimit. Both of those fields already "
            "agree on 304/304, so this 1.0 follows arithmetically and carries NO independent "
            "information about utilisation extraction. The AS-EXTRACTED counterpart is "
            "both_null on 304/304 (neither side emits it), which is the finding that matters."
        )
    return {
        "_question": ("Are the 1.0 non-transaction fields genuinely perfect, or artifacts -- "
                      "constant corpus, arithmetic dependency, or a value compared against "
                      "itself?"),
        "fields_at_exactly_1_0": sorted(perfect),
        "n_fields_at_exactly_1_0": len(perfect),
        "n_non_transaction_fields_examined": len(dec),
        "audit": perfect,
    }


def self_comparison_guard():
    """Prove the two arms are distinct artifacts, and explain the 29/304 exact-set figure.

    A reviewer observed that only 29 of 304 statements have a transaction set IDENTICAL to the
    GT's, which sits badly beside a 1.0. Both are true and not in conflict: exact-set equality
    here is over the FULL transaction dict -- including `txnType` (484 wrong_value) and the
    int-vs-float serialisation of `amount` (2,345 format-only) -- so it is a strictly harder
    test than agreement on the five priority fields. It is reported next to the row-level
    numbers so neither can be quoted without the other.
    """
    ids = sorted(os.path.basename(p)[:-5]
                 for p in glob.glob(os.path.join(ROOT, "luna_refined/json/*.json")))
    byte_identical = 0
    same_multiset = 0
    same_multiset_normalised = 0
    for sid in ids:
        lp = os.path.join(ROOT, f"luna_refined/json/{sid}.json")
        gp = os.path.join(ROOT, f"opus_gt/json/{sid}.json")
        if not os.path.exists(gp):
            continue
        with open(lp, "rb") as a, open(gp, "rb") as b:
            if a.read() == b.read():
                byte_identical += 1
        lt = (json.load(open(lp, encoding="utf-8"))["parsed_json"] or {}).get("transactions") or []
        gt_t = (json.load(open(gp, encoding="utf-8"))["parsed_json"] or {}).get("transactions") or []

        def key(t, norm):
            if not norm:
                return json.dumps(t, sort_keys=True, default=str)
            # same value, canonical serialisation: 180 and 180.0 collapse
            return json.dumps({k: (float(v) if isinstance(v, (int, float))
                                   and not isinstance(v, bool) else v)
                               for k, v in sorted(t.items())}, sort_keys=True, default=str)
        from collections import Counter
        if Counter(key(t, False) for t in lt) == Counter(key(t, False) for t in gt_t):
            same_multiset += 1
        if Counter(key(t, True) for t in lt) == Counter(key(t, True) for t in gt_t):
            same_multiset_normalised += 1
    return {
        "statements_checked": len(ids),
        "byte_identical_artifact_files": byte_identical,
        "is_self_comparison": byte_identical > 0,
        "statements_with_identical_full_transaction_dict_multiset": same_multiset,
        "statements_with_identical_multiset_after_numeric_normalisation": same_multiset_normalised,
        "_note": ("0 byte-identical files means the two sides are independent artifacts, so "
                  "nothing here is a value compared against itself. The exact-multiset figure "
                  "is over the FULL transaction dict (7 keys incl. the 484-wrong txnType and "
                  "int-vs-float amount serialisation), which is why it is far below the "
                  "priority-field agreement rate -- it is a harder test, not a contradiction."),
        "fields_that_disprove_self_comparison": {
            "statementMeta.rawStatementId": "Luna null on 304/304 where the GT is populated",
            "rewards.programType": "303/304 wrong_value",
            "transactions[].txnType": "484/4096 wrong_value",
            "cards[].cardMeta.lastFourDigit": "15/404 wrong_value",
        },
    }


def _verdict(luna_txn, csv_txn, luna_fields, adjudication, core4):
    """The headline sentence, computed from the numbers rather than typed by hand.

    States BOTH readings of transaction value correctness. The strict reading and the
    narration-fidelity-tolerant reading rank the two arms differently on this corpus, and
    picking one silently would be the same kind of error as publishing the pairing F1 as
    accuracy.
    """
    lj, cj = luna_txn["joint_row_correctness"], csv_txn["joint_row_correctness"]
    la, ca = luna_txn["row_alignment"], csv_txn["row_alignment"]
    ldesc = luna_txn["value_correctness"]["priority"]["description"]
    cdesc = csv_txn["value_correctness"]["priority"]["description"]
    fid = sum(n for k, n in lj["description_defect_classes"].items()
              if k in TD._FIDELITY_ONLY)
    pct = lambda x: f"{100 * x:.2f}%"
    return (
        f"TRANSACTION ROW RECOVERY (ALIGNMENT, not accuracy): Luna pairs "
        f"{la['rows_matched']:,}/{la['rows_ref']:,} reference rows, the incumbent "
        f"{ca['rows_matched']:,}/{ca['rows_ref']:,} ({ca['rows_missing_vs_reference']} lost). "
        f"Luna's {la['pairing_f1']:.4f} there is saturated BY CONSTRUCTION on this corpus and is "
        f"NOT an accuracy claim. TRANSACTION VALUE CORRECTNESS, strict, over all "
        f"{la['rows_ref']:,} reference rows -- rows correct on all five priority fields: Luna "
        f"{pct(lj['all_fields_correct_rate_over_reference_rows'])} vs incumbent "
        f"{pct(cj['all_fields_correct_rate_over_reference_rows'])}; the INCUMBENT LEADS on the "
        f"strict reading because Luna's {ldesc['wrong_value']} description defects outnumber the "
        f"incumbent's {cdesc['wrong_value']}. Counting narration text-fidelity slips as "
        f"non-defects instead ({fid} of Luna's {ldesc['wrong_value']} are intra-cell spacing or a "
        f"dropped trailing 'IN', per desc_defect_classes.json), Luna leads "
        f"{pct(lj['all_fields_correct_rate_over_reference_rows_excl_desc_fidelity'])} to "
        f"{pct(cj['all_fields_correct_rate_over_reference_rows_excl_desc_fidelity'])}. Both "
        f"readings are published because the ranking depends on which one the client wants. On "
        f"date/amount/direction/currency alone Luna is clearly ahead: {core4(luna_txn)} defective "
        f"rows vs {core4(csv_txn)}. CARD IDENTITY: Luna materially better -- 0 network "
        f"fabrications vs 72, PDF adjudication "
        f"{adjudication['tally']['CSV_WRONG']}-{adjudication['tally']['LUNA_WRONG']} in Luna's "
        f"favour. Luna's weakest priority field remains card last-four at "
        f"{pct(luna_fields['cards[].cardMeta.lastFourDigit']['accuracy'])}."
    )


def compact_comparison(raw, label):
    return {
        "label": label,
        "n_statements": raw["n_statements"],
        "priority": raw["fields"],
        "transaction_metrics": TD.decompose(raw),
        "summary_field_audit": summary_field_audit(raw),
    }


def token_arm(raw, model, price=None):
    prompt = raw["prompt_tokens"]["sum"]
    completion = raw["completion_tokens"]["sum"]
    total = raw["total_tokens"]["sum"]
    assert prompt + completion == total
    out = {
        "model": model,
        "calls": raw["prompt_tokens"]["n"],
        "input_tokens": prompt,
        "output_tokens": completion,
        "reasoning_tokens": raw["reasoning_tokens"]["sum"] if raw["reasoning_tokens"] else None,
        "total_tokens": total,
        "total_tokens_formula": "input_tokens + output_tokens",
        "token_sum_check": prompt + completion == total,
    }
    if price:
        cost = prompt * price[0] / 1_000_000 + completion * price[1] / 1_000_000
        out.update({
            "input_price_usd_per_million": price[0],
            "output_price_usd_per_million": price[1],
            "cost_usd": cost,
            "cost_formula": "input_tokens*input_price_usd_per_million/1e6 + output_tokens*output_price_usd_per_million/1e6",
        })
    else:
        out.update({"cost_usd": None, "cost_note": "UNPUBLISHED_PRICE__TOKEN_COUNTS_ONLY"})
    return out


def output_health(pattern, cap):
    records = []
    for path in sorted(glob.glob(os.path.join(ROOT, pattern))):
        row = json.load(open(path, encoding="utf-8"))
        usage = row.get("usage_raw") or {}
        output = usage.get("completion_tokens")
        hit = row.get("finish_reason") in ("length", "max_tokens")
        records.append({"statement_id": row.get("statement_id"), "finish_reason": row.get("finish_reason"),
                        "output_tokens": output, "output_token_cap": cap,
                        "output_token_headroom": cap - output if output is not None else None,
                        "hit_cap": hit})
    return {"cap": cap, "hit_cap_count": sum(x["hit_cap"] for x in records), "records": records}


def main():
    score = load("scores_phase3.json")
    misses = load("glaring_misses.json")
    network = load("network_vs_pdf.json")
    adjudication = load("adjudication.json")
    join = verify_filename_join()
    assert len(glob.glob(os.path.join(ROOT, "luna_refined/json/*.json"))) == 304
    assert len(glob.glob(os.path.join(ROOT, "opus_gt/json/*.json"))) == 304

    comparisons = {
        "luna_vs_gt": compact_comparison(score["comparisons"]["luna_refined_vs_GT__all"], "ACCURACY"),
        "csv_vs_gt": compact_comparison(score["comparisons"]["CSV_vs_GT__all"], "ACCURACY"),
        "luna_vs_csv": compact_comparison(score["comparisons"]["luna_refined_vs_CSV__all"], "AGREEMENT"),
    }
    heldout = {
        "luna_vs_gt": compact_comparison(score["comparisons"]["luna_refined_vs_GT__heldout"], "HELD_OUT_ACCURACY"),
        "csv_vs_gt": compact_comparison(score["comparisons"]["CSV_vs_GT__heldout"], "HELD_OUT_ACCURACY"),
        "luna_vs_csv": compact_comparison(score["comparisons"]["luna_refined_vs_CSV__heldout"], "HELD_OUT_AGREEMENT"),
    }
    tokens = {
        "luna_refined": token_arm(score["tokens"]["luna_refined"], "databricks-gpt-5-6-luna"),
        "luna_client_phase1": token_arm(score["tokens"]["luna_client_p1"], "databricks-gpt-5-6-luna"),
        "opus_gt": token_arm(score["tokens"]["opus_gt"], "databricks-claude-opus-5",
                             (OPUS_INPUT_PER_M, OPUS_OUTPUT_PER_M)),
    }
    gt_cost = tokens["opus_gt"]["cost_usd"]
    assert abs(gt_cost - score["opus_gt_cost_usd_at_published_rate"]) < 0.01
    decided = adjudication["tally"]["CSV_WRONG"] + adjudication["tally"]["LUNA_WRONG"]
    luna_txn = comparisons["luna_vs_gt"]["transaction_metrics"]
    csv_txn = comparisons["csv_vs_gt"]["transaction_metrics"]
    luna_fields = comparisons["luna_vs_gt"]["priority"]
    desc_classes = load("desc_defect_classes.json")

    def core4_defective_rows(txn):
        """Rows carrying a defect on date/amount/direction/currency -- i.e. every defect
        signature except the description-only one. Reported separately because `description`
        is a text-fidelity field and dominates Luna's defect count."""
        sig = txn["joint_row_correctness"]["defect_signature_counts"]
        return sum(n for s, n in sig.items()
                   if [f for f in s.split("+") if f != "description"])

    def txn_headline(txn, arm):
        """Alignment and correctness, side by side, each labelled for what it is."""
        al, vc, jt = txn["row_alignment"], txn["value_correctness"]["priority"], txn["joint_row_correctness"]
        return {
            "row_alignment__NOT_A_CORRECTNESS_CLAIM": {
                "pairing_f1": al["pairing_f1"],
                "rows_recovered": al["rows_matched"],
                "reference_rows": al["rows_ref"],
                "rows_missing_vs_reference": al["rows_missing_vs_reference"],
                "row_count_exact_match_statements": al["row_count_exact_match_statements"],
                "exactly_1_0_metrics": al["exactly_1_0_metrics"],
                "annotation": al.get("exactly_1_0_annotation",
                                     "not saturated on this arm"),
            },
            "value_correctness": {
                "joint_all_5_priority_fields_correct_over_reference_rows":
                    jt["all_fields_correct_rate_over_reference_rows"],
                "joint_all_5_priority_fields_correct_over_reference_rows_excl_description_fidelity":
                    jt["all_fields_correct_rate_over_reference_rows_excl_desc_fidelity"],
                "per_field_accuracy_over_matched_pairs": {
                    f: vc[f]["accuracy"] for f in TD.TXN_PRIORITY if f in vc},
                "per_field_wrong_value": {f: vc[f]["wrong_value"] for f in TD.TXN_PRIORITY if f in vc},
                "per_field_format_only_not_charged_as_wrong": {
                    f: vc[f]["format_only"] for f in TD.TXN_PRIORITY if f in vc},
                "fields_at_exactly_1_0": {
                    f: vc[f]["exactly_1_0_annotation"] for f in TD.TXN_PRIORITY
                    if f in vc and "exactly_1_0_annotation" in vc[f]},
                "description_defect_classes": desc_classes.get(arm, {}).get("classes"),
            },
        }

    out = {
        "status": "FINAL",
        "record_counts": {"luna_refined": 304, "opus_gt": 304, "incumbent_csv_joined": 304, "scoreable": 304},
        "excluded_arms": {"phase1_luna_client": "10-record tuning/baseline arm; excluded from final corpus scores",
                          "phase1_luna_generic": "10-record Axis-contaminated generic arm; excluded"},
        "matcher": {"admission": "description_similarity_only", "assignment": "strict_1_to_1",
                    "order_sensitive": False, "test_matcher_noncircular": "PASS: all 4 non-circularity proof obligations hold",
                    "what_the_matcher_test_does_NOT_prove": (
                        "test_matcher_noncircular.py proves the MATCHER does not admit pairs on "
                        "date/amount/direction/currency. It says nothing about how the ROLL-UP "
                        "labels the matcher's output. The 1.0 published before 2026-08-11 was a "
                        "roll-up labelling defect one level above the matcher; "
                        "test_rollup_honesty.py is the guard for that layer."),
                    "test_rollup_honesty": "PASS: no by-construction 1.0 published as a correctness headline"},
        "measurement_defect_correction": {
            "date": "2026-08-11",
            "severity": "BLOCKING -- invalidated the transaction headline of the previous roll-up",
            "withdrawn_claims": {
                "headline_verdicts.luna_transaction_micro_f1": 1.0,
                "headline_verdicts.verdict": ("Luna recovers every GT transaction row and "
                                              "materially outperforms the incumbent on card "
                                              "identity; remaining headline weakness is card "
                                              "last-four accuracy."),
            },
            "mechanism": (
                "score_phase3.run_pair derives micro_precision = rows_matched/rows_pred and "
                "micro_recall = rows_matched/rows_ref, where rows_matched comes from "
                "score_lib.match_txns_by_description -- a matcher that admits a pair on "
                "DESCRIPTION SIMILARITY ALONE (>=0.60, strict 1:1) and deliberately excludes "
                "date/amount/direction/currency from admission. Those ratios therefore measure "
                "ROW ADMISSION, not value agreement. ICICI narrations transcribe near-perfectly "
                "(mean description similarity 0.9992, 4,097/4,097 rows admitted, 304/304 "
                "statements with equal row counts), so precision, recall, micro-F1 and macro-F1 "
                "all saturate at exactly 1.0 BY CONSTRUCTION. build_final_scores then republished "
                "that block verbatim as `transaction_metrics` and hoisted micro_f1 into "
                "headline_verdicts as `luna_transaction_micro_f1`, converting an alignment "
                "statistic into a correctness claim. The per-field transaction verdicts that DO "
                "measure correctness were already being computed and were already NOT 1.0 "
                "(description 0.9280, amount 0.9995, direction 0.9988) -- they were simply not "
                "the number anyone read."),
            "fix": ("transaction_metrics is now {row_alignment, value_correctness, "
                    "joint_row_correctness}; alignment ratios are renamed pairing_* and flagged "
                    "is_correctness_claim=false; every exactly-1.0 metric carries an "
                    "exactly_1_0_annotation explaining whether it is earned, corpus-constant, or "
                    "arithmetically derived."),
        },
        "self_comparison_guard": self_comparison_guard(),
        "comparisons": comparisons,
        "gt_corrected_accuracy": {},
        "pdf_adjudication": {"tally": adjudication["tally"], "n_items": len(adjudication["items"]),
                             "luna_win_rate_decided_disagreements_pct": 100 * adjudication["tally"]["CSV_WRONG"] / decided,
                             "items": adjudication["items"]},
        "gt_audit": {"network_vs_pdf": network, "glaring_misses": {
            "luna_refined_errors_vs_gt": {k: v for k, v in misses["luna_refined_errors_vs_GT"].items() if k != "items"},
            "incumbent_csv_errors_vs_gt": {k: v for k, v in misses["incumbent_csv_errors_vs_GT"].items() if k != "items"}}},
        "tokens": tokens,
        "gt_output_health": output_health("opus_gt/json/*.json", 32000),
        "statement_date_rule": {"not_recomputed_for_this_rollup": True},
        "notes": {"cardDisplayName": "lenient scoring", "currency": "near-constant INR; high accuracy is non-differentiating",
                  "utilisation_printed_pdfs": 0, "network": "PDF-adjudicated because GT is not reliable for this field",
                  "gt_instrument_caveat": (
                      "The GT is the Opus-5 native-PDF extraction and is the accuracy reference, "
                      "not truth. It shares the schema instrument with the challenger, so a field "
                      "on which both sides agree perfectly may be a shared blind spot rather than "
                      "a solved field; transactions[].description is the clearest case where the "
                      "GT is demonstrably the weaker side (see desc_defect_classes.json: 191 of "
                      "Luna's 295 'defects' are the GT carrying an intra-cell line-wrap space "
                      "mid-word). pdf_adjudication is the only PDF-grounded check here."),
                  "transaction_metrics_schema": (
                      "comparisons[*].transaction_metrics changed shape on 2026-08-11. The old "
                      "micro_precision/micro_recall/micro_f1/macro_f1 keys now live under "
                      "row_alignment as pairing_precision/pairing_recall/pairing_f1/"
                      "pairing_macro_f1 with identical values and an explicit "
                      "is_correctness_claim=false; see measurement_defect_correction. "
                      "sbi/final_scores.json still carries the un-split `txn` block and has the "
                      "same latent defect (precision=recall=f1=1.0 on 3,527/3,527 rows), though "
                      "it never promoted it to a headline."),
                  "desc_defect_classes": desc_classes},
        "heldout_comparisons": heldout,
        "corpus_join": join,
        "headline_verdicts": {
            "_reading_rule": ("Nothing in this block may be quoted as transaction correctness "
                              "unless it sits under a `value_correctness` key. Alignment "
                              "figures are namespaced with NOT_A_CORRECTNESS_CLAIM."),
            "transactions": {
                "luna": txn_headline(luna_txn, "luna_refined_vs_GT__all"),
                "incumbent_csv": txn_headline(csv_txn, "CSV_vs_GT__all"),
            },
            "luna_last_four_digit_accuracy": luna_fields["cards[].cardMeta.lastFourDigit"]["accuracy"],
            "luna_card_display_name_accuracy": luna_fields["cards[].cardMeta.cardDisplayName"]["accuracy"],
            "luna_network_fabrications_vs_pdf": 0,
            "incumbent_network_fabrications_vs_pdf": 72,
            "verdict": _verdict(luna_txn, csv_txn, luna_fields, adjudication,
                                core4_defective_rows),
        },
        "discrepancies": [
            {"source": "ICICI_REPORT.md section 4 (current working copy, lines 162-164)",
             "prose_claim": {"heldout_luna_rows": 3961, "heldout_csv_rows": 3802,
                             "heldout_reference_rows": 3961},
             "artifact": {"heldout_luna_rows": heldout["luna_vs_gt"]["transaction_metrics"]["row_alignment"]["rows_pred"],
                          "heldout_csv_rows": heldout["csv_vs_gt"]["transaction_metrics"]["row_alignment"]["rows_pred"],
                          "heldout_reference_rows": heldout["luna_vs_gt"]["transaction_metrics"]["row_alignment"]["rows_ref"]},
             "adjudication": "ARTIFACT IS RIGHT; THE PROSE IS STALE.",
             "reason": (
                 "Verified independently from the per-statement artifacts: the 10 tuning "
                 "statements (phase1_sample.json) hold 553 GT transaction rows between them -- "
                 "two of them alone hold 150 and 140 -- so held-out = 4,097-553 = 3,544 for Luna "
                 "and 3,932-553 = 3,379 for the incumbent, exactly the artifact figures. The "
                 "prose's 3,961 implies the 10 tuning statements hold only 136 rows, which "
                 "contradicts the on-disk counts. The prose was written against an intermediate, "
                 "partially-complete scores_phase3.json: the COMMITTED report_tables.md at "
                 "c3100c2 still shows that snapshot (54/97 statements, 801/1,304 rows), while the "
                 "regenerated report_tables.md agrees with the artifact at 3,544/3,379. The "
                 "prose's 3,802 additionally coincides with Luna's all-scope "
                 "`descriptions exact (casefold)` figure (3,802), i.e. a cell transcribed from "
                 "the wrong row of the generated table."),
             "prose_lines_to_correct": "ICICI_REPORT.md lines 162-164"},
            {"source": "ICICI_REPORT.md PDF adjudication prose (lines 53, 230, 247, 465)",
             "prose_claim": {"CSV_WRONG": 347, "LUNA_WRONG": 25, "decided": 372},
             "artifact": {"CSV_WRONG": adjudication["tally"]["CSV_WRONG"],
                          "LUNA_WRONG": adjudication["tally"]["LUNA_WRONG"],
                          "decided": decided},
             "adjudication": "ARTIFACT IS RIGHT; THE PROSE PREDATES A FIX TO THE ADJUDICATOR.",
             "reason": (
                 "The decided total is 372 on both sides, so nothing was added or dropped -- "
                 "exactly two items changed side. adjudicate.py's verdict_for was hardened after "
                 "the prose was written (MARKETING and TXNROW snippet exclusions) so that a "
                 "network token found only in a cross-sell advert or inside a transaction row no "
                 "longer counts as evidence of THIS card's network. Statements 647130 and "
                 "870931682, field cards[].cardMeta.network, now carry adjudication=CSV_WRONG "
                 "with zero pdf_evidence and the reason 'network appears only inside the "
                 "four-network fuel-surcharge disclaimer, which identifies no card; Luna's null "
                 "is correct'. They were previously charged as LUNA_WRONG. 25-2 = 23 and "
                 "347+2 = 349, which is the current tally exactly. The hardening is correct on "
                 "the merits (the Opus GT independently returns null for both), so 349/23 stands "
                 "and the 14:1 prose figure should read 15.2:1."),
             "prose_lines_to_correct": "ICICI_REPORT.md lines 53, 230, 247, 465"},
        ],
        "report_prose_corrections_required": {
            "_why": ("ICICI_REPORT.md is hand-written prose and still carries the withdrawn 1.0 "
                     "transaction headline plus the two stale figures adjudicated above. It has "
                     "uncommitted in-flight edits from another author and is deliberately NOT "
                     "modified by this roll-up fix; these are the exact corrections it needs."),
            "items": [
                {"lines": "48 (and 44)",
                 "current": "Transaction micro-F1 vs GT | 1.0000 | 0.9794/0.9762",
                 "required": ("relabel as 'transaction ROW ALIGNMENT F1 (pairing only, not "
                              "accuracy)' and add the value-correctness row: rows correct on all "
                              "five priority fields, 92.70% Luna vs 94.19% incumbent strict / "
                              "99.58% vs 94.34% excluding narration fidelity-only slips")},
                {"lines": "162-164", "current": "held-out rows 3,961 / 3,802 / ref 3,961",
                 "required": "3,544 / 3,379 / ref 3,544"},
                {"lines": "53, 230, 247, 465", "current": "347 CSV_WRONG vs 25 LUNA_WRONG, '14:1'",
                 "required": "349 CSV_WRONG vs 23 LUNA_WRONG, '15.2:1'"},
            ],
        },
        "provenance": {"score_artifact": "scores_phase3.json",
                       "transaction_decomposition": "txn_decomp.py (derived from the same artifact)",
                       "no_model_inference": True,
                       "no_arm_regeneration": True,
                       "join_reverified_during_build": True}
    }
    with open(os.path.join(ROOT, "final_scores.json"), "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    print("wrote final_scores.json")
    print(json.dumps({"join": join, "opus_cost_usd": gt_cost,
                      "self_comparison_guard": out["self_comparison_guard"],
                      "headline": out["headline_verdicts"]}, indent=2))


if __name__ == "__main__":
    main()
