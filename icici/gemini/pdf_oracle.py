"""Build the PDF ORACLE for the 11 ICICI statements: the ground truth we score against.

Derived GEOMETRICALLY from the PDFs, not hand-typed, so it is reproducible and auditable.

Two label->value binding modes occur on ICICI and both are implemented explicitly:
  SAME_ROW  (Layout 1) label and value share a row band; value sits to the RIGHT.
              e.g. "Total Points earned*" y=655.8 x=48.4  ->  661 y=658.2 x=158.3
  COLUMN    (Layouts 2/3/4) the two labels sit above and BOTH values share one line
              below, separated only by x; each value binds to the label in its own
              COLUMN, not to the nearest text in reading order.
              e.g. "My Cash earned" x=47 / "My Cash transferred to" x=112
                   -> 3 at x=71 and 172 at x=145
Getting this wrong is the whole reason a naive reader mis-attributes rewards values.

closingPoints / openingPoints / expiry are asserted NULL with evidence: no ICICI layout
in this corpus prints a points balance or an expiry cell. The only "Closing Balance" on
the page is row SL.No 18 of the pre-printed illustrative Minimum-Amount-Due example,
whose money column is headed by a bare ` and whose value is the frozen specimen
26,958.20 -- money, never points.
"""

import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "probe"))
from pdflib import doc_lines, parse_indian_num  # noqa: E402

PDF_DIR = "/Users/mayanck.bihani/Downloads/output/ICICI/PDF"
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "pdf_oracle.json")
NETL4 = os.path.join(HERE, "probe", "net_l4_v2.json")

NUMONLY = re.compile(r"^-?[\d,]+(?:\.\d+)?$")

# label -> (role, layout). Matched whitespace-flexibly, case-insensitively.
LABELS = [
    ("Total Points earned",              "earned",   1),
    ("Points earned on iShop",           "ishop",    1),
    ("My Cash earned",                   "earned",   2),
    ("My Cash transferred to",           "redeemed", 2),
    ("Earnings transfered to",           "redeemed", 3),
    ("Earnings transferred to",          "redeemed", 3),
    ("Points Transferred to PAYBACK",    "redeemed", 4),
    ("Points Earned",                    "earned",   4),
    ("Earned",                           "earned",   3),
]
PROGRAM_BY_LAYOUT = {1: "Reward Points", 2: "Cashback", 3: "Cashback", 4: "Reward Points"}


def ns(s):
    return re.sub(r"\s+", "", s).upper()


def find_labels(lines):
    """Locate rewards labels. Longest-first so 'Points Earned' cannot steal
    'Points Transferred to PAYBACK', and 'Earned' cannot steal 'My Cash earned'."""
    found = []
    claimed = []          # (page, y, x0, x1) already consumed by a longer label
    for needle, role, layout in sorted(LABELS, key=lambda t: -len(t[0])):
        for ln in lines:
            if ns(needle) not in ns(ln["text"]):
                continue
            # a footnote sentence is not a label cell: labels are short
            if len(ln["text"]) > 60:
                continue
            key = (ln["page"], round(ln["bbox"][1], 1), round(ln["bbox"][0], 1))
            if any(k[0] == key[0] and abs(k[1] - key[1]) < 2 and abs(k[2] - key[2]) < 2
                   for k in claimed):
                continue
            claimed.append(key)
            found.append({"role": role, "layout": layout, "label": ln["text"],
                          "page": ln["page"], "x0": ln["bbox"][0], "x1": ln["bbox"][2],
                          "y": ln["bbox"][1]})
    return found


def numeric_lines(lines, page):
    return [{"v": parse_indian_num(l["text"]), "x0": l["bbox"][0], "x1": l["bbox"][2],
             "y": l["bbox"][1], "raw": l["text"]}
            for l in lines if l["page"] == page and NUMONLY.match(l["text"].strip())]


