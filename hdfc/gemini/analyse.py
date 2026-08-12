"""Per-field capture analysis over the 26 leaf fields of the adopted Gemini schema.

REFERENCE PRIORITY (as briefed)
  1. THE PDF ITSELF -- pdf_rows (geometric transaction rows), pdf_scalars (label-anchored
     headline figures), pdf_cards (printed card numbers + printed network words). This is
     the only oracle used for a verdict.
  2. OPUS-5 GT -- hdfc/gt_full/json, 15/15 statement-id overlap, all OK/finish=stop. Used
     as a SECOND OPINION, never as an oracle.
  3. The 15 JSONs in ~/Downloads/output/HDFC/JSON -- labelled 'prior run, unattributable':
     its prompt and schema are unknown (luna_run.py is a fragment with undefined
     LUNA_PROMPT/RESPONSE_FORMAT) and it targeted a different workspace. Reported for
     context only; never used to judge.

ADJUDICATOR HARDENING -- every one of these exists because its absence manufactures a
false accusation against the model:
  * whitespace-flexible AND space-insensitive description comparison. HDFC breaks words
    mid-token in the text layer ("Paym ent received", "Am azon Pay"), so the primary
    description test removes ALL whitespace; exact-string spacing fidelity is reported
    SEPARATELY rather than conflated with character capture.
  * Indian digit grouping is handled by stripping commas before parsing, never by
    matching them in place -- so 1,94,022.00 cannot lose its lakh digit, and HDFC's
    own malformed negative "-,208.34" still parses.
  * dates are compared SEMANTICALLY (day, month, year), not as strings. The HDFC prompt
    demands DD/MM/YYYY; the generic prompt demands no format and so echoes the PDF's
    "16 Jan 2026, 23:31". Scoring the control on format it was never asked for would be
    a rigged comparison, so format conformance is reported as its own separate metric.
  * amounts compared with a 0.005 tolerance.
  * direction/txnType vocabulary is MEASURED, not assumed -- the adopted schema pins no
    enum (see convert_schema.py), so off-vocabulary values are possible and visible.
  * a value absent from the text layer is NOT treated as fabricated. HDFC product names
    live in page-1 header ARTWORK that get_text() cannot see, so cardDisplayName /
    productFamily are reported as UNVERIFIABLE-FROM-TEXT rather than accused.
"""

import json
import os
import re
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pdf_cards as PC  # noqa: E402
import pdf_rows as P  # noqa: E402
import pdf_scalars as PS  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
GT_DIR = "/Users/mayanck.bihani/Savesage/bank_eval/hdfc/gt_full/json"
PRIOR_DIR = "/Users/mayanck.bihani/Downloads/output/HDFC/JSON"

# Arms are the json_<arm> directories to compare. Overridable so the same scoring
# implementation serves the 2-arm and 3-arm comparisons -- duplicating the judging
# logic into a second script would let the two copies drift silently.
ARMS = tuple((os.environ.get("ARMS") or "hdfc,generic").split(","))
OUT_JSON = os.environ.get("OUT_JSON") or "analysis.json"

MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}

LEAVES = [
    "statementMeta.issuerName", "statementMeta.statementDate", "statementMeta.dueDate",
    "statementLevelSummary.totalAmountDue", "statementLevelSummary.totalMinimumAmountDue",
    "statementLevelSummary.totalCreditLimit", "statementLevelSummary.availableCreditLimit",
    "cards[].cardMeta.cardDisplayName", "cards[].cardMeta.productFamily",
    "cards[].cardMeta.lastFourDigit", "cards[].cardMeta.network",
    "cards[].cardMeta.isPrimaryCard",
    "transactions[].date", "transactions[].description", "transactions[].amount",
    "transactions[].direction", "transactions[].txnType",
    "transactions[].rewardPointsOnThisTransaction", "transactions[].currency",
    "rewards.programType", "rewards.openingPoints", "rewards.pointsEarnedThisCycle",
    "rewards.pointsRedeemedThisCycle", "rewards.closingPoints",
    "rewards.pointsExpiringNext30Days", "rewards.pointsExpiringNext60Days",
]


