"""STEP 1b -- read the ACTUAL page-1 rewards block on each of the 12 SBI statements
and bind label -> value GEOMETRICALLY (never by text order: SBI prints the labels and
their values as separate runs, often values FIRST).

The refined prompt currently claims SBI prints TWO reward tables. The probe output
says otherwise, so this script enumerates the layouts empirically instead of assuming.

Binding rule: a value cell belongs to the label whose horizontal span it overlaps
most, among labels in the header row directly above/below it. Values and labels are
matched within the block only.
"""

import json
import os
import re

import fitz

PDF_DIR = "/Users/mayanck.bihani/Downloads/output/SBI/PDF"
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "probe", "evidence_rewards.json")

# Headers that OPEN a page-1 rewards block on this corpus.
BLOCK_HEADERS = [
    "REWARD SUMMARY", "CARD CASHBACK SUMMARY", "CASHBACK SUMMARY",
    "REWARD POINT SUMMARY", "NEUCOINS SUMMARY", "NEUCOIN SUMMARY",
    "SHOP & SMILE SUMMARY", "SAVINGS AND BENEFITS SECTION",
]
NUM_RE = re.compile(r"^\(?-?[\d,]+(?:\.\d+)?\)?(?:\s*CR)?$")


def lines_of(page):
    out = []
    for b in page.get_text("dict")["blocks"]:
        if b.get("type") != 0:
            continue
        for ln in b.get("lines", []):
            t = "".join(sp["text"] for sp in ln["spans"]).strip()
            if t:
                out.append({"t": t, "x0": round(ln["bbox"][0], 1),
                            "x1": round(ln["bbox"][2], 1),
                            "y0": round(ln["bbox"][1], 1),
                            "y1": round(ln["bbox"][3], 1)})
    out.sort(key=lambda d: (d["y0"], d["x0"]))
    return out


def is_num(t):
    return bool(NUM_RE.match(t.replace(" ", "")))


def to_num(t):
    t = t.replace(",", "").replace(" ", "").replace("CR", "")
    neg = t.startswith("(") and t.endswith(")")
    t = t.strip("()")
    try:
        v = float(t)
    except ValueError:
        return None
    return -v if neg else v


def bind_block(lines, y_lo, y_hi):
    """Within [y_lo, y_hi): split into label rows and numeric rows, then bind each
    number to the label with the greatest horizontal-span overlap."""
    band = [L for L in lines if y_lo <= L["y0"] < y_hi]
    labels = [L for L in band if not is_num(L["t"]) and len(L["t"]) > 2]
    nums = [L for L in band if is_num(L["t"])]
    pairs = []
    for n in nums:
        best, bestscore = None, -1.0
        for lb in labels:
            # only labels within 40pt vertically (header row above OR below)
            if abs(lb["y0"] - n["y0"]) > 40:
                continue
            ov = min(lb["x1"], n["x1"]) - max(lb["x0"], n["x0"])
            # centre distance as tiebreak when spans do not overlap
            cd = abs((lb["x0"] + lb["x1"]) / 2 - (n["x0"] + n["x1"]) / 2)
            score = ov if ov > 0 else -cd / 100.0
            if score > bestscore:
                best, bestscore = lb, score
        pairs.append({"value_text": n["t"], "value": to_num(n["t"]),
                      "x": n["x0"], "y": n["y0"],
                      "bound_label": best["t"] if best else None,
                      "label_y": best["y0"] if best else None,
                      "overlap_score": round(bestscore, 2)})
    return {"labels": [{"t": L["t"], "x0": L["x0"], "x1": L["x1"], "y": L["y0"]}
                       for L in labels],
            "bindings": pairs}


def probe(path):
    doc = fitz.open(path)
    rec = {"blocks": []}
    for pi in range(min(3, doc.page_count)):
        lines = lines_of(doc[pi])
        for i, L in enumerate(lines):
            up = L["t"].upper()
            hdr = next((h for h in BLOCK_HEADERS if h in up), None)
            if not hdr:
                continue
            # block runs from the header to the next big vertical gap or 120pt
            y_lo = L["y0"]
            y_hi = y_lo + 130
            b = bind_block(lines, y_lo, y_hi)
            b["header"] = L["t"]
            b["header_key"] = hdr
            b["page"] = pi
            b["y"] = y_lo
            b["raw_lines"] = [f"y={x['y0']:.0f} x={x['x0']:.0f} {x['t'][:80]}"
                              for x in lines if y_lo <= x["y0"] < y_hi]
            rec["blocks"].append(b)
    doc.close()
    return rec


def main():
    files = sorted(f for f in os.listdir(PDF_DIR) if f.lower().endswith(".pdf"))
    out = {}
    for f in files:
        sid = re.match(r"^decrypt_(?:encrypt_)?(\d+)_", f).group(1)
        r = probe(os.path.join(PDF_DIR, f))
        out[sid] = r
        print(f"\n######## {sid}")
        for b in r["blocks"]:
            print(f"  [p{b['page']+1} y={b['y']:.0f}] {b['header']}  ({b['header_key']})")
            for L in b["raw_lines"]:
                print(f"       {L}")
            print("     BINDINGS:")
            for p in b["bindings"]:
                print(f"       {str(p['value_text']):>12s}  ->  "
                      f"{str(p['bound_label'])[:60]:60s} ov={p['overlap_score']}")
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(out, open(OUT, "w"), indent=1)
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
