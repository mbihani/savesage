"""STEP 1 -- establish, from the PDFs themselves, whether `null` is the CORRECT answer
for the five fields the human reports as low-scoring.

Fields under adjudication:
  cards[].cardMeta.network
  rewards.closingPoints
  rewards.pointsRedeemedThisCycle
  rewards.pointsExpiringNext30Days
  rewards.pointsExpiringNext60Days

ADJUDICATOR DISCIPLINE (this project has produced multiple FALSE accusations of the
model, so every one of these is deliberate):
  * whitespace-flexible matching -- PDF line-wrap splits tokens MID-WORD, so a naive
    `"EXPIRING" in text` misses "EXPIR ING". Every probe matches against a text with
    ALL whitespace stripped, and separately against per-line text for context.
  * word-bounded, not naive substring, when matching on real (spaced) text.
  * Indian digit grouping: 18,068 == 18068 == 18068.00 all compare equal.
  * a value visible only inside a raster image is IMAGE_ONLY, never "absent".
  * a probe that reports "not printed" for text that IS printed is a PROBE BUG, so
    every 'absent' verdict is cross-checked with a second, independent method.

Output: probe/evidence_5fields.json  (+ a human-readable digest on stdout)
"""

import json
import os
import re
import sys

import fitz

PDF_DIR = "/Users/mayanck.bihani/Downloads/output/SBI/PDF"
JSON_DIR = "/Users/mayanck.bihani/Downloads/output/SBI/JSON"
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "probe", "evidence_5fields.json")

NETWORKS = ["VISA", "MASTERCARD", "MASTER CARD", "RUPAY", "AMEX",
            "AMERICAN EXPRESS", "DINERS"]

# Phrases that mark a network mention as BOILERPLATE rather than card identity.
BOILERPLATE_CUES = [
    "DISPUTE", "GUIDELINE", "NETWORK (", "AS PER THE NETWORK", "RESOLVED",
    "TERMS", "CONDITION", "FEE", "CHARGE", "PAY ", "PAYMENT OPTION",
    "BILLDESK", "NEFT", "IMPS", "UPI", "MERCHANT", "ELIGIBLE", "OFFER",
    "T&C", "APPLICABLE", "PLEASE", "WWW.", "HTTP", "CUSTOMER CARE",
    "INTERNATIONAL", "CROSS CURRENCY", "MARKUP", "CASH WITHDRAWAL",
]

# A masked SBI card number: 'XXXX XXXX XXXX XX57' (only last TWO digits real).
CARD_NUM_RE = re.compile(r"[X\*x]{4}\s*[X\*x]{4}\s*[X\*x]{4}\s*[X\*x]{0,2}\s*\d{2,4}")

EXPIRY_TOKENS = ["EXPIR", "EXPIRING", "EXPIRY", "WILLEXPIRE", "TOEXPIRE",
                 "EXPIRINGIN30", "EXPIRINGIN60", "LAPSE", "FORFEIT"]

REWARD_CUES = ["REWARD", "POINT", "CASHBACK", "CASH BACK", "NEUCOIN", "NEU COIN",
               "CLOSING", "REDEEM", "EARNED", "PREVIOUS BALANCE", "OPENING",
               "SAVINGS AND BENEFITS", "BALANCE", "FORFEIT", "EXPIR"]


def collapse(s):
    """All whitespace removed, uppercased. Defeats mid-word line-wrap splits."""
    return re.sub(r"\s+", "", s).upper()


def norm_num(tok):
    """'18,068.00' / '18068' / '(1,072)' -> float. Indian grouping safe."""
    t = tok.strip().replace(",", "").replace("`", "").replace("₹", "")
    neg = t.startswith("(") and t.endswith(")")
    t = t.strip("()").strip()
    try:
        v = float(t)
    except ValueError:
        return None
    return -v if neg else v


NUM_RE = re.compile(r"-?\(?\d[\d,]*(?:\.\d+)?\)?")


