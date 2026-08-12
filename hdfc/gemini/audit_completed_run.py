"""Local-only integrity and causal-control audit of the two completed Luna arms."""
import glob, json, os, re, sys
from collections import Counter

sys.path.insert(0, os.path.dirname(__file__))
import pdf_rows as P

HERE = os.path.dirname(__file__)

def load_arm(arm):
    return {x["statement_id"]: x for f in glob.glob(f"{HERE}/json_{arm}/*.json")
            for x in [json.load(open(f))]}

def main():
    arms = {a: load_arm(a) for a in ("hdfc", "generic")}
    pdf = {P.statement_id(f): P.extract(p) for _, f, p in P.corpus()}
    analysis = json.load(open(f"{HERE}/analysis.json"))
    out = {"calls": {}, "generic_direction": {}, "controls": {}}
    for arm, records in arms.items():
        usages = Counter()
        additive = 0
        sanitized = 0
        statuses = Counter()
        finishes = Counter()
        rate_limited = 0
        for r in records.values():
            u = r["usage_raw"]
            for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
                usages[k] += u.get(k, 0)
            additive += u.get("prompt_tokens", 0) + u.get("completion_tokens", 0) == u.get("total_tokens", 0)
            sanitized += r.get("filename_sent_to_model") == "statement.pdf"
            statuses[str((r.get("meta") or {}).get("http_status"))] += 1
            finishes[str(r.get("finish_reason"))] += 1
            rate_limited += (r.get("meta") or {}).get("rate_limited", 0)
        out["calls"][arm] = {"count": len(records), "outcomes": dict(Counter(r.get("outcome") for r in records.values())),
            "failure_classes": dict(Counter(str(r.get("failure_class")) for r in records.values())),
            "http_statuses": dict(statuses), "finish_reasons": dict(finishes), "rate_limited": rate_limited,
            "usage_raw_totals": dict(usages), "prompt_plus_completion_equals_total_calls": additive,
            "neutral_filename_calls": sanitized, "completion_to_prompt_ratio": usages["completion_tokens"] / usages["prompt_tokens"]}

    called_credit = true_credit = errors = debit_called_credit = debit_called_credit_itf = 0
    by_layout = {"A": Counter(), "B": Counter()}
    for sid, rec in arms["generic"].items():
        rows = (rec.get("parsed_json") or {}).get("transactions") or []
        refs = pdf[sid]["rows"]
        assert len(rows) == len(refs)
        lay = pdf[sid]["layout"]
        for m, p in zip(rows, refs):
            mc = m.get("direction") == "CREDIT"
            tc = p["direction"] == "CREDIT"
            called_credit += mc; true_credit += tc
            wrong = mc != tc; errors += wrong
            dc = mc and not tc; debit_called_credit += dc
            debit_called_credit_itf += dc and p["currency_marker"] == "ITFRupee_C"
            by_layout[lay]["rows"] += 1; by_layout[lay]["errors"] += wrong
    out["generic_direction"] = {"claimed_credit": called_credit, "true_credit": true_credit,
        "errors": errors, "debit_called_credit": debit_called_credit,
        "debit_called_credit_with_itfrupee_C": debit_called_credit_itf}
    out["controls"]["direction_by_layout"] = {k: dict(v) for k, v in by_layout.items()}
    out["overlap"] = {"opus5_gt_statements": analysis["gt_overlap"],
                      "prior_run_unattributable_statements": analysis["prior_overlap"]}
    json.dump(out, open(f"{HERE}/completed_run_audit.json", "w"), indent=2, sort_keys=True)

if __name__ == "__main__": main()