def bind(lab, nums):
    """Bind a value to this label: SAME_ROW to the right, else COLUMN below."""
    # SAME_ROW: within 6pt vertically, to the right, within 150pt
    same = [n for n in nums if abs(n["y"] - lab["y"]) <= 6
            and n["x0"] > lab["x0"] and n["x0"] - lab["x1"] < 150]
    if same:
        n = min(same, key=lambda n: n["x0"] - lab["x1"])
        return n, "SAME_ROW"
    # COLUMN: 6..34pt below, value's x within the label's column
    below = [n for n in nums if 6 < n["y"] - lab["y"] <= 34
             and (lab["x0"] - 30) <= n["x0"] <= (lab["x1"] + 30)]
    if below:
        n = min(below, key=lambda n: (n["y"] - lab["y"], abs(n["x0"] - lab["x0"])))
        return n, "COLUMN"
    return None, "UNBOUND"


def main():
    netl4 = json.load(open(NETL4))
    oracle = {}
    for path in sorted(glob.glob(PDF_DIR + "/*.pdf")):
        sid = re.match(r"decrypt_(\d+)_", os.path.basename(path)).group(1)
        lines, meta = doc_lines(path)
        labs = find_labels(lines)
        layouts = sorted({l["layout"] for l in labs})
        rec = {"n_pages": meta["n_pages"], "layouts_detected": layouts, "bindings": []}

        vals = {}
        for lab in labs:
            nums = numeric_lines(lines, lab["page"])
            n, mode = bind(lab, nums)
            rec["bindings"].append({
                "role": lab["role"], "layout": lab["layout"], "label": lab["label"],
                "label_page": lab["page"], "label_bbox_x0": round(lab["x0"], 2),
                "label_y": round(lab["y"], 2), "mode": mode,
                "value": None if n is None else n["v"],
                "value_x0": None if n is None else round(n["x0"], 2),
                "value_y": None if n is None else round(n["y"], 2),
            })
            if n is not None and lab["role"] in ("earned", "redeemed"):
                vals.setdefault(lab["role"], n["v"])

        # ---- rewards oracle ----
        rec["rewards"] = {
            "programType": PROGRAM_BY_LAYOUT.get(layouts[0]) if layouts else None,
            "pointsEarnedThisCycle": vals.get("earned"),
            "pointsRedeemedThisCycle": vals.get("redeemed"),
            # asserted null with evidence -- see module docstring
            "closingPoints": None,
            "openingPoints": None,
            "pointsExpiringNext30Days": None,
            "pointsExpiringNext60Days": None,
        }
        rec["closing_points_evidence"] = (
            "no points balance printed in any layout; the only 'Closing Balance' is the "
            "money specimen 26,958.20 at SL.No 18 of the pre-printed Minimum-Amount-Due "
            "example" if any("26,958.20" in l["text"] for l in lines)
            else "no 'Closing Balance' string anywhere in the document")

        # ---- cards oracle (from the measured card-number headings) ----
        m = netl4[sid]
        rec["cards"] = {
            "last4_reading_order": m["card_last4_reading_order"],
            "last4_distinct": m["distinct_last4"],
            "n_card_headings": len(m["card_headings"]),
            "network": None,
            "network_evidence": m["network_summary"]["verdict"],
        }
        rec["issuerName"] = "ICICI Bank"
        oracle[sid] = rec
        print(f"{sid}: layouts={layouts} earned={vals.get('earned')} "
              f"redeemed={vals.get('redeemed')} prog={rec['rewards']['programType']} "
              f"last4={m['distinct_last4']}")

    json.dump(oracle, open(OUT, "w"), indent=1)
    print("\nwrote", OUT)

    # ---- self-check against the values I measured by hand from the strip dumps ----
    EXPECT = {"1737715836": (661, None), "1529317035": (3, 172), "205034973": (33, 33),
              "410444357": (10, 69), "952325284": (146, 146)}
    print("\nself-check vs hand-read strip measurements:")
    bad = 0
    for sid, (e, r) in EXPECT.items():
        g = oracle[sid]["rewards"]
        ok = (g["pointsEarnedThisCycle"] == e and g["pointsRedeemedThisCycle"] == r)
        bad += not ok
        print(f"  {sid}: expect earned={e} redeemed={r} | got earned="
              f"{g['pointsEarnedThisCycle']} redeemed={g['pointsRedeemedThisCycle']} "
              f"{'OK' if ok else '<<< MISMATCH'}")
    if bad:
        print(f"\n{bad} MISMATCH(es): the oracle's geometric binding disagrees with the "
              f"hand-read values. Fix the binder before scoring anything.")
        sys.exit(1)
    print("\noracle binding agrees with hand-read measurements.")


if __name__ == "__main__":
    main()