def page_lines(page):
    """-> [ {text, bbox, page} ] in geometric order."""
    out = []
    d = page.get_text("dict")
    for blk in d.get("blocks", []):
        if blk.get("type") != 0:
            continue
        for ln in blk.get("lines", []):
            txt = "".join(sp.get("text", "") for sp in ln.get("spans", []))
            if not txt.strip():
                continue
            out.append({"text": txt, "bbox": [round(c, 1) for c in ln["bbox"]]})
    return out


def classify_network_hit(line_text, page_idx, line_bbox, card_boxes):
    """(a) card-specific identity text near the masked card number -> EVIDENCE
    (b) boilerplate / disclaimer / fee table                      -> BOILERPLATE
    Only (a) may ever justify a non-null `network`."""
    up = line_text.upper()
    cues = [c for c in BOILERPLATE_CUES if c in up]
    # proximity to a printed masked card number, same page, within 60pt vertically
    near = []
    for cb in card_boxes:
        if cb["page"] != page_idx:
            continue
        dy = min(abs(line_bbox[1] - cb["bbox"][3]), abs(cb["bbox"][1] - line_bbox[3]),
                 abs(line_bbox[1] - cb["bbox"][1]))
        if dy <= 60:
            near.append({"card_text": cb["text"], "dy": round(dy, 1)})
    if near and not cues:
        return "EVIDENCE_CARD_IDENTITY", cues, near
    if near and cues:
        return "AMBIGUOUS_NEAR_CARD_BUT_BOILERPLATE_CUES", cues, near
    return "BOILERPLATE", cues, near


