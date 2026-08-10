#!/usr/bin/env python3
"""Phase 3 three-way scoring.

  GT         = Opus-5 native PDF, GT_PROMPT/GT_SCHEMA unchanged (shared instrument)
  Incumbent  = the CSV (Gemini). NOT ground truth.
  Challenger = Luna, refined HDFC prompt.

Reported strictly as:
  Luna-vs-GT  / CSV-vs-GT   -> ACCURACY
  Luna-vs-CSV               -> AGREEMENT (adjudicated against the PDF separately)

Transaction pairing is DESCRIPTION-ONLY 1:1 (score_lib.match_transactions); date,
amount, direction and currency never enter the matcher, so their per-field numbers
are real measurements rather than artefacts of the pairing.

Every metric is emitted twice: over ALL scoreable statements and over the HELD-OUT
set (all statements minus the 10 tuned on), because the prompt was tuned on 10 and
tested on ~271 -- a 27x extrapolation.
"""
import json
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hdfc_lib as H
import score_lib as S

HERE = os.path.dirname(os.path.abspath(__file__))


def med(xs):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def token_stats(run, label):
    """Reasoning INSIDE vs OUTSIDE completion is DETERMINED here, not assumed."""
    rows, additive, non_additive = [], 0, 0
    for sid, r in run.items():
        u = r.get("usage_raw") or {}
        p, c, t = u.get("prompt_tokens"), u.get("completion_tokens"), u.get("total_tokens")
        det = u.get("completion_tokens_details") or {}
        reas = det.get("reasoning_tokens")
        if None not in (p, c, t):
            if p + c == t:
                additive += 1
            else:
                non_additive += 1
        rows.append({"sid": sid, "in": p, "out": c, "total": t, "reasoning": reas})
    ins = [x["in"] for x in rows]
    outs = [x["out"] for x in rows]
    tots = [x["total"] for x in rows]
    res = [x["reasoning"] for x in rows if x["reasoning"] is not None]
    reasoning_inside = None
    if res and outs:
        # If reasoning were OUTSIDE completion, completion would be smaller than
        # reasoning on at least some calls. Test it rather than assert it.
        reasoning_inside = all(
            (x["reasoning"] or 0) <= (x["out"] or 0) for x in rows if x["reasoning"] is not None)
    return {
        "label": label,
        "n_calls": len(rows),
        "prompt_plus_completion_equals_total": additive,
        "non_additive": non_additive,
        "input_total": sum(v for v in ins if v),
        "output_total": sum(v for v in outs if v),
        "reasoning_total": sum(res) if res else 0,
        "grand_total": sum(v for v in tots if v),
        "input_mean": round(sum(ins) / len(ins), 1) if ins else None,
        "input_median": med(ins), "input_max": max(ins) if ins else None,
        "output_mean": round(sum(outs) / len(outs), 1) if outs else None,
        "output_median": med(outs), "output_max": max(outs) if outs else None,
        "reasoning_mean": round(sum(res) / len(res), 1) if res else None,
        "reasoning_median": med(res), "reasoning_max": max(res) if res else None,
        "reasoning_reported_on_n_calls": len(res),
        "reasoning_nested_inside_completion": reasoning_inside,
    }


