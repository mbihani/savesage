"""STEP 1 (final) -- adjudicate the 5 weak fields on all 12 statements against the PDF.

Three sources per cell:
  PDF    -- what is actually printed (this script's evidence)
  GT     -- the client's incumbent record in sbi.csv `data` blob.  NOTE: `modelName`
            on these rows is 'gemini-3-flash-preview' / 'databricks-gemini-3-flash'
            and `detectionSource` is 'GEMINI', so GT is an INCUMBENT MODEL OUTPUT,
            not a human-verified oracle. Treated as the client's CONTRACT, and any
            GT cell contradicted by the PDF is reported as GT_DEFECT.
  MODEL  -- the prior Luna output in ~/Downloads/output/SBI/JSON

Verdict vocabulary (per the task):
  MODEL_ERROR / GT_DEFECT / NOT_PRINTED_NULL_CORRECT / IMAGE_ONLY / AMBIGUOUS_IN_PDF
  plus MATCH when model == GT and the PDF supports it.

ADJUDICATOR BUG THAT THIS FILE EXISTS TO AVOID
----------------------------------------------
An earlier pass in this session searched for numbers inside a text with ALL
whitespace stripped. Collapsing whitespace DESTROYS numeric token boundaries:
'... 12 720 1879 ...' collapses to '127201879', so a word-bounded regex for 1879
fails and the probe reports 'NOT PRINTED' for a figure that IS printed. It falsely
accused the ground truth. Numbers are therefore matched against the WORD TOKEN list
(page.get_text('words')); collapsed text is used ONLY for alphabetic labels, where
line-wrap ('EXPIR ING') is the real hazard.
"""

import csv
import json
import os
import re

import fitz

csv.field_size_limit(10 ** 9)

PDF_DIR = "/Users/mayanck.bihani/Downloads/output/SBI/PDF"
JSON_DIR = "/Users/mayanck.bihani/Downloads/output/SBI/JSON"
GT_CSV = "/Users/mayanck.bihani/Downloads/remaining pdfs ground truth/sbi.csv"
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "probe", "adjudication_5fields.json")

FIELDS = ["network", "closingPoints", "pointsRedeemedThisCycle",
          "pointsExpiringNext30Days", "pointsExpiringNext60Days"]