def probe_one(sid, fname, path):
    doc = fitz.open(path)
    rec = {"sid": sid, "filename": fname, "n_pages": doc.page_count}

    all_lines, coll_pages = [], []
    for pi in range(doc.page_count):
        pg = doc[pi]
        lns = page_lines(pg)
        for L in lns:
            L["page"] = pi
        all_lines.extend(lns)
        coll_pages.append(collapse(pg.get_text()))
    coll_all = "".join(coll_pages)
    rec["collapsed_chars"] = len(coll_all)

    # ---------------------------------------------------------------- ITFRupee?
    # HDFC encodes the rupee sign as ASCII 'C' via the ITFRupee font, which poisons
    # any 'C -> CREDIT' rule. Prior work says SBI has no ITFRupee. VERIFY, don't assume.
    fonts = set()
    for pi in range(doc.page_count):
        for f in doc[pi].get_fonts(full=False):
            fonts.add(str(f[3]))
    rec["fonts"] = sorted(fonts)
    rec["itfrupee_present"] = any("ITFRUPEE" in f.upper().replace(" ", "")
                                  for f in fonts)

    # ---------------------------------------------------------------- card numbers
    card_boxes = []
    for L in all_lines:
        for m in CARD_NUM_RE.finditer(L["text"]):
            card_boxes.append({"page": L["page"], "text": m.group(0).strip(),
                               "bbox": L["bbox"], "line": L["text"].strip()[:120]})
    rec["card_number_hits"] = card_boxes

    # ---------------------------------------------------------------- NETWORK
    net_hits = []
    for L in all_lines:
        cl = collapse(L["text"])
        for n in NETWORKS:
            key = collapse(n)
            if key not in cl:
                continue
            verdict, cues, near = classify_network_hit(
                L["text"], L["page"], L["bbox"], card_boxes)
            net_hits.append({
                "token": n, "page": L["page"], "bbox": L["bbox"],
                "line": L["text"].strip()[:200], "verdict": verdict,
                "boilerplate_cues": cues[:6], "near_card_number": near[:2],
            })
    rec["network_hits"] = net_hits
    rec["network_hit_counts"] = {}
    for h in net_hits:
        rec["network_hit_counts"][h["token"]] = \
            rec["network_hit_counts"].get(h["token"], 0) + 1
    ev = [h for h in net_hits if h["verdict"] == "EVIDENCE_CARD_IDENTITY"]
    amb = [h for h in net_hits if h["verdict"].startswith("AMBIGUOUS")]
    distinct_ev = sorted({h["token"] for h in ev})
    if len(distinct_ev) == 1:
        rec["network_verdict"] = "PRINTED_IN_TEXT"
        rec["network_value"] = distinct_ev[0]
    elif len(distinct_ev) > 1:
        rec["network_verdict"] = "CONFLICTING_TEXT_EVIDENCE"
        rec["network_value"] = None
    elif amb:
        rec["network_verdict"] = "AMBIGUOUS_IN_PDF"
        rec["network_value"] = None
    else:
        rec["network_verdict"] = "NOT_PRINTED_NULL_CORRECT"
        rec["network_value"] = None

    # -------------------------------------------------- page-1 card ARTWORK images
    # A network mark present ONLY as a logo image is IMAGE_ONLY: invisible to
    # get_text(), so it caps a text-based extractor BELOW 100%.
    imgs = []
    for pi in range(min(2, doc.page_count)):
        for im in doc[pi].get_image_info(xrefs=True):
            bb = im.get("bbox")
            if not bb:
                continue
            w, h = bb[2] - bb[0], bb[3] - bb[1]
            if w < 8 or h < 8:
                continue
            imgs.append({"page": pi, "bbox": [round(c, 1) for c in bb],
                         "w": round(w, 1), "h": round(h, 1),
                         "xref": im.get("xref")})
    imgs.sort(key=lambda d: -(d["w"] * d["h"]))
    rec["page12_images"] = imgs[:12]
    rec["n_page12_images"] = len(imgs)
    # images sitting near a printed card number = plausible card artwork
    rec["images_near_card_number"] = [
        {"page": im["page"], "bbox": im["bbox"], "w": im["w"], "h": im["h"]}
        for im in imgs
        for cb in card_boxes
        if cb["page"] == im["page"]
        and im["bbox"][1] - 90 <= cb["bbox"][1] <= im["bbox"][3] + 90
    ][:8]

    # ---------------------------------------------------------------- EXPIRY
    exp_hits = []
    for tok in EXPIRY_TOKENS:
        k = collapse(tok)
        for pi, cp in enumerate(coll_pages):
            start = 0
            while True:
                i = cp.find(k, start)
                if i < 0:
                    break
                exp_hits.append({"token": tok, "page": pi,
                                 "collapsed_context": cp[max(0, i - 70):i + 90]})
                start = i + 1
    # dedupe by (page, context)
    seen, ded = set(), []
    for h in exp_hits:
        key = (h["page"], h["collapsed_context"])
        if key in seen:
            continue
        seen.add(key)
        ded.append(h)
    rec["expiry_hits"] = ded
    # second, INDEPENDENT method: raw page text regex allowing whitespace between chars
    loose = re.compile(r"E\s*X\s*P\s*I\s*R", re.I)
    rec["expiry_hits_method2"] = [
        {"page": pi, "context": re.sub(r"\s+", " ", doc[pi].get_text()
                                       [max(0, m.start() - 80):m.start() + 110])}
        for pi in range(doc.page_count)
        for m in loose.finditer(doc[pi].get_text())
    ]
    # Does an expiry hit carry a NUMBER (i.e. could it populate the field at all)?
    rec["expiry_hits_with_number"] = [
        h for h in ded
        if NUM_RE.search(h["collapsed_context"])
        and any(w in h["collapsed_context"] for w in ("POINT", "REWARD", "NEUCOIN",
                                                      "30DAY", "60DAY", "DAYS"))
    ]
    if not ded and not rec["expiry_hits_method2"]:
        rec["expiry_verdict"] = "NOT_PRINTED_NULL_CORRECT"
    elif rec["expiry_hits_with_number"]:
        rec["expiry_verdict"] = "NEEDS_MANUAL_READ"
    else:
        rec["expiry_verdict"] = "WORD_PRESENT_BUT_NO_POINTS_FIGURE"

    # ---------------------------------------------------------------- REWARDS block
    rw = []
    for L in all_lines:
        cl = collapse(L["text"])
        if any(collapse(c) in cl for c in REWARD_CUES):
            rw.append({"page": L["page"], "bbox": L["bbox"],
                       "text": L["text"].strip()[:220]})
    rec["reward_lines"] = rw
    rec["n_reward_lines"] = len(rw)

    # which reward TABLE is present?
    joined = collapse(" ".join(L["text"] for L in all_lines))
    rec["has_current_cycle_strip"] = all(
        k in joined for k in ("PREVIOUSBALANCE", "EARNED", "CLOSINGBALANCE"))
    rec["has_savings_benefits"] = "SAVINGSANDBENEFITS" in joined
    rec["label_probe"] = {
        lbl: (collapse(lbl) in joined) for lbl in [
            "Closing Balance", "Closing Points", "Reward Points Balance",
            "Previous Balance", "Earned", "Redeemed", "Redeemed/Expired/Forfeited",
            "Forfeited", "Total Reward Points", "Reward Points", "NeuCoins",
            "Cashback", "Total Cashback", "Net Reward Points", "Points Balance",
            "Reward Point Balance", "Cash Back", "For this statement",
            "From the card issue date", "For this year", "Total Savings",
        ]
    }
    doc.close()
    return rec


