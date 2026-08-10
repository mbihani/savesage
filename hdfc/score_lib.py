"""Scoring for the HDFC 3-way comparison.

THE CENTRAL DISCIPLINE -- NON-CIRCULAR TRANSACTION MATCHING.
Candidate transaction pairs are admitted on DESCRIPTION similarity ONLY, then a
1:1 assignment is enforced (greedy over globally-sorted similarity, which for this
data is equivalent to Hungarian at far lower cost and is order-insensitive because
the candidate list is sorted by score before assignment, not by input order).
`date`, `amount` and `direction` are NEVER inputs to matching -- if they were,
reporting per-field accuracy for them would make them 100% by construction. That
bug has already bitten this project once.

The CSV is the INCUMBENT (Gemini) parser's output, not ground truth. So:
  Luna-vs-Opus-GT  -> reported as ACCURACY
  Luna-vs-CSV      -> reported as AGREEMENT
and every Luna-vs-CSV disagreement is adjudicated against the PDF text itself.
"""

import difflib
import json
import re
import unicodedata

# ---------------------------------------------------------------- normalisation

_MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}


def norm_date(v):
    """-> 'DD/MM/YYYY' or None. The CSV prints statement-level dates as
    '21 Aug, 2026, 12:00 AM' and transaction dates as '01/07/2026'; the models emit
    DD/MM/YYYY. Comparing raw strings would score a pure FORMAT difference as a
    disagreement, so both sides are normalised to one canonical form."""
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    # Strip a trailing clock time only ('21 Aug, 2026, 12:00 AM' -> '21 Aug, 2026').
    # Splitting on the first comma instead would truncate '03 Feb, 2026' to '03 Feb'
    # and score a pure FORMAT difference as a disagreement.
    # HDFC transaction rows print the clock time after a PIPE: '18/04/2026 | 00:00', and
    # some layouts omit the space: '17/06/2026| 16:06'. This must run BEFORE the generic
    # trailing-time strip below, which would otherwise remove ' 00:00' and leave a
    # dangling '18/04/2026 |' that no date pattern matches. Without it norm_date returns
    # None and a pure FORMAT difference is scored as a wrong date -- measured as 23
    # spurious incumbent date errors on one statement.
    s = re.sub(r"\s*\|\s*\d{1,2}:\d{2}(?::\d{2})?\s*(?:[AaPp]\.?[Mm]\.?)?\s*$", "", s).strip()
    s = re.sub(r"[,\s]+\d{1,2}:\d{2}(?::\d{2})?\s*(?:[AaPp]\.?[Mm]\.?)?\s*$", "", s).strip()
    s = re.sub(r"\s+\d{2}:\d{2}.*$", "", s).strip()
    s = s.rstrip("|").strip()   # any residual column separator
    m = re.match(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{4})$", s)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return f"{d:02d}/{mo:02d}/{y}" if 1 <= mo <= 12 else None
    m = re.match(r"^(\d{4})[/-](\d{1,2})[/-](\d{1,2})$", s)
    if m:
        return f"{int(m.group(3)):02d}/{int(m.group(2)):02d}/{m.group(1)}"
    m = re.match(r"^(\d{1,2})\s+([A-Za-z]{3,})\.?\s*,?\s*(\d{4})$", s)
    if m:
        mo = _MONTHS.get(m.group(2)[:3].lower())
        if mo:
            return f"{int(m.group(1)):02d}/{mo:02d}/{m.group(3)}"
    m = re.match(r"^([A-Za-z]{3,})\.?\s+(\d{1,2})\s*,?\s*(\d{4})$", s)
    if m:
        mo = _MONTHS.get(m.group(1)[:3].lower())
        if mo:
            return f"{int(m.group(2)):02d}/{mo:02d}/{m.group(3)}"
    return None


def norm_num(v):
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = re.sub(r"[,\s₹]|(?:INR)|(?:Rs\.?)", "", str(v), flags=re.I)
    neg = s.endswith("-") or (s.startswith("(") and s.endswith(")"))
    s = s.strip("()-")
    s = re.sub(r"(?i)\s*(cr|dr)$", "", s)
    try:
        f = float(s)
    except ValueError:
        return None
    return -f if neg else f


def norm_str(v):
    if v is None:
        return None
    s = unicodedata.normalize("NFKC", str(v)).strip()
    return s or None


