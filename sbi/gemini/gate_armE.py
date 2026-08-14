"""REGRESSION GATE: arm E (row-completeness fix) vs arm D (pre-fix), 12 statements.

The fix was added to win ONE thing (row completeness on the long statement). Everything
else the current prompt already wins is MEASURED and must not move. This script re-checks
each protected item and prints a table, plus a revert recommendation if anything moved.

PROTECTED ITEMS (each was won by an earlier, separately measured prompt change)
  1. closingPoints            the printed cashback figure on six Shape-2a statements,
                              18068 on 221159806, null on the other five
  2. DUPLICATION INVARIANT    count of statements with closingPoints == pointsEarnedThisCycle
                              must be zero OUTSIDE PDF-proven Shape 2a. It was widespread
                              before the closingPoints fix, so this remains the most
                              important invariant here.
  3. network                  null on 12/12 (all 135 network mentions in this corpus are
                              boilerplate)
  4. pointsExpiringNext30Days / ...60Days   null on 12/12
  5. pointsEarnedThisCycle    unchanged per statement
  6. txnType                  no off-vocabulary value; the REFUND anchor still fires
  7. row counts               identical to arm D on the other 11 statements

ROW EXACTNESS, NOT JUST ROW COUNT
---------------------------------
Row count turned out to be a MISLEADING metric on this defect: the pre-fix prompt hit the
correct count of 71 on 8 of 12 samples while 4 of those still carried a fabricated row
(description from one printed line, amount from the next) and dropped the real one. So
this gate also diffs every arm's rows against the PDF-derived truth from pdf_rowtruth.py,
with normalised dates and rounded amounts and Counter multiplicity -- because arm C emits
'01 May 2026' where arm D emits '01/05/2026', and a naive string key matches nothing and
manufactures a phantom discrepancy.
"""

import glob
import json
import os
import re
import sys
from collections import Counter

import fitz

HERE = os.path.dirname(os.path.abspath(__file__))
TRUTH = json.load(open(os.path.join(HERE, "pdf_rowtruth.json")))
PDF_DIR = "/Users/mayanck.bihani/Downloads/output/SBI/PDF"

EXPECTED_CLOSING = {
    "1036185244": 106,
    "1118980175": 1525.25,
    "1120623464": None,
    "1152718739": None,
    "1390952698": None,
    "1511624796": 476,
    "1707857175": None,
    "221159806": 18068,
    "369606524": 375.25,
    "393366914": None,
    "515948911": -1467,
    "905768587": 453,
}

SHAPE_2A_HEADERS = {
    "CARD CASHBACK SUMMARY FOR THIS STATEMENT",
    "CASHBACK SUMMARY FOR THIS STATEMENT",
}

TXNTYPE_VOCAB = {"PURCHASE", "PAYMENT", "REFUND", "REVERSAL", "CASHBACK", "FEE", "TAX",
                 "INTEREST", "EMI", "CASH_ADVANCE", "UPI", None}

MON = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"])}


def nd(s):
    """Normalise a date to DD/MM/YYYY. Arms disagree on FORMAT, not on value."""
    if not s:
        return None
    s = str(s).strip()
    m = re.match(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{4})$", s)
    if m:
        return f"{int(m.group(1)):02d}/{int(m.group(2)):02d}/{m.group(3)}"
    m = re.match(r"^(\d{1,2})\s+([A-Za-z]{3})[a-z]*\.?\s+(\d{2}|\d{4})$", s)
    if m:
        y = int(m.group(3))
        y = 2000 + y if y < 100 else y
        return f"{int(m.group(1)):02d}/{MON[m.group(2).lower()]:02d}/{y:04d}"
    return s


def K(d, a, desc):
    return (nd(d), round(float(a), 2) if a is not None else None,
            re.sub(r"\s+", " ", (desc or "")).strip().upper())


def load(path):
    return json.load(open(path))


