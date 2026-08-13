"""Per-field, per-arm scoring of the three SBI arms against the client's incumbent record.

WHAT THE ORACLE IS, AND WHAT IT IS NOT
-------------------------------------
The comparator is the `data` blob in `sbi.csv`. Those rows carry
`modelName: gemini-3-flash-preview` / `databricks-gemini-3-flash` and
`detectionSource: GEMINI`, so the blob is the CLIENT'S INCUMBENT MODEL OUTPUT, not a
human-verified ground truth. It is the contract we are asked to match, and it is
scored as such -- but a cell where the incumbent is contradicted by the PDF is a
GT_DEFECT, not an arm failure, and adjudicate_5fields.py records those separately.
Nothing here should be read as "accuracy against truth".

REPORTING RULES THIS FILE ENFORCES (conflating these would mislead)
------------------------------------------------------------------
* NO ORACLE      -- the incumbent is null on all 12 statements, so agreement is
                    unmeasurable. Reported as a POPULATED-ONLY count, never as accuracy.
* NON-DISCRIMINATING -- all three arms emit identical values on all 12; the field
                    cannot separate the arms whatever its score.
* UNEARNED       -- the oracle is trivially uniform (one distinct value across the
                    whole corpus), so a high score reflects the corpus, not the prompt.
* BOTH-NULL cells are counted and shown SEPARATELY from substantive agreement, because
  a field that is correctly null 12/12 has a 100% score and zero information.
"""

import argparse
import csv
import json
import os
import re
import sys
from collections import Counter, defaultdict

csv.field_size_limit(10 ** 9)

HERE = os.path.dirname(os.path.abspath(__file__))
GT_CSV = "/Users/mayanck.bihani/Downloads/remaining pdfs ground truth/sbi.csv"
ARMS = ["A", "B", "C"]

SCALAR_LEAVES = [
    "statementMeta.issuerName", "statementMeta.statementDate", "statementMeta.dueDate",
    "statementLevelSummary.totalAmountDue", "statementLevelSummary.totalMinimumAmountDue",
    "statementLevelSummary.totalCreditLimit", "statementLevelSummary.availableCreditLimit",
    "cards[].cardMeta.cardDisplayName", "cards[].cardMeta.productFamily",
    "cards[].cardMeta.lastFourDigit", "cards[].cardMeta.network",
    "cards[].cardMeta.isPrimaryCard",
    "rewards.programType", "rewards.openingPoints", "rewards.pointsEarnedThisCycle",
    "rewards.pointsRedeemedThisCycle", "rewards.closingPoints",
    "rewards.pointsExpiringNext30Days", "rewards.pointsExpiringNext60Days",
]
TXN_LEAVES = ["date", "description", "amount", "direction", "txnType",
              "rewardPointsOnThisTransaction", "currency"]

DATE_LEAVES = {"statementMeta.statementDate", "statementMeta.dueDate"}
NUM_LEAVES = {l for l in SCALAR_LEAVES
              if l.startswith("statementLevelSummary.") or l.startswith("rewards.")} - {
    "rewards.programType"}

_MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}


def norm_date(v):
    """Everything to DD/MM/YYYY. A format difference is NOT a wrong day."""
    if v is None:
        return None
    s = str(v).strip()
    m = re.match(r"^(\d{1,2})[/\-\s]([A-Za-z]{3,})[/\-\s](\d{2,4})$", s)
    if m and m.group(2)[:3].lower() in _MONTHS:
        d, mo, y = int(m.group(1)), _MONTHS[m.group(2)[:3].lower()], int(m.group(3))
        return f"{d:02d}/{mo:02d}/{2000 + y if y < 100 else y}"
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return f"{int(m.group(3)):02d}/{int(m.group(2)):02d}/{m.group(1)}"
    m = re.match(r"^(\d{1,2})[/\-](\d{1,2})[/\-](\d{2,4})$", s)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return f"{d:02d}/{mo:02d}/{2000 + y if y < 100 else y}"
    return s.upper()