def norm_key(v):
    """Case/space/punctuation-insensitive form for comparing labels."""
    s = norm_str(v)
    if s is None:
        return None
    return re.sub(r"[^a-z0-9]+", "", s.lower()) or None


def norm_desc(v):
    """Description comparison key: case-folded, whitespace-collapsed. Punctuation is
    KEPT -- the prompt requires verbatim narration, so dropping it would hide a real
    fidelity defect."""
    s = norm_str(v)
    if s is None:
        return None
    return re.sub(r"\s+", " ", s).lower()


NETWORK_ALIASES = {
    "visa": "VISA", "mastercard": "MASTERCARD", "master": "MASTERCARD",
    "mastercardworld": "MASTERCARD", "rupay": "RUPAY", "amex": "AMEX",
    "americanexpress": "AMEX", "diners": "DINERS", "dinersclub": "DINERS",
    "dinersclubinternational": "DINERS",
}


def norm_network(v):
    k = norm_key(v)
    return NETWORK_ALIASES.get(k, (norm_str(v) or "").upper() or None) if k else None


# ---------------------------------------------------------------- field access

def dig(obj, path):
    cur = obj
    for p in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(p)
    return cur


def primary_card(rec):
    """The card whose fields are scored. Prefer the flagged primary; else the first.
    Never merges across cards."""
    cards = (rec or {}).get("cards")
    if not isinstance(cards, list) or not cards:
        return {}
    for c in cards:
        if isinstance(c, dict) and dig(c, "cardMeta.isPrimaryCard") is True:
            return c
    return cards[0] if isinstance(cards[0], dict) else {}


# The 16 client-priority fields. `kind` drives comparison semantics.
STMT_FIELDS = [
    ("cardDisplayName", "card", "cardMeta.cardDisplayName", "lenient_str"),
    ("lastFourDigit", "card", "cardMeta.lastFourDigit", "digits4"),
    ("network", "card", "cardMeta.network", "network"),
    ("statementLevelSummary.totalAmountDue", "root", "statementLevelSummary.totalAmountDue", "num"),
    ("statementLevelSummary.availableCreditLimit", "root",
     "statementLevelSummary.availableCreditLimit", "num"),
    ("statementLevelSummary.utilisationPercent", "root",
     "statementLevelSummary.utilisationPercent", "num"),
    ("statementLevelSummary.totalCreditLimit", "root",
     "statementLevelSummary.totalCreditLimit", "num"),
    ("statementLevelSummary.totalMinimumAmountDue", "root",
     "statementLevelSummary.totalMinimumAmountDue", "num"),
    ("statementMeta.issuerName", "root", "statementMeta.issuerName", "issuer"),
    ("statementMeta.statementDate", "root", "statementMeta.statementDate", "date"),
    ("statementMeta.dueDate", "root", "statementMeta.dueDate", "date"),
]
TXN_FIELDS = ["date", "description", "amount", "direction", "currency"]


def get_field(rec, scope, path):
    if rec is None:
        return None
    return dig(primary_card(rec), path) if scope == "card" else dig(rec, path)


def _digits4(v):
    s = norm_str(v)
    if s is None:
        return None
    d = re.sub(r"\D", "", s)
    return d[-4:] if len(d) >= 4 else (d or None)


def canon(kind, v):
    if kind == "num":
        return norm_num(v)
    if kind == "date":
        return norm_date(v)
    if kind == "digits4":
        return _digits4(v)
    if kind == "network":
        return norm_network(v)
    if kind == "issuer":
        return norm_key(v)
    if kind == "lenient_str":
        return norm_key(v)
    return norm_key(v)


def num_equal(a, b, tol=0.01):
    if a is None or b is None:
        return a is None and b is None
    return abs(a - b) <= max(tol, abs(b) * 1e-6)


def values_equal(kind, a, b):
    ca, cb = canon(kind, a), canon(kind, b)
    if kind == "num":
        if ca is None or cb is None:
            return ca is None and cb is None
        return num_equal(ca, cb)
    if kind == "issuer":
        # Any spelling of HDFC Bank is the same issuer; a DIFFERENT bank is not.
        if ca and cb and "hdfc" in ca and "hdfc" in cb:
            return True
    if kind == "lenient_str":
        # cardDisplayName is unstable run-to-run even inside the GT (measured), so
        # containment counts as agreement. Reported as LENIENT in the report.
        if ca and cb and (ca in cb or cb in ca):
            return True
    return ca == cb