def score_pair(pred_run, gold_lookup, sids, label, is_csv_gold=False):
    """pred vs gold over `sids`. gold_lookup(sid) -> extraction dict or None."""
    stmt = {name: S.FieldTally() for name, _, _, _ in S.STMT_FIELDS}
    stmt["statementLevelSummary.utilisationPercent_DERIVED"] = S.FieldTally()
    txn = {f: S.FieldTally() for f in S.TXN_FIELDS}
    tp = fp = fn = 0
    desc_sims = []
    scored = 0
    per_stmt = []

    for sid in sids:
        pr = pred_run.get(sid)
        gold = gold_lookup(sid)
        if gold is None:
            continue
        pred = (pr or {}).get("parsed_json")
        if not isinstance(pred, dict):
            continue
        scored += 1
        for name, scope, path, kind in S.STMT_FIELDS:
            stmt[name].add(kind, S.get_field(pred, scope, path),
                           S.get_field(gold, scope, path), sid)
        stmt["statementLevelSummary.utilisationPercent_DERIVED"].add(
            "num", S.derived_utilisation(pred), S.derived_utilisation(gold), sid)

        pairs, un_p, un_g = S.match_transactions(pred.get("transactions"),
                                                 gold.get("transactions"))
        tp += len(pairs)
        fp += len(un_p)
        fn += len(un_g)
        for i, j, _sim in pairs:
            pt = pred["transactions"][i] or {}
            gt = gold["transactions"][j] or {}
            # Per-field comparison uses txn_field_equal (not FieldTally.add), because
            # transaction fields have their own comparison semantics.
            for f in S.TXN_FIELDS:
                t = txn[f]
                t.n += 1
                if S.txn_field_equal(f, pt.get(f), gt.get(f)):
                    t.correct += 1
                else:
                    pv, gv = pt.get(f), gt.get(f)
                    pn = pv in (None, "")
                    gn = gv in (None, "")
                    if gn and not pn:
                        t.hallucinated_when_gold_null += 1
                        tag = "HALLUCINATED"
                    elif pn and not gn:
                        t.null_when_populated += 1
                        tag = "NULL_WHEN_POPULATED"
                    else:
                        t.wrong_value += 1
                        tag = "WRONG_VALUE"
                    if len(t.examples) < 60:
                        t.examples.append({"sid": sid, "tag": tag, "pred": pv, "gold": gv,
                                           "pred_desc": pt.get("description"),
                                           "gold_desc": gt.get("description")})
            desc_sims.append(S.desc_fidelity(pt.get("description"), gt.get("description")))
        per_stmt.append({"sid": sid, "pred_txn": len(pred.get("transactions") or []),
                         "gold_txn": len(gold.get("transactions") or []),
                         "pairs": len(pairs), "pred_only": len(un_p), "gold_only": len(un_g)})

    prec = tp / (tp + fp) if (tp + fp) else None
    rec = tp / (tp + fn) if (tp + fn) else None
    f1 = (2 * prec * rec / (prec + rec)) if (prec and rec) else None
    return {
        "label": label,
        "comparison": "AGREEMENT (incumbent CSV is not ground truth)" if is_csv_gold
                      else "ACCURACY (vs Opus-5 GT)",
        "statements_scored": scored,
        "statement_fields": {k: v.as_dict() for k, v in stmt.items()},
        "transaction_fields": {k: v.as_dict() for k, v in txn.items()},
        "transaction_matching": {
            "matched_pairs": tp, "pred_only_false_pos": fp, "gold_only_false_neg": fn,
            "precision": round(prec, 4) if prec else None,
            "recall": round(rec, 4) if rec else None,
            "f1": round(f1, 4) if f1 else None,
            "description_exact_match_rate": round(
                sum(1 for s in desc_sims if s == 1.0) / len(desc_sims), 4) if desc_sims else None,
            "description_mean_similarity": round(sum(desc_sims) / len(desc_sims), 4)
                                           if desc_sims else None,
        },
        "per_statement": per_stmt,
    }


def outcome_tally(run):
    return dict(Counter(r.get("outcome") for r in run.values()))