def norm_num(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return round(float(v), 2)
    s = str(v).replace(",", "").replace("`", "").replace("₹", "").strip()
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()").replace("CR", "").replace("Dr", "").strip()
    try:
        f = float(s)
    except ValueError:
        return str(v).strip().upper()
    return round(-f if neg else f, 2)


def norm_txt(v):
    if v is None:
        return None
    return re.sub(r"\s+", " ", str(v)).strip().upper()


def normalise(leaf, v):
    if leaf in DATE_LEAVES:
        return norm_date(v)
    if leaf in NUM_LEAVES:
        return norm_num(v)
    return norm_txt(v)


def dig(obj, leaf):
    """Read a dotted leaf path, treating `cards[]` as 'the first card'."""
    node = obj
    for part in leaf.replace("[]", "").split("."):
        if node is None:
            return None
        if isinstance(node, list):
            node = node[0] if node else None
            if node is None:
                return None
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    if isinstance(node, list):
        node = node[0] if node else None
    return node


# ------------------------------------------------------------------ loading
def load_gt():
    out = {}
    for r in csv.DictReader(open(GT_CSV, encoding="utf-8", errors="replace")):
        m = re.search(r"decrypt_(?:encrypt_)?(\d+)_", str(r.get("link", "")))
        if not m:
            continue
        d = r.get("data") or "{}"
        try:
            d = json.loads(d)
            if isinstance(d, str):
                d = json.loads(d)
        except Exception:
            d = {}
        if isinstance(d, dict):
            out[m.group(1)] = d
    return out


def load_arm(arm):
    out, infra = {}, {}
    d = os.path.join(HERE, f"json_arm{arm}")
    if not os.path.isdir(d):
        return out, infra
    for f in sorted(os.listdir(d)):
        if not f.endswith(".json"):
            continue
        rec = json.load(open(os.path.join(d, f)))
        sid = rec["sid"]
        if rec.get("failure_class") == "infrastructure":
            infra[sid] = rec.get("outcome")
            continue
        out[sid] = rec
    return out, infra


# ------------------------------------------------------------------ txn matching
def _row_key(r):
    """(amount, description) -- the two fields both sides copy verbatim. The DATE is
    excluded on purpose: it is one of the fields under measurement, so keying on it
    would make the matcher assume the answer."""
    return (norm_num(r.get("amount")), norm_txt(r.get("description")) or "")


def match_txns(gt_rows, arm_rows):
    """ORDER-PRESERVING alignment (LCS) on (amount, description).

    WHY NOT GREEDY -- this was a real measurement defect caught mid-analysis. SBI
    statements repeat the SAME description at the SAME amount on many different dates
    (statement 1707857175 has a long run of 'UPI-REDEFINED PRIVATE L' at 20.00). A
    greedy global match pairs the wrong instances, and when one arm drops a single row
    the whole run shifts by one, manufacturing a cascade of fake date mismatches. The
    first version of this function reported arm A at 93.8% on `date` purely because of
    that cascade. Both sides list transactions in printed order, so alignment must
    preserve order.
    """
    g_keys = [_row_key(r) for r in gt_rows]
    a_keys = [_row_key(r) for r in arm_rows]
    n, m = len(g_keys), len(a_keys)
    # LCS table
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            dp[i][j] = (dp[i + 1][j + 1] + 1) if g_keys[i] == a_keys[j] \
                else max(dp[i + 1][j], dp[i][j + 1])
    pairs, gi, aj = [], 0, 0
    matched_g, matched_a = set(), set()
    while gi < n and aj < m:
        if g_keys[gi] == a_keys[aj]:
            pairs.append((gt_rows[gi], arm_rows[aj]))
            matched_g.add(gi)
            matched_a.add(aj)
            gi += 1
            aj += 1
        elif dp[gi + 1][aj] >= dp[gi][aj + 1]:
            gi += 1
        else:
            aj += 1
    return (pairs,
            [r for i, r in enumerate(gt_rows) if i not in matched_g],
            [r for j, r in enumerate(arm_rows) if j not in matched_a])


# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(HERE, "analysis_3arm.json"))
    a = ap.parse_args()

    gt = load_gt()
    arms, infra = {}, {}
    for k in ARMS:
        arms[k], infra[k] = load_arm(k)
    sids = sorted(set(gt) & set.union(*[set(arms[k]) for k in ARMS]) if
                  all(arms[k] for k in ARMS) else set(gt) & set(arms["A"]))
    print(f"statements scored: {len(sids)}")
    for k in ARMS:
        print(f"  arm {k}: {len(arms[k])} usable records, "
              f"{len(infra[k])} INFRASTRUCTURE failures {infra[k] or ''}")

    res = {"n_statements": len(sids), "sids": sids,
           "oracle": {"source": GT_CSV,
                      "nature": "CLIENT INCUMBENT MODEL OUTPUT (modelName=gemini-3-flash*, "
                                "detectionSource=GEMINI) -- NOT human-verified truth"},
           "infrastructure_failures": infra,
           "scalar": {}, "txn": {}, "tokens": {}, "per_statement": {}}

    # ---------------------------------------------------------- scalar leaves
    for leaf in SCALAR_LEAVES:
        gt_vals = {s: normalise(leaf, dig(gt[s], leaf)) for s in sids}
        row = {"gt_populated": sum(1 for v in gt_vals.values() if v is not None),
               "gt_distinct": len({str(v) for v in gt_vals.values()}),
               "arms": {}}
        arm_vals = {}
        for k in ARMS:
            av = {s: normalise(leaf, dig(arms[k][s].get("parsed_json") or {}, leaf))
                  for s in sids if s in arms[k]}
            arm_vals[k] = av
            agree = sum(1 for s in av if av[s] == gt_vals[s])
            both_null = sum(1 for s in av if av[s] is None and gt_vals[s] is None)
            row["arms"][k] = {
                "n": len(av), "agree": agree,
                "agree_pct": round(100 * agree / len(av), 1) if av else None,
                "both_null": both_null,
                "substantive_agree": agree - both_null,
                "arm_null_gt_populated": sum(
                    1 for s in av if av[s] is None and gt_vals[s] is not None),
                "arm_populated_gt_null": sum(
                    1 for s in av if av[s] is not None and gt_vals[s] is None),
                "both_populated_differ": sum(
                    1 for s in av if av[s] is not None and gt_vals[s] is not None
                    and av[s] != gt_vals[s]),
                "populated": sum(1 for v in av.values() if v is not None),
                "disagreements": sorted(
                    s for s in av if av[s] != gt_vals[s]),
            }
        row["no_oracle"] = row["gt_populated"] == 0
        row["unearned"] = row["gt_distinct"] <= 1
        row["non_discriminating"] = all(
            arm_vals["A"].get(s) == arm_vals["B"].get(s) == arm_vals["C"].get(s)
            for s in sids)
        # REGRESSION GATE: A strictly worse than B
        ra, rb = row["arms"]["A"], row["arms"]["B"]
        if ra["agree"] < rb["agree"]:
            row["REGRESSION_A_vs_B"] = {
                "A_agree": ra["agree"], "B_agree": rb["agree"],
                "sids_A_wrong_B_right": sorted(
                    s for s in arm_vals["A"]
                    if arm_vals["A"][s] != gt_vals[s] and arm_vals["B"].get(s) == gt_vals[s]),
            }
        res["scalar"][leaf] = row

    # ---------------------------------------------------------- transaction leaves
    for leaf in TXN_LEAVES:
        row = {"arms": {}}
        for k in ARMS:
            n = agree = both_null = 0
            gt_pop = 0
            for s in sids:
                if s not in arms[k]:
                    continue
                g = (gt[s].get("transactions") or [])
                p = ((arms[k][s].get("parsed_json") or {}).get("transactions") or [])
                pairs, ug, ua = match_txns(g, p)
                for gr, ar in pairs:
                    gv = norm_date(gr.get(leaf)) if leaf == "date" else (
                        norm_num(gr.get(leaf)) if leaf in ("amount",
                                                           "rewardPointsOnThisTransaction")
                        else norm_txt(gr.get(leaf)))
                    av = norm_date(ar.get(leaf)) if leaf == "date" else (
                        norm_num(ar.get(leaf)) if leaf in ("amount",
                                                           "rewardPointsOnThisTransaction")
                        else norm_txt(ar.get(leaf)))
                    n += 1
                    if gv is not None:
                        gt_pop += 1
                    if gv == av:
                        agree += 1
                        if gv is None:
                            both_null += 1
            row["arms"][k] = {"rows_compared": n, "agree": agree,
                              "agree_pct": round(100 * agree / n, 1) if n else None,
                              "both_null": both_null,
                              "substantive_agree": agree - both_null,
                              "gt_populated": gt_pop}
        row["no_oracle"] = row["arms"]["A"]["gt_populated"] == 0
        ra, rb = row["arms"]["A"], row["arms"]["B"]
        if ra["agree_pct"] is not None and rb["agree_pct"] is not None \
                and ra["agree_pct"] < rb["agree_pct"]:
            row["REGRESSION_A_vs_B"] = {"A_pct": ra["agree_pct"], "B_pct": rb["agree_pct"]}
        res["txn"][leaf] = row

    # ---------------------------------------------------------- row counts
    res["row_counts"] = {}
    for k in ARMS:
        tot_g = tot_a = matched = 0
        per = {}
        for s in sids:
            if s not in arms[k]:
                continue
            g = (gt[s].get("transactions") or [])
            p = ((arms[k][s].get("parsed_json") or {}).get("transactions") or [])
            pairs, ug, ua = match_txns(g, p)
            tot_g += len(g)
            tot_a += len(p)
            matched += len(pairs)
            per[s] = {"gt": len(g), "arm": len(p), "matched": len(pairs),
                      "gt_only": len(ug), "arm_only": len(ua)}
        # ROW FIDELITY -- the honest denominator. `description` above is scored on
        # MATCHED rows only, and rows are matched BY (amount, description); so a row
        # whose description differs cannot match and is therefore INVISIBLE to that
        # score. Without this metric an arm that mistranscribes a description looks
        # like 100% on `description`. Reported alongside it, never instead of it.
        res["row_counts"][k] = {
            "gt_rows": tot_g, "arm_rows": tot_a, "matched": matched,
            "unmatched_gt_rows": tot_g - matched,
            "unmatched_arm_rows": tot_a - matched,
            "row_fidelity_pct": round(100 * matched / tot_g, 1) if tot_g else None,
            "note": "unmatched rows are description or completeness differences that the "
                    "matched-rows-only `description` score cannot see",
            "per_statement": {s: v for s, v in per.items()
                              if v["gt_only"] or v["arm_only"]},
        }

    # ---------------------------------------------------------- tokens
    for k in ARMS:
        pt = ct = tt = rt = cached = 0
        consistent = True
        per = {}
        for s, rec in arms[k].items():
            u = rec.get("usage_raw") or {}
            p, c, t = (u.get("prompt_tokens") or 0, u.get("completion_tokens") or 0,
                       u.get("total_tokens") or 0)
            r = ((u.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0)
            ch = ((u.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)
            pt, ct, tt, rt, cached = pt + p, ct + c, tt + t, rt + r, cached + ch
            # VERIFIED PER CALL, not assumed: is reasoning inside completion, and does
            # prompt+completion == total? Getting this backwards is a ~30% error.
            if p + c != t:
                consistent = False
            per[s] = {"prompt": p, "completion": c, "total": t, "reasoning": r,
                      "cached": ch, "reasoning_inside_completion": r <= c,
                      "prompt_plus_completion_eq_total": p + c == t}
        res["tokens"][k] = {
            "calls": len(arms[k]), "prompt": pt, "completion": ct, "total": tt,
            "reasoning": rt, "cached": cached,
            "cached_pct_of_prompt": round(100 * cached / pt, 2) if pt else None,
            "prompt_plus_completion_eq_total_ALL_CALLS": consistent,
            "reasoning_inside_completion_ALL_CALLS": all(
                v["reasoning_inside_completion"] for v in per.values()),
            "per_statement": per,
        }

    json.dump(res, open(a.out, "w"), indent=1)

    # ------------------------------------------------------------------ print
    print(f"\n{'='*104}\nSCALAR LEAVES -- agreement with the client incumbent "
          f"(both-null shown separately)\n{'='*104}")
    hdr = (f"{'leaf':46s} {'gtPop':>5s} | {'A':>9s} {'B':>9s} {'C':>9s} | "
           f"{'A-sub':>6s} {'B-sub':>6s} {'C-sub':>6s}  flags")
    print(hdr)
    for leaf, row in res["scalar"].items():
        f = []
        if row["no_oracle"]:
            f.append("NO-ORACLE")
        if row["unearned"]:
            f.append("UNEARNED")
        if row["non_discriminating"]:
            f.append("NON-DISCRIM")
        if "REGRESSION_A_vs_B" in row:
            f.append("*** REGRESSION A<B ***")
        aa = [f"{row['arms'][k]['agree']}/{row['arms'][k]['n']}" for k in ARMS]
        sub = [str(row['arms'][k]['substantive_agree']) for k in ARMS]
        print(f"{leaf:46s} {row['gt_populated']:5d} | {aa[0]:>9s} {aa[1]:>9s} {aa[2]:>9s} | "
              f"{sub[0]:>6s} {sub[1]:>6s} {sub[2]:>6s}  {' '.join(f)}")

    print(f"\n{'='*104}\nTRANSACTION LEAVES (matched rows only)\n{'='*104}")
    print(f"{'leaf':30s} {'rows':>6s} {'gtPop':>6s} | {'A':>13s} {'B':>13s} {'C':>13s}  flags")
    for leaf, row in res["txn"].items():
        f = []
        if row["no_oracle"]:
            f.append("NO-ORACLE")
        if "REGRESSION_A_vs_B" in row:
            f.append("*** REGRESSION A<B ***")
        cells = [f"{row['arms'][k]['agree']}/{row['arms'][k]['rows_compared']}"
                 f"={row['arms'][k]['agree_pct']}%" for k in ARMS]
        print(f"{leaf:30s} {row['arms']['A']['rows_compared']:6d} "
              f"{row['arms']['A']['gt_populated']:6d} | "
              f"{cells[0]:>13s} {cells[1]:>13s} {cells[2]:>13s}  {' '.join(f)}")

    print(f"\n{'='*104}\nROW COUNTS\n{'='*104}")
    for k in ARMS:
        rc = res["row_counts"][k]
        print(f"  arm {k}: incumbent_rows={rc['gt_rows']} arm_rows={rc['arm_rows']} "
              f"matched={rc['matched']} row_fidelity={rc['row_fidelity_pct']}% "
              f"(unmatched: {rc['unmatched_gt_rows']} incumbent-only, "
              f"{rc['unmatched_arm_rows']} arm-only)")

    print(f"\n{'='*104}\nTOKENS (no dollar figures -- Luna's rate is unpublished)\n{'='*104}")
    for k in ARMS:
        t = res["tokens"][k]
        print(f"  arm {k}: calls={t['calls']} prompt={t['prompt']} completion={t['completion']} "
              f"reasoning={t['reasoning']} total={t['total']} cached={t['cached']} "
              f"({t['cached_pct_of_prompt']}% of prompt)")
        print(f"          prompt+completion==total on ALL calls: "
              f"{t['prompt_plus_completion_eq_total_ALL_CALLS']}   "
              f"reasoning<=completion on ALL calls: "
              f"{t['reasoning_inside_completion_ALL_CALLS']}")

    regs = [l for l, r in list(res["scalar"].items()) + list(res["txn"].items())
            if "REGRESSION_A_vs_B" in r]
    print(f"\n{'='*104}\nREGRESSION GATE\n{'='*104}")
    if not regs:
        print("  No field where arm A is worse than arm B.")
    for l in regs:
        r = (res["scalar"].get(l) or res["txn"][l])["REGRESSION_A_vs_B"]
        print(f"  *** {l}: {r}")
    print(f"\nwrote {a.out}")


if __name__ == "__main__":
    main()