def pdf_has_shape_2a(sid):
    """Anchor the equality exception in page-1 PDF evidence, never model output."""
    matches = glob.glob(os.path.join(PDF_DIR, f"decrypt*_{sid}_*.pdf"))
    if len(matches) != 1:
        raise AssertionError(f"{sid}: expected one source PDF, found {len(matches)}")
    with fitz.open(matches[0]) as doc:
        page1 = re.sub(r"\s+", " ", doc[0].get_text()).upper()
    return any(header in page1 for header in SHAPE_2A_HEADERS)


def unbacked_duplication(closing, earned, shape_2a):
    """True only for the old mis-slot defect; Shape 2a equality is by convention."""
    return closing is not None and closing == earned and not shape_2a


def self_test():
    assert unbacked_duplication(12, 12, False)
    assert not unbacked_duplication(12, 12, True)
    assert not unbacked_duplication(None, None, False)
    print("PASS: synthetic equality without a Shape-2a PDF block fires the invariant")


def rowkeys(rec):
    return Counter(K(t.get("date"), t.get("amount"), t.get("description"))
                   for t in rec["parsed_json"]["transactions"])


def truthkeys(sid):
    return Counter(K(r["date"], r["amount"], r["desc"]) for r in TRUTH[sid]["rows"])


def g(rec, *path):
    cur = rec.get("parsed_json") or {}
    for p in path:
        if isinstance(cur, list):
            cur = cur[0] if cur else {}
        if not isinstance(cur, dict):
            return None
        cur = cur.get(p)
    return cur


def rewards(rec, field):
    """rewards.* live under the statement object; probe the plausible shapes."""
    pj = rec.get("parsed_json") or {}
    for holder in (pj.get("rewards"), (pj.get("cards") or [{}])[0].get("rewards")
                   if pj.get("cards") else None):
        if isinstance(holder, dict) and field in holder:
            return holder[field]
    # fall back: search one level deep
    def walk(n):
        if isinstance(n, dict):
            if field in n:
                return n[field]
            for v in n.values():
                r = walk(v)
                if r is not None:
                    return r
        if isinstance(n, list):
            for v in n:
                r = walk(v)
                if r is not None:
                    return r
        return None
    return walk(pj)


def find(node, field):
    """Collect every value of `field` anywhere in the tree (for network/lastFourDigit)."""
    out = []
    if isinstance(node, dict):
        for k, v in node.items():
            if k == field:
                out.append(v)
            else:
                out += find(v, field)
    elif isinstance(node, list):
        for v in node:
            out += find(v, field)
    return out