# ----------------------------------------------------------------- number matching
def indian_group(n):
    s = str(int(n))
    if len(s) <= 3:
        return s
    head, tail = s[:-3], s[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return ",".join(parts) + "," + tail


def variants(v):
    """Every plausible printed spelling of a number on an Indian bank statement."""
    a = abs(float(v))
    ints = int(a)
    out = {str(ints), f"{ints:,}", indian_group(ints)}
    if a != ints:
        for d in (1, 2):
            out |= {f"{a:.{d}f}", f"{a:,.{d}f}", indian_group(ints) + f"{a - ints:.{d}f}"[1:]}
    else:
        out |= {f"{a}.00", f"{ints:,}.00", indian_group(ints) + ".00",
                f"{a}.0", indian_group(ints) + ".0"}
    return {s for s in out if s}


def word_tokens(doc):
    """-> [(page, text, bbox)] for every word. Token boundaries INTACT."""
    out = []
    for pi in range(doc.page_count):
        for w in doc[pi].get_text("words"):
            out.append((pi, w[4], (round(w[0], 1), round(w[1], 1),
                                   round(w[2], 1), round(w[3], 1))))
    return out


def find_number(doc, toks, v):
    """Is `v` printed, as any Indian-grouped / decimal / signed variant?"""
    if v is None:
        return []
    cands = variants(v)
    hits = []
    for pi, t, bb in toks:
        clean = t.strip().strip("`₹()").lstrip("-+").rstrip("CRDcrd").strip()
        if clean in cands:
            hits.append({"page": pi + 1, "token": t, "bbox": bb})
    # also allow a value split across adjacent tokens on the same line ('1,525' '.25')
    if not hits:
        for i in range(len(toks) - 1):
            if toks[i][0] != toks[i + 1][0]:
                continue
            join = (toks[i][1] + toks[i + 1][1]).strip().strip("`₹()")
            if join in cands:
                hits.append({"page": toks[i][0] + 1, "token": join,
                             "bbox": toks[i][2], "split": True})
    return hits


def label_near(doc, toks, hit, radius=95):
    """Alphabetic labels geometrically near a numeric hit -> what the figure is called."""
    pi = hit["page"] - 1
    x0, y0, x1, y1 = hit["bbox"]
    words = [t for t in toks if t[0] == pi]
    near = []
    for _, t, bb in words:
        if not re.search(r"[A-Za-z]", t):
            continue
        dy = min(abs(bb[1] - y1), abs(y0 - bb[3]), abs(bb[1] - y0))
        dx = min(abs(bb[0] - x1), abs(x0 - bb[2]), abs(bb[0] - x0))
        if dy <= 40 and dx <= radius:
            near.append((round(dy + dx / 3, 1), t))
    near.sort()
    return [t for _, t in near[:10]]


# ----------------------------------------------------------------- loaders
def load_gt():
    rows = list(csv.DictReader(open(GT_CSV, encoding="utf-8", errors="replace")))
    out = {}
    for r in rows:
        link = str(r.get("link", ""))
        m = re.search(r"decrypt_(?:encrypt_)?(\d+)_", link)
        if not m:
            continue
        d = r.get("data") or "{}"
        try:
            d = json.loads(d)
            if isinstance(d, str):
                d = json.loads(d)
        except Exception:
            d = {}
        if not isinstance(d, dict):
            d = {}
        rw = d.get("rewards") or {}
        cards = d.get("cards") or []
        out[m.group(1)] = {
            "network": next((c.get("cardMeta", {}).get("network") for c in cards
                             if (c.get("cardMeta") or {}).get("network") is not None), None),
            "closingPoints": rw.get("closingPoints"),
            "pointsRedeemedThisCycle": rw.get("pointsRedeemedThisCycle"),
            "pointsExpiringNext30Days": rw.get("pointsExpiringNext30Days"),
            "pointsExpiringNext60Days": rw.get("pointsExpiringNext60Days"),
            "openingPoints": rw.get("openingPoints"),
            "pointsEarnedThisCycle": rw.get("pointsEarnedThisCycle"),
            "programType": rw.get("programType"),
            "rewardsSummary": rw.get("rewardsSummary"),
            "modelName": r.get("modelName"),
            "detectionSource": r.get("detectionSource"),
        }
    return out


def load_model(fname):
    p = os.path.join(JSON_DIR, fname.replace(".pdf", ".json"))
    if not os.path.exists(p):
        return {}
    d = json.load(open(p))
    rw = d.get("rewards") or {}
    cards = d.get("cards") or []
    return {
        "network": next((c.get("cardMeta", {}).get("network") for c in cards
                         if (c.get("cardMeta") or {}).get("network") is not None), None),
        "closingPoints": rw.get("closingPoints"),
        "pointsRedeemedThisCycle": rw.get("pointsRedeemedThisCycle"),
        "pointsExpiringNext30Days": rw.get("pointsExpiringNext30Days"),
        "pointsExpiringNext60Days": rw.get("pointsExpiringNext60Days"),
        "openingPoints": rw.get("openingPoints"),
        "pointsEarnedThisCycle": rw.get("pointsEarnedThisCycle"),
        "programType": rw.get("programType"),
    }


def num_eq(a, b):
    if a is None or b is None:
        return a is None and b is None
    try:
        return abs(float(a) - float(b)) < 0.005
    except (TypeError, ValueError):
        return str(a).strip().upper() == str(b).strip().upper()


def main():
    gt = load_gt()
    ev = {r["sid"]: r for r in json.load(
        open(os.path.join(HERE, "probe", "evidence_5fields.json")))}
    files = sorted(f for f in os.listdir(PDF_DIR) if f.lower().endswith(".pdf"))
    result = {}

    for f in files:
        sid = re.match(r"^decrypt_(?:encrypt_)?(\d+)_", f).group(1)
        doc = fitz.open(os.path.join(PDF_DIR, f))
        toks = word_tokens(doc)
        g, m = gt.get(sid, {}), load_model(f)
        cell = {"sid": sid, "filename": f, "gt": g, "model": m, "fields": {}}

        # ---------------------------------------------------- network
        e = ev[sid]
        gv, mv = g.get("network"), m.get("network")
        if e["network_verdict"] == "NOT_PRINTED_NULL_CORRECT":
            if gv is None and mv is None:
                v = "MATCH_NOT_PRINTED_NULL_CORRECT"
            elif gv is not None and mv is None:
                v = "GT_DEFECT"
            elif mv is not None:
                v = "MODEL_ERROR"
            else:
                v = "MATCH_NOT_PRINTED_NULL_CORRECT"
        else:
            v = e["network_verdict"]
        cell["fields"]["network"] = {
            "pdf": e["network_verdict"], "gt": gv, "model": mv, "verdict": v,
            "pdf_note": "every VISA/MasterCard/Rupay/Amex occurrence is boilerplate "
                        "(dispute paragraph, intl-fee table, VISA Credit Card Pay, "
                        "MoneySend); zero network tokens anywhere on page 1, where the "
                        "masked card number is printed; page-1 header art carries only "
                        "the co-brand product logo and the SBI Card logo -- no network mark",
        }

        # ---------------------------------------------------- numeric reward fields
        for fld in FIELDS[1:]:
            gv, mv = g.get(fld), m.get(fld)
            g_hits = find_number(doc, toks, gv)
            m_hits = find_number(doc, toks, mv)
            g_lab = label_near(doc, toks, g_hits[0]) if g_hits else []
            m_lab = label_near(doc, toks, m_hits[0]) if m_hits else []
            if num_eq(gv, mv):
                v = "MATCH_BOTH_NULL" if gv is None else "MATCH"
            elif mv is None and gv is not None:
                v = "GT_DEFECT_VALUE_NOT_PRINTED" if not g_hits else "MODEL_NULL_GT_PRINTED"
            elif mv is not None and gv is None:
                v = "MODEL_ERROR_NOT_PRINTED" if not m_hits else "MODEL_EXTRA_PRINTED"
            else:
                v = "DISAGREE_BOTH_NONNULL"
            cell["fields"][fld] = {
                "gt": gv, "model": mv, "verdict": v,
                "gt_printed": bool(g_hits), "gt_hits": g_hits[:3], "gt_labels": g_lab,
                "model_printed": bool(m_hits), "model_hits": m_hits[:3],
                "model_labels": m_lab,
            }
        cell["expiry_pdf_verdict"] = e["expiry_verdict"]
        cell["expiry_pdf_evidence"] = [h["collapsed_context"][:120]
                                       for h in e["expiry_hits"]]
        result[sid] = cell
        doc.close()

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(result, open(OUT, "w"), indent=1)

    # ------------------------------------------------------------------ report
    for fld in FIELDS:
        print(f"\n================ {fld}")
        tally = {}
        for sid, c in result.items():
            d = c["fields"][fld]
            tally[d["verdict"]] = tally.get(d["verdict"], 0) + 1
            extra = ""
            if fld != "network":
                extra = (f" gt_printed={d['gt_printed']} "
                         f"gt_labels={d['gt_labels'][:5]}")
            print(f"  {sid:12s} gt={str(d['gt'])[:12]:13s} model={str(d['model'])[:12]:13s} "
                  f"{d['verdict']:32s}{extra}")
        print(f"  TALLY: {tally}")
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