# ---------------------------------------------------------------- utilisation

def derived_utilisation(rec):
    """No model emits utilisationPercent (1 of 2,636 prior calls). Derived from each
    source's OWN totals so the comparison is like-for-like."""
    tad = norm_num(dig(rec or {}, "statementLevelSummary.totalAmountDue"))
    tcl = norm_num(dig(rec or {}, "statementLevelSummary.totalCreditLimit"))
    if tad is None or tcl in (None, 0):
        return None
    return round(tad / tcl * 100, 2)


# ---------------------------------------------------------------- txn matching

def _desc_sim(a, b):
    """Description-only similarity in [0,1]. Token-set Jaccard blended with a
    character-ratio so that HDFC's mid-word truncation still matches its full form."""
    if not a or not b:
        return 1.0 if (not a and not b) else 0.0
    if a == b:
        return 1.0
    ta, tb = set(re.findall(r"[a-z0-9]+", a)), set(re.findall(r"[a-z0-9]+", b))
    jac = len(ta & tb) / len(ta | tb) if (ta or tb) else 0.0
    ratio = difflib.SequenceMatcher(None, a, b).ratio()
    pref = 1.0 if (a.startswith(b[:14]) or b.startswith(a[:14])) else 0.0
    # Space-INSENSITIVE ratio. Several HDFC layouts (measured on the pixel_play
    # family) emit broken intra-word spacing -- "Zom atofood", "Am azon Pay",
    # "Airtel Paym ents Bank Lim ited". Luna copies that verbatim as the prompt
    # requires while the incumbent silently de-spaces it, so a token-based score
    # reads 0 on rows that are plainly the same transaction (8 such rows on
    # decrypt_310396339...). Collapsing whitespace before comparing recovers them.
    # Still description-only: no scored field enters this computation.
    sa, sb = a.replace(" ", ""), b.replace(" ", "")
    flat = difflib.SequenceMatcher(None, sa, sb).ratio()
    return max(0.6 * jac + 0.4 * ratio,
               0.5 * ratio + 0.5 * pref * min(1.0, ratio + 0.3),
               flat * 0.98)


DESC_THRESHOLD = 0.55


def match_transactions(pred, gold, threshold=DESC_THRESHOLD):
    """1:1 assignment on DESCRIPTION SIMILARITY ONLY.

    Returns (pairs, unmatched_pred_idx, unmatched_gold_idx) where pairs are
    (pred_idx, gold_idx, sim). date/amount/direction/currency are deliberately NOT
    used, so per-field accuracy over the matched pairs is a real measurement.
    Order-insensitive: candidates are globally sorted by (sim desc, idx) before the
    greedy 1:1 sweep.
    """
    pred = pred if isinstance(pred, list) else []
    gold = gold if isinstance(gold, list) else []
    dp = [norm_desc((t or {}).get("description")) for t in pred]
    dg = [norm_desc((t or {}).get("description")) for t in gold]

    # Positional tie-break. HDFC repeats identical narrations heavily (one sample
    # statement has ETERNAL LIMITEDGURGAON 9x). Description-only similarity makes
    # those rows EXACTLY tied, so an arbitrary pairing inside the tie group reports
    # spurious date/amount mismatches -- measured as 34 fake date + 37 fake amount
    # errors on decrypt_705330814..., whose rows are in fact in identical order.
    # Relative print position resolves the tie. This is NOT circular: position is
    # not a scored field, and it only orders candidates that are already equal on
    # description, so a genuinely wrong date still fails its comparison.
    np_, ng = max(1, len(pred) - 1), max(1, len(gold) - 1)
    cands = []
    for i, a in enumerate(dp):
        for j, b in enumerate(dg):
            s = _desc_sim(a, b)
            if s >= threshold:
                cands.append((s, abs(i / np_ - j / ng), i, j))
    cands.sort(key=lambda c: (-c[0], c[1], c[2], c[3]))

    used_p, used_g, pairs = set(), set(), []
    for s, _pos, i, j in cands:
        if i in used_p or j in used_g:
            continue
        used_p.add(i)
        used_g.add(j)
        pairs.append((i, j, s))
    pairs.sort(key=lambda p: p[1])
    return (pairs,
            [i for i in range(len(pred)) if i not in used_p],
            [j for j in range(len(gold)) if j not in used_g])