def main():
    matched, unmatched, orphans = H.build_join()
    prof = json.load(open(os.path.join(HERE, "corpus_profile.json")))
    tune = {p["sid"] for p in prof["sample"]}

    runs = {}
    for label, d in (("luna_refined", "phase3_refined"),
                     ("luna_generic_full", "phase3_generic"),
                     ("luna_generic_sample", "phase1_baseline"),
                     ("luna_refined_sample", "phase2_refined"),
                     ("gt_opus", "gt_full")):
        runs[label] = S.load_run(os.path.join(HERE, d))

    gt = runs["gt_opus"]
    csv_by_sid = {m["sid"]: S.csv_extraction(m["csv_row"]) for m in matched}
    all_sids = [m["sid"] for m in matched]

    def gt_gold(sid):
        r = gt.get(sid)
        pj = (r or {}).get("parsed_json")
        return pj if isinstance(pj, dict) and r.get("failure_class") in (None, "cap") else None

    def csv_gold(sid):
        return csv_by_sid.get(sid)

    gt_ok = [s for s in all_sids if gt_gold(s) is not None]
    heldout = [s for s in gt_ok if s not in tune]
    heldout_all = [s for s in all_sids if s not in tune]

    out = {
        "corpus": {
            "pdfs_on_disk": len(H.discover_pdfs()),
            "csv_data_rows": len(H.csv_rows()),
            "joined_scoreable": len(matched),
            "csv_rows_unmatched": len(unmatched),
            "pdfs_without_csv_row": len(orphans),
            "note": ("join reaches 281/300 once the CSV link basename is URL-DECODED; "
                     "the 19 non-joining CSV rows are exactly the 19 entries of "
                     "failed-download-links.txt (PDFs never downloaded)"),
        },
        "tuning_sample": sorted(tune),
        "gt_usable_statements": len(gt_ok),
        "outcomes": {k: outcome_tally(v) for k, v in runs.items() if v},
        "tokens": {k: token_stats(v, k) for k, v in runs.items() if v},
        "scores": {},
    }

    # Opus GT cost at published rate (Luna price unpublished -> tokens only)
    gtt = out["tokens"].get("gt_opus")
    if gtt:
        out["gt_opus_cost_usd_published_rate"] = round(
            gtt["input_total"] / 1e6 * 5.00 + gtt["output_total"] / 1e6 * 25.00, 2)

    for label, run in (("luna_refined", runs["luna_refined"]),
                       ("luna_generic_full", runs["luna_generic_full"])):
        if not run:
            continue
        out["scores"][f"{label}_vs_GT__all"] = score_pair(run, gt_gold, gt_ok, f"{label} vs GT (all)")
        out["scores"][f"{label}_vs_GT__heldout"] = score_pair(
            run, gt_gold, heldout, f"{label} vs GT (held-out)")
        out["scores"][f"{label}_vs_CSV__all"] = score_pair(
            run, csv_gold, all_sids, f"{label} vs CSV (all)", is_csv_gold=True)
        out["scores"][f"{label}_vs_CSV__heldout"] = score_pair(
            run, csv_gold, heldout_all, f"{label} vs CSV (held-out)", is_csv_gold=True)

    # the incumbent measured on the SAME instrument
    csv_run = {sid: {"parsed_json": x, "outcome": "OK"} for sid, x in csv_by_sid.items()}
    out["scores"]["CSV_vs_GT__all"] = score_pair(csv_run, gt_gold, gt_ok, "CSV vs GT (all)")
    out["scores"]["CSV_vs_GT__heldout"] = score_pair(csv_run, gt_gold, heldout,
                                                     "CSV vs GT (held-out)")

    H.G.atomic_write_json(os.path.join(HERE, "scores_phase3.json"), out)

    print(f"corpus: {out['corpus']['joined_scoreable']} scoreable, GT usable {len(gt_ok)}, "
          f"held-out {len(heldout)}")
    for k, v in out["outcomes"].items():
        print(f"  outcomes {k:22s} {v}")
    print()
    for key in ["luna_refined_vs_GT__all", "CSV_vs_GT__all",
                "luna_generic_full_vs_GT__all",
                "luna_refined_vs_GT__heldout", "CSV_vs_GT__heldout"]:
        s = out["scores"].get(key)
        if not s:
            continue
        tm = s["transaction_matching"]
        print(f"== {key}  (n={s['statements_scored']})  P={tm['precision']} R={tm['recall']} "
              f"F1={tm['f1']} descExact={tm['description_exact_match_rate']}")
        for f, d in s["statement_fields"].items():
            print(f"     {f:52s} acc={d['accuracy']} n={d['n']} wrong={d['wrong_value']} "
                  f"null={d['null_when_populated']} halluc={d['hallucinated_when_gold_null']}")
        for f, d in s["transaction_fields"].items():
            print(f"     txn.{f:48s} acc={d['accuracy']} n={d['n']} wrong={d['wrong_value']} "
                  f"null={d['null_when_populated']} halluc={d['hallucinated_when_gold_null']}")
        print()


if __name__ == "__main__":
    main()