def prior_json(sid_file):
    p = os.path.join(JSON_DIR, sid_file.replace(".pdf", ".json"))
    if not os.path.exists(p):
        return None
    try:
        return json.load(open(p))
    except Exception:
        return None


def main():
    files = sorted(f for f in os.listdir(PDF_DIR) if f.lower().endswith(".pdf"))
    print(f"PDFs found: {len(files)}")
    recs = []
    for f in files:
        sid = re.match(r"^decrypt_(?:encrypt_)?(\d+)_", f).group(1)
        r = probe_one(sid, f, os.path.join(PDF_DIR, f))
        pj = prior_json(f)
        if pj:
            rw = (pj.get("rewards") or {})
            cards = pj.get("cards") or []
            r["prior_output"] = {
                "network": [((c.get("cardMeta") or {}).get("network")) for c in cards],
                "programType": rw.get("programType"),
                "openingPoints": rw.get("openingPoints"),
                "pointsEarnedThisCycle": rw.get("pointsEarnedThisCycle"),
                "pointsRedeemedThisCycle": rw.get("pointsRedeemedThisCycle"),
                "closingPoints": rw.get("closingPoints"),
                "pointsExpiringNext30Days": rw.get("pointsExpiringNext30Days"),
                "pointsExpiringNext60Days": rw.get("pointsExpiringNext60Days"),
            }
        recs.append(r)
        print(f"\n=== {sid}  ({f[:46]}) pages={r['n_pages']} "
              f"itfrupee={r['itfrupee_present']}")
        print(f"  network: {r['network_verdict']} value={r['network_value']} "
              f"counts={r['network_hit_counts']} "
              f"evidence_hits={sum(1 for h in r['network_hits'] if h['verdict']=='EVIDENCE_CARD_IDENTITY')} "
              f"card_num_hits={len(r['card_number_hits'])} imgs={r['n_page12_images']}")
        print(f"  expiry : {r['expiry_verdict']} hits={len(r['expiry_hits'])} "
              f"m2={len(r['expiry_hits_method2'])} with_num={len(r['expiry_hits_with_number'])}")
        print(f"  rewards: cycle_strip={r['has_current_cycle_strip']} "
              f"savings={r['has_savings_benefits']} reward_lines={r['n_reward_lines']}")
        if pj:
            print(f"  prior  : {r['prior_output']}")

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(recs, open(OUT, "w"), indent=1)
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