TXN_KIND = {"date": "date", "description": "desc", "amount": "num",
            "direction": "enum", "currency": "enum"}


def txn_field_equal(f, a, b):
    k = TXN_KIND[f]
    if k == "num":
        return num_equal(norm_num(a), norm_num(b))
    if k == "date":
        return norm_date(a) == norm_date(b)
    if k == "desc":
        return norm_desc(a) == norm_desc(b)
    ca, cb = norm_key(a), norm_key(b)
    return ca == cb


def desc_fidelity(a, b):
    """Similarity of two descriptions after normalisation -- reported alongside exact
    match so 'right row, slightly re-cased narration' is distinguishable from
    'wrong row'."""
    na, nb = norm_desc(a), norm_desc(b)
    if na is None or nb is None:
        return 1.0 if na == nb else 0.0
    return difflib.SequenceMatcher(None, na, nb).ratio()


# ---------------------------------------------------------------- tallies

class FieldTally:
    """Per-field counters. `hallucinated_when_gold_null` is kept SEPARATE from
    wrong_value: fabricating a card `network` is worse than returning null."""

    def __init__(self):
        self.n = 0
        self.correct = 0
        self.wrong_value = 0
        self.null_when_populated = 0
        self.hallucinated_when_gold_null = 0
        self.both_null = 0
        self.examples = []

    def add(self, kind, pred, gold, sid=None, cap=40):
        self.n += 1
        p_null = canon(kind, pred) is None
        g_null = canon(kind, gold) is None
        if values_equal(kind, pred, gold):
            self.correct += 1
            if p_null and g_null:
                self.both_null += 1
            return
        if g_null and not p_null:
            self.hallucinated_when_gold_null += 1
            tag = "HALLUCINATED"
        elif p_null and not g_null:
            self.null_when_populated += 1
            tag = "NULL_WHEN_POPULATED"
        else:
            self.wrong_value += 1
            tag = "WRONG_VALUE"
        if len(self.examples) < cap:
            self.examples.append({"sid": sid, "tag": tag,
                                  "pred": pred, "gold": gold})

    def as_dict(self):
        return {
            "n": self.n,
            "correct": self.correct,
            "accuracy": round(self.correct / self.n, 4) if self.n else None,
            "wrong_value": self.wrong_value,
            "null_when_populated": self.null_when_populated,
            "hallucinated_when_gold_null": self.hallucinated_when_gold_null,
            "both_null_counted_correct": self.both_null,
            "examples": self.examples,
        }


def load_run(outdir):
    """-> {sid: record}. Only reads; never mutates."""
    import os
    out = {}
    jd = os.path.join(outdir, "json")
    if not os.path.isdir(jd):
        return out
    for f in sorted(os.listdir(jd)):
        if not f.endswith(".json"):
            continue
        try:
            r = json.loads(open(os.path.join(jd, f)).read())
        except Exception:
            continue
        out[r.get("sid") or f[:-5]] = r
    return out


def csv_extraction(csv_row):
    """The incumbent's extraction, assembled from the nested `data` blob for
    transaction-level values and the top-level columns for statement-level ones.
    Where both carry a value the `data` blob wins (it is the parser's own output);
    the top-level column is used as a fallback only."""
    try:
        d = json.loads(csv_row.get("data") or "{}")
    except Exception:
        d = {}
    if not isinstance(d, dict):
        d = {}
    sls = dict(d.get("statementLevelSummary") or {})
    meta = dict(d.get("statementMeta") or {})
    fallback = [
        (sls, "totalAmountDue", csv_row.get("totalAmount")),
        (sls, "totalMinimumAmountDue", csv_row.get("minimumAmount")),
        (sls, "totalCreditLimit", csv_row.get("totalCardLimit")),
        (sls, "availableCreditLimit", csv_row.get("availableLimit")),
    ]
    for tgt, key, val in fallback:
        if tgt.get(key) is None and norm_num(val) is not None:
            tgt[key] = norm_num(val)
    for key, val in (("statementDate", csv_row.get("statementDate")),
                     ("dueDate", csv_row.get("dueDate"))):
        if meta.get(key) is None and norm_date(val) is not None:
            meta[key] = norm_date(val)
    return {
        "statementMeta": meta,
        "statementLevelSummary": sls,
        "cards": d.get("cards") or [],
        "transactions": d.get("transactions") if isinstance(d.get("transactions"), list) else [],
        "rewards": d.get("rewards") or {},
    }