def main():
    sids = sorted(TRUTH)
    D, E = {}, {}
    incomplete = []
    for s in sids:
        D[s] = load(f"{HERE}/json_armD/{s}.json")
        p = f"{HERE}/json_armE/{s}.json"
        E[s] = load(p) if os.path.exists(p) else {"outcome": "MISSING"}
        # A record with no parsed_json cannot be scored. It is almost always an
        # infrastructure failure (429 / IP-ACL / network / expired OAuth), and scoring it
        # as a model result would manufacture a regression. Report, never score.
        if not isinstance(E[s].get("parsed_json"), dict):
            incomplete.append((s, E[s].get("outcome"), E[s].get("failure_class")))

    print("=" * 100)
    print("REGRESSION GATE  arm E (fix) vs arm D (pre-fix)   n=12 statements")
    print("=" * 100)

    if incomplete:
        print(f"\n*** GATE INCOMPLETE: {len(incomplete)}/12 arm-E records carry no parsed "
              f"JSON and are NOT scored (infrastructure, not model failure) ***")
        for s, o, fc in incomplete:
            print(f"    {s:<13} outcome={o} failure_class={fc}")
        print("    Re-run: python3 run_armE.py --reps 3   (these records are non-terminal "
              "and will be retried)")
        sids = [s for s in sids if s not in {x[0] for x in incomplete}]
        print(f"    Scoring the {len(sids)} complete statement(s) below.\n")

    moved = []

    # ---- infrastructure sanity: never score a 429/IP-ACL as a model result
    for s in sids:
        for nm, r in (("D", D[s]), ("E", E[s])):
            if r.get("outcome") != "OK" or r.get("failure_class") not in (None, "model"):
                moved.append(f"{s}: arm{nm} non-OK outcome={r.get('outcome')} "
                             f"failure_class={r.get('failure_class')} (INFRASTRUCTURE)")
            if r.get("finish_reason") != "stop":
                moved.append(f"{s}: arm{nm} finish_reason={r.get('finish_reason')}")
            u = r.get("usage_raw") or {}
            if u.get("prompt_tokens", 0) + u.get("completion_tokens", 0) != u.get("total_tokens"):
                moved.append(f"{s}: arm{nm} token accounting mismatch {u}")

    # ---- row counts + row exactness
    print(f"\n{'sid':<13}{'PDFrows':>8}{'D_n':>6}{'E_n':>6}{'D_exact':>9}{'E_exact':>9}  note")
    for s in sorted(sids, key=lambda x: -TRUTH[x]["n_rows"]):
        t = truthkeys(s)
        dk, ek = rowkeys(D[s]), rowkeys(E[s])
        dex = (t - dk) == Counter() and (dk - t) == Counter()
        eex = (t - ek) == Counter() and (ek - t) == Counter()
        note = ""
        if D[s].get("n_transactions") != E[s].get("n_transactions"):
            if s == "1707857175":
                note = "TARGET statement"
            else:
                note = "*** ROW COUNT MOVED ***"
                moved.append(f"{s}: row count D={D[s].get('n_transactions')} "
                             f"E={E[s].get('n_transactions')}")
        if dex and not eex:
            note += " *** E LOST ROW EXACTNESS ***"
            moved.append(f"{s}: row exactness regressed D=exact E=not")
        print(f"{s:<13}{TRUTH[s]['n_rows']:>8}{D[s].get('n_transactions'):>6}"
              f"{E[s].get('n_transactions'):>6}{str(dex):>9}{str(eex):>9}  {note}")

    # ---- rewards protected fields
    print(f"\n{'sid':<13}{'closingPoints D/E':>26}{'earnedThisCycle D/E':>28}"
          f"{'exp30 D/E':>14}{'exp60 D/E':>14}{'net D/E':>14}")
    dup_d = dup_e = allowed_dup_e = 0
    for s in sids:
        cd, ce = rewards(D[s], "closingPoints"), rewards(E[s], "closingPoints")
        pd_, pe = rewards(D[s], "pointsEarnedThisCycle"), rewards(E[s], "pointsEarnedThisCycle")
        e30d, e30e = rewards(D[s], "pointsExpiringNext30Days"), rewards(E[s], "pointsExpiringNext30Days")
        e60d, e60e = rewards(D[s], "pointsExpiringNext60Days"), rewards(E[s], "pointsExpiringNext60Days")
        nd_, ne = find(D[s].get("parsed_json"), "network"), find(E[s].get("parsed_json"), "network")
        nd_ = nd_[0] if nd_ else "ABSENT"
        ne = ne[0] if ne else "ABSENT"
        if cd is not None and cd == pd_:
            dup_d += 1
        shape_2a = pdf_has_shape_2a(s)
        if ce is not None and ce == pe:
            dup_e += 1
            if shape_2a:
                allowed_dup_e += 1
        for label, a, b in (("pointsEarnedThisCycle", pd_, pe),
                            ("pointsExpiringNext30Days", e30d, e30e),
                            ("pointsExpiringNext60Days", e60d, e60e),
                            ("network", nd_, ne)):
            if a != b:
                moved.append(f"{s}: {label} D={a!r} -> E={b!r}")
        if ce != EXPECTED_CLOSING[s]:
            moved.append(f"{s}: closingPoints expected {EXPECTED_CLOSING[s]!r}, got {ce!r}")
        if unbacked_duplication(ce, pe, shape_2a):
            moved.append(f"{s}: DUPLICATION INVARIANT BROKEN without Shape-2a PDF evidence")
        if e30e is not None or e60e is not None:
            moved.append(f"{s}: expiry fields must be null in E, got {e30e!r}/{e60e!r}")
        if ne not in (None, "ABSENT"):
            moved.append(f"{s}: network must be null in E, got {ne!r}")
        print(f"{s:<13}{str(cd)+' / '+str(ce):>26}{str(pd_)+' / '+str(pe):>28}"
              f"{str(e30d)+'/'+str(e30e):>14}{str(e60d)+'/'+str(e60e):>14}"
              f"{str(nd_)+'/'+str(ne):>14}")
    print(f"\nDUPLICATION INVARIANT  closingPoints == pointsEarnedThisCycle:"
          f"  armD {dup_d}/12   armE {dup_e}/12; "
          f"Shape-2a-backed {allowed_dup_e}, unbacked {dup_e - allowed_dup_e} (must be 0)")
    if "221159806" in sids and rewards(E["221159806"], "closingPoints") != 18068:
        moved.append(f"221159806 closingPoints expected 18068, "
                     f"got {rewards(E['221159806'], 'closingPoints')!r}")

    # ---- txnType vocabulary + REFUND anchor
    print("\ntxnType vocabulary + REFUND anchor")
    off_d = off_e = 0
    ref_d = ref_e = 0
    for s in sids:
        for nm, r, in (("D", D[s]), ("E", E[s])):
            vals = [t.get("txnType") for t in r["parsed_json"]["transactions"]]
            off = [v for v in vals if v not in TXNTYPE_VOCAB]
            nref = sum(1 for v in vals if v == "REFUND")
            if nm == "D":
                off_d += len(off); ref_d += nref
            else:
                off_e += len(off); ref_e += nref
            if off:
                moved.append(f"{s}: arm{nm} off-vocabulary txnType {sorted(set(off))}")
    print(f"  off-vocabulary values:  armD {off_d}   armE {off_e}   (must be 0)")
    print(f"  REFUND rows total:      armD {ref_d}   armE {ref_e}   (anchor must keep firing)")
    if ref_d > 0 and ref_e == 0:
        moved.append(f"REFUND anchor stopped firing: D={ref_d} E={ref_e}")

    # ---- repeats on the long statement
    print("\nREPEATS on 1707857175 (PDF prints 71)")
    t = truthkeys("1707857175")
    for tag, pat in (("armD  (pre-fix)", f"{HERE}/json_armE_base_r*/1707857175.json"),
                     ("armE  (fix)", f"{HERE}/json_armE_fix_r*/1707857175.json"),
                     ("armE  (full-run reps)", f"{HERE}/json_armE_rep*/1707857175.json")):
        rows = []
        for p in sorted(glob.glob(pat), key=lambda x: int(re.search(r"_r(?:ep)?(\d+)/", x).group(1))):
            r = load(p)
            if not isinstance(r.get("parsed_json"), dict):
                continue          # infrastructure failure -- never scored as a model result
            k = rowkeys(r)
            rows.append((r.get("n_transactions"),
                         "exact" if (t - k) == Counter() and (k - t) == Counter() else "DIFF"))
        if rows:
            ns = [x[0] for x in rows]
            ex = sum(1 for x in rows if x[1] == "exact")
            print(f"  {tag:<24} n={ns}  row-exact {ex}/{len(rows)}")

    print("\n" + "=" * 100)
    if moved:
        print(f"*** {len(moved)} PROTECTED ITEM(S) MOVED -- REVERT RECOMMENDED ***")
        for m in moved:
            print("   -", m)
    else:
        print("ALL PROTECTED ITEMS HELD. No movement on any gated field.")
    print("=" * 100)
    return 1 if moved else 0


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        self_test()
        sys.exit(0)
    sys.exit(main())