# ------------------------------------------------------------------ normalisers
def nospace(s):
    return re.sub(r"\s+", "", str(s or "")).upper()


def num(v):
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace(",", "").replace("₹", "").replace("Rs.", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def amt_eq(a, b):
    a, b = num(a), num(b)
    if a is None or b is None:
        return False
    return abs(a - b) < 0.005


def parse_date(v):
    """-> (d, m, y) from any of DD/MM/YYYY, 'DD Mon YYYY[, HH:MM]', 'DD Mon, YYYY'."""
    if not v or not isinstance(v, str):
        return None
    s = v.strip()
    m = re.match(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{4})", s)
    if m:
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.match(r"^(\d{1,2})\s+([A-Za-z]{3,9}),?\s+(\d{4})", s)
    if m:
        mo = MONTHS.get(m.group(2)[:3].lower())
        return (int(m.group(1)), mo, int(m.group(3))) if mo else None
    return None


def is_ddmmyyyy(v):
    return bool(isinstance(v, str) and re.match(r"^\d{2}/\d{2}/\d{4}$", v.strip()))


def date_eq(a, b):
    pa, pb = parse_date(a), parse_date(b)
    if pa and pb:
        return pa == pb
    # non-date text (e.g. "Nil") -> compare space-insensitively
    if a and b and not pa and not pb:
        return nospace(a) == nospace(b)
    return False


# ------------------------------------------------------------------ loaders
def load_arm(arm):
    out = {}
    d = os.path.join(HERE, f"json_{arm}")
    for f in os.listdir(d):
        if f.endswith(".json"):
            r = json.load(open(os.path.join(d, f)))
            out[r["statement_id"]] = r
    return out


def load_gt():
    out = {}
    for f in os.listdir(GT_DIR):
        m = re.match(r"^decrypt_(?:encrypt_)?(\d+)_", f)
        if m:
            out[m.group(1)] = json.load(open(os.path.join(GT_DIR, f)))
    return out


def load_prior():
    """The unattributable prior run. Shape unknown -> read defensively."""
    out = {}
    if not os.path.isdir(PRIOR_DIR):
        return out
    for f in os.listdir(PRIOR_DIR):
        if not f.endswith(".json"):
            continue
        m = re.match(r"^decrypt_(?:encrypt_)?(\d+)_", f)
        if not m:
            continue
        try:
            out[m.group(1)] = json.load(open(os.path.join(PRIOR_DIR, f)))
        except Exception:
            pass
    return out


def get(d, path):
    cur = d
    for k in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


# ------------------------------------------------------------------ row alignment
def align(model_rows, pdf_rows):
    """Align model transactions to PDF rows.

    Index alignment when the counts match (both are in statement order). Otherwise a
    greedy match on (semantic date, amount) so a dropped or added row is localised
    instead of shifting every later row into a false mismatch.
    """
    if len(model_rows) == len(pdf_rows):
        return list(zip(model_rows, pdf_rows)), [], []
    used, pairs, unmatched_m = set(), [], []
    for mr in model_rows:
        md, ma = parse_date(mr.get("date")), num(mr.get("amount"))
        hit = None
        for i, pr in enumerate(pdf_rows):
            if i in used:
                continue
            if md == parse_date(pr["date"]) and ma is not None and abs(ma - pr["amount"]) < 0.005:
                hit = i
                break
        if hit is None:
            unmatched_m.append(mr)
        else:
            used.add(hit)
            pairs.append((mr, pdf_rows[hit]))
    unmatched_p = [pr for i, pr in enumerate(pdf_rows) if i not in used]
    return pairs, unmatched_m, unmatched_p


# ------------------------------------------------------------------ main
def main():
    arms = {a: load_arm(a) for a in ARMS}
    gt, prior = load_gt(), load_prior()

    pdf = {}
    for sid, fn, path in P.corpus():
        s = P.statement_id(fn)
        pdf[s] = {"rows": P.extract(path), "scalars": PS.extract(path),
                  "cards": PC.extract(path), "file": fn}

    ids = sorted(pdf, key=lambda x: int(x))

    # populated/null tallies per arm per leaf
    tally = {a: defaultdict(lambda: {"populated": 0, "null": 0, "cells": 0}) for a in ARMS}
    # correctness tallies
    correct = {a: defaultdict(lambda: {"ok": 0, "wrong": 0, "cmp": 0}) for a in ARMS}
    details = defaultdict(list)
    vocab = {a: {"direction": Counter(), "txnType": Counter()} for a in ARMS}
    fmt = {a: {"date_ddmmyyyy": 0, "date_cells": 0, "desc_exact": 0, "desc_cells": 0}
           for a in ARMS}
    rowcounts = []

    def bump(a, leaf, val):
        t = tally[a][leaf]
        t["cells"] += 1
        if val is None:
            t["null"] += 1
        else:
            t["populated"] += 1

    def judge(a, leaf, ok, sid, got, ref, note=""):
        c = correct[a][leaf]
        c["cmp"] += 1
        if ok:
            c["ok"] += 1
        else:
            c["wrong"] += 1
            details[(a, leaf)].append({"statement": sid, "got": got, "pdf_ref": ref,
                                       "note": note})

    for sid in ids:
        pr = pdf[sid]
        prow = pr["rows"]["rows"]
        psc = pr["scalars"]
        pcards = pr["cards"]["cards"]
        pnets = pr["cards"]["network_words_in_text"]

        for a in ARMS:
            rec = arms[a].get(sid)
            pj = (rec or {}).get("parsed_json") or {}

            # ---- statementMeta
            iss = get(pj, "statementMeta.issuerName")
            bump(a, "statementMeta.issuerName", iss)
            judge(a, "statementMeta.issuerName", nospace(iss) == "HDFCBANK", sid, iss,
                  "HDFC Bank")

            sd = get(pj, "statementMeta.statementDate")
            bump(a, "statementMeta.statementDate", sd)
            judge(a, "statementMeta.statementDate",
                  date_eq(sd, psc.get("statementDate", {}).get("value")), sid, sd,
                  psc.get("statementDate", {}).get("value"))

            dd = get(pj, "statementMeta.dueDate")
            bump(a, "statementMeta.dueDate", dd)
            judge(a, "statementMeta.dueDate",
                  date_eq(dd, psc.get("dueDate", {}).get("value")), sid, dd,
                  psc.get("dueDate", {}).get("value"))

            # ---- statementLevelSummary
            for leaf, key in (("totalAmountDue", "totalAmountDue"),
                              ("totalMinimumAmountDue", "totalMinimumAmountDue"),
                              ("totalCreditLimit", "totalCreditLimit"),
                              ("availableCreditLimit", "availableCreditLimit")):
                full = f"statementLevelSummary.{leaf}"
                v = get(pj, full)
                bump(a, full, v)
                ref = psc.get(key, {}).get("value")
                judge(a, full, amt_eq(v, ref), sid, v, ref)

            # ---- cards[]
            mcards = pj.get("cards") or []
            for leaf in ("cardDisplayName", "productFamily", "lastFourDigit", "network",
                         "isPrimaryCard"):
                full = f"cards[].cardMeta.{leaf}"
                if not mcards:
                    bump(a, full, None)
                for c in mcards:
                    bump(a, full, (c.get("cardMeta") or {}).get(leaf))

            # lastFourDigit vs printed last four (set comparison; order-insensitive)
            got4 = [(c.get("cardMeta") or {}).get("lastFourDigit") for c in mcards]
            ref4 = [c["last4"] for c in pcards]
            for g in got4:
                judge(a, "cards[].cardMeta.lastFourDigit",
                      g is not None and str(g) in ref4, sid, g, ref4)
            # a dropped card is recorded against lastFourDigit's alignment, separately
            if len(mcards) != len(pcards):
                details[(a, "cards[]_count")].append(
                    {"statement": sid, "got": len(mcards), "pdf_ref": len(pcards)})

            # network: correct iff (printed word -> that word) and (no word -> null)
            for c in mcards:
                g = (c.get("cardMeta") or {}).get("network")
                l4 = str((c.get("cardMeta") or {}).get("lastFourDigit"))
                exp = None
                for pc in pcards:
                    if pc["last4"] == l4 and pc["network"]:
                        exp = pc["network"].upper()
                if exp is None and pnets:
                    exp = pnets[0].upper() if len(set(pnets)) == 1 else "ANY_OF:" + ",".join(pnets)
                ok = (nospace(g) == nospace(exp)) if exp else (g is None)
                judge(a, "cards[].cardMeta.network", ok, sid, g, exp)

            # ---- transactions
            trows = pj.get("transactions") or []
            pairs, um, up = align(trows, prow)
            rowcounts.append({"statement": sid, "arm": a, "model": len(trows),
                              "pdf": len(prow), "unmatched_model": len(um),
                              "unmatched_pdf": len(up)})
            for leaf in ("date", "description", "amount", "direction", "txnType",
                         "rewardPointsOnThisTransaction", "currency"):
                full = f"transactions[].{leaf}"
                for r in trows:
                    bump(a, full, r.get(leaf))
            for r in trows:
                vocab[a]["direction"][str(r.get("direction"))] += 1
                vocab[a]["txnType"][str(r.get("txnType"))] += 1

            for mr, pr_ in pairs:
                fmt[a]["date_cells"] += 1
                if is_ddmmyyyy(mr.get("date")):
                    fmt[a]["date_ddmmyyyy"] += 1
                judge(a, "transactions[].date", date_eq(mr.get("date"), pr_["date"]),
                      sid, mr.get("date"), pr_["date"])
                judge(a, "transactions[].amount", amt_eq(mr.get("amount"), pr_["amount"]),
                      sid, mr.get("amount"), pr_["amount"])
                judge(a, "transactions[].direction",
                      str(mr.get("direction")) == pr_["direction"], sid,
                      mr.get("direction"), pr_["direction"],
                      note=f"marker plus={pr_['has_plus']} green={pr_['is_green']}")
                fmt[a]["desc_cells"] += 1
                if str(mr.get("description") or "") == pr_["description"]:
                    fmt[a]["desc_exact"] += 1
                judge(a, "transactions[].description",
                      nospace(mr.get("description")) == nospace(pr_["description"]),
                      sid, mr.get("description"), pr_["description"])
                # currency: every row in this corpus is rupee-denominated (0 FX legs)
                judge(a, "transactions[].currency",
                      nospace(mr.get("currency")) == "INR", sid, mr.get("currency"), "INR")
                # reward points -- judged over EVERY paired row, in BOTH directions.
                #
                # The earlier version only judged rows where the PDF HAD a printed reward
                # value, which structurally could not see a FALSE POSITIVE: a row with no
                # reward column where the model invents a number. That is exactly the C2
                # defect (a narration-internal '-RATE 18.0 -19' leaking into the field),
                # so it has to be scored, not skipped. Absent column => null is the
                # correct answer and a non-null is wrong.
                got_rp = mr.get("rewardPointsOnThisTransaction")
                ref_rp = pr_["reward_points"]
                if ref_rp is None:
                    ok_rp, note_rp = got_rp is None, "pdf row has NO reward column value"
                else:
                    ok_rp = num(got_rp) == float(ref_rp)
                    note_rp = "pdf row has a printed reward value"
                judge(a, "transactions[].rewardPointsOnThisTransaction", ok_rp, sid,
                      got_rp, ref_rp, note=note_rp)
                # sub-metric: only the rows that DO print a value (the older, narrower
                # measure, kept so the two are never conflated)
                if ref_rp is not None:
                    judge(a, "transactions[].rewardPointsOnThisTransaction@printedonly",
                          num(got_rp) == float(ref_rp), sid, got_rp, ref_rp)

            # ---- rewards (populated/null only; correctness needs per-file adjudication)
            for leaf in ("programType", "openingPoints", "pointsEarnedThisCycle",
                         "pointsRedeemedThisCycle", "closingPoints",
                         "pointsExpiringNext30Days", "pointsExpiringNext60Days"):
                full = f"rewards.{leaf}"
                bump(a, full, get(pj, f"rewards.{leaf}"))

    out = {
        "corpus": {"statements": len(ids), "pdf_transaction_rows": sum(len(pdf[s]["rows"]["rows"]) for s in ids)},
        "tally": {a: {k: dict(v) for k, v in tally[a].items()} for a in ARMS},
        "correct": {a: {k: dict(v) for k, v in correct[a].items()} for a in ARMS},
        "vocab": {a: {k: dict(v) for k, v in vocab[a].items()} for a in ARMS},
        "format": fmt,
        "rowcounts": rowcounts,
        "mismatches": {f"{a}|{leaf}": v for (a, leaf), v in details.items()},
        "gt_overlap": len([s for s in ids if s in gt]),
        "prior_overlap": len([s for s in ids if s in prior]),
    }
    with open(os.path.join(HERE, OUT_JSON), "w") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)

    # ---------------- console summary (small: details live in the analysis json)
    print(f"statements={len(ids)}  pdf_rows={out['corpus']['pdf_transaction_rows']}"
          f"  gt_overlap={out['gt_overlap']}  prior_overlap={out['prior_overlap']}")
    print(f"arms = {list(ARMS)}   ->  {OUT_JSON}")

    # A leaf is CORRECTNESS_SCORED only if a PDF oracle actually produced comparisons.
    # Everything else is POPULATED_ONLY: a populated tally is NOT accuracy and must
    # never be printed in an accuracy column.
    def label(leaf):
        return ("CORRECTNESS_SCORED"
                if any(correct[a][leaf]["cmp"] for a in ARMS) else "POPULATED_ONLY")

    hdr = f"{'leaf':<50}{'metric':<20}"
    for a in ARMS:
        hdr += f"{a[:14]:>17}"
    print("\n" + hdr)
    print("-" * len(hdr))
    for leaf in LEAVES + ["transactions[].rewardPointsOnThisTransaction@printedonly"]:
        lab = label(leaf)
        row = f"{leaf:<50}{lab:<20}"
        for a in ARMS:
            t, c = tally[a][leaf], correct[a][leaf]
            if lab == "CORRECTNESS_SCORED":
                row += f"{c['ok']}/{c['cmp']}".rjust(17)
            else:
                row += f"pop {t['populated']}/{t['cells']}".rjust(17)
        print(row)
    # NOTE: as of the enum change the CURRENT schema pins direction and txnType, but the
    # archived arms were run under the un-pinned schema, so this stays MEASURED rather
    # than assumed -- it is the check that the enum did what it claims.
    print("\nvocabulary (measured, never assumed):")
    for a in ARMS:
        print(f"  {a:14s} direction={dict(vocab[a]['direction'])}")
        print(f"  {a:14s} txnType={dict(vocab[a]['txnType'])}")
    print("\nformat conformance:")
    for a in ARMS:
        f = fmt[a]
        print(f"  {a:8s} date DD/MM/YYYY {f['date_ddmmyyyy']}/{f['date_cells']}   "
              f"description exact-spacing {f['desc_exact']}/{f['desc_cells']}")
    bad = [r for r in rowcounts if r["model"] != r["pdf"]]
    print(f"\nrow-count mismatches vs PDF: {len(bad)}")
    for r in bad:
        print(f"  {r['arm']:8s} {r['statement']:<12} model={r['model']} pdf={r['pdf']}")


if __name__ == "__main__":
    main()
