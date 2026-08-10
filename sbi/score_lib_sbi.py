"""Scoring for the SBI corpus against TWO references, with a NON-CIRCULAR matcher.

Normalisers (text/num/date_norm/direction/lenient_hit/strip_provenance) are
imported from the project's canonical scorer so SBI is judged by exactly the same
yardstick as the Axis arms.

WHY A DESCRIPTION-ONLY MATCHER. The canonical `score.match_txs` pairs rows on the
composite key (date, amount, direction) and then reports accuracy for date, amount
and direction -- any row that matched did so *because* those three agreed, so all
three come out ~100% by construction. This bug already bit this project once. This
scorer matches on DESCRIPTION SIMILARITY ONLY, with an enforced 1:1,
order-insensitive assignment, then scores date/amount/direction/currency inside
the matched pairs, so those four numbers are earned rather than tautological.

The two references are NOT equivalent:
  * `csv` = the incumbent GEMINI parser's own output (detectionSource=GEMINI,
            modelName in {gemini-3-flash-preview, databricks-gemini-3-flash}).
            A difference here is DISAGREEMENT WITH THE INCUMBENT, not an error.
  * `gt`  = the Opus-5 native-PDF pass over these same statements.
"""

import csv as _csv
import json
import os
import re
import sys
import urllib.parse
from decimal import Decimal
from difflib import SequenceMatcher

sys.path.insert(0, "/Users/mayanck.bihani/Savesage/bakeoff/scorer")
import score as sc  # noqa: E402  canonical normalisers

text, num, direction, lenient_hit = (sc.text, sc.num, sc.direction, sc.lenient_hit)

# The canonical date_norm does NOT understand SBI's dominant transaction-date format,
# the 2-DIGIT-YEAR "DD Mon YY" ('22 Jun 26'). It returns such a value UNCHANGED, so
# '22 Jun 26' != '22/06/2026' and every comparison against it scores wrong_value.
# MEASURED on this corpus: 2,733 of 3,769 incumbent transaction dates (72.5%) are
# 'DD Mon YY', while Luna and Opus both emit DD/MM/YYYY on 100% of rows -- so the
# unpatched normaliser would have charged the incumbent a spurious 72.5% date defect
# and corrupted the matcher's date tie-break at the same time. Wrapped rather than
# edited in place so every other bank keeps the identical canonical yardstick.
_SBI_DMY = re.compile(r"^\s*(\d{1,2})[\s/-]+([A-Za-z]{3})[a-z]*[\s,/-]+(\d{2}|\d{4})\s*$")
_MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}


def date_norm(v):
    """Canonical date_norm + the 'DD Mon YY' form SBI actually prints.

    A 2-digit year is expanded as 20YY: this corpus spans 2021-2026 statement dates,
    so there is no 19xx ambiguity to resolve.
    """
    if v is None:
        return None
    m = _SBI_DMY.match(str(v))
    if m:
        mon = _MONTHS.get(m.group(2).lower())
        if mon:
            y = int(m.group(3))
            return f"{int(m.group(1)):02d}/{mon:02d}/{2000 + y if y < 100 else y}"
    return sc.date_norm(v)

ROOT = os.path.dirname(os.path.abspath(__file__))
CSVP = "/Users/mayanck.bihani/Downloads/remaining pdfs ground truth/sbi.csv"
PDFR = "/Users/mayanck.bihani/Downloads/remaining pdfs ground truth/sbi-pdfs"

# ------------------------------------------------------------------ the 16
PRIORITY = [
    "cards[].cardMeta.cardDisplayName", "cards[].cardMeta.lastFourDigit",
    "cards[].cardMeta.network",
    "statementLevelSummary.totalAmountDue", "statementLevelSummary.availableCreditLimit",
    "statementLevelSummary.utilisationPercent", "statementLevelSummary.totalCreditLimit",
    "statementLevelSummary.totalMinimumAmountDue",
    "statementMeta.issuerName", "statementMeta.statementDate", "statementMeta.dueDate",
    "transactions[].date", "transactions[].description", "transactions[].amount",
    "transactions[].direction", "transactions[].currency",
]
SECONDARY = [
    "statementMeta.statementPeriodStart", "statementMeta.statementPeriodEnd",
    "statementMeta.rawStatementId",
    "cards[].cardMeta.productFamily", "cards[].cardMeta.isPrimaryCard",
    "cards[].bigPicture.cardCreditLimit", "cards[].bigPicture.cardAvailableCreditLimit",
    "rewards.programType", "rewards.openingPoints", "rewards.pointsEarnedThisCycle",
    "rewards.pointsRedeemedThisCycle", "rewards.closingPoints",
    "rewards.pointsExpiringNext30Days", "rewards.pointsExpiringNext60Days",
    "rewards.bonusPointsThisCycle",
    "transactions[].txnType", "transactions[].rewardPointsOnThisTransaction",
]
TXN_FIELDS = ["date", "description", "amount", "direction", "currency",
              "txnType", "rewardPointsOnThisTransaction"]

# An ISO-vs-DD/MM/YYYY difference is a FORMAT difference, not a wrong day.
DATEF = {"statementMeta.statementDate", "statementMeta.dueDate",
         "statementMeta.statementPeriodStart", "statementMeta.statementPeriodEnd",
         "transactions[].date"}
NUMF = ({f for f in PRIORITY + SECONDARY
         if f.startswith("statementLevelSummary.") or f.startswith("rewards.")
         or f.startswith("cards[].bigPicture.")} - {"rewards.programType"}) | {
    "transactions[].amount", "transactions[].rewardPointsOnThisTransaction"}
# Judgement-laden derived labels -> scored leniently (substring containment either
# way), per the GT exclusion manifest. Reported separately so the leniency is visible.
LENIENT = {"cards[].cardMeta.cardDisplayName", "cards[].cardMeta.productFamily"}


# ------------------------------------------------------------------ loading
def _sid(filename):
    m = re.match(r"^decrypt_(?:encrypt_)?(\d+)_", filename)
    if m:
        return m.group(1)
    m = re.match(r"^decrypt_(gmail:\d+):", filename)
    if m:
        return m.group(1).replace(":", "_")
    return None


def load_corpus():
    out = []
    for f in sorted(os.listdir(PDFR)):
        if not f.lower().endswith(".pdf"):
            continue
        s = _sid(f)
        if s is None:
            raise RuntimeError(f"PDF off-convention: {f}")
        out.append((s, f, os.path.join(PDFR, f)))
    ids = [x[0] for x in out]
    assert len(set(ids)) == len(ids), "duplicate statement ids"
    return sorted(out, key=lambda t: (0, int(t[0])) if t[0].isdigit() else (1, 0, t[0]))


def load_csv_incumbent():
    """statement_id -> the nested `data` blob (the authoritative incumbent record).

    MAPPING DECISION, measured not assumed. Contrary to the Axis corpus, the SBI
    blob DOES carry `statementMeta` and `statementLevelSummary` -- 315/315 rows
    contain both keys -- alongside `cards`, `rewards` and `transactions`. The blob
    is therefore used whole, and the top-level columns are treated as a lossy
    projection of it:

      * `availableLimit` differs from the blob's availableCreditLimit on 247/315
        rows, and 247/247 of those differences are exactly int(blob) -- i.e. the
        column is an integer truncation. Using the column would charge 247
        statements a spurious fractional error.
      * `dueDate` is blank on 56/315 columns while the blob carries it.
      * `cycleStartDate` / `cycleEndDate` are blank on 315/315 columns while the
        blob's statementPeriodStart is populated on 304/315.
      * `statementDate` column disagrees with the blob on 1/315 rows.
      * every `*Text` column is empty (315/315).

    The columns are still captured in `meta` per statement so any column-level
    claim can be re-checked without re-reading the CSV.
    """
    _csv.field_size_limit(10 ** 9)
    out, meta = {}, {}
    for r in _csv.DictReader(open(CSVP, encoding="utf-8")):
        b = os.path.basename(urllib.parse.unquote(urllib.parse.urlparse(r["link"]).path))
        s = _sid(b)
        if s is None:
            continue
        blob = json.loads(r["data"]) if r.get("data") else {}
        blob.setdefault("statementMeta", {})
        blob.setdefault("statementLevelSummary", {})
        out[s] = blob
        meta[s] = {k: r.get(k) for k in
                   ("id", "detectionSource", "modelName", "totalAmount", "minimumAmount",
                    "availableLimit", "totalCardLimit", "statementDate", "dueDate",
                    "rewardPoints", "pointExpiry30", "pointExpiry60", "link")}
    return out, meta


_MON = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}


def _csv_date(v):
    """'1 Jul, 2026, 12:00 AM' -> '01/07/2026'. Returns None on blank/unparseable."""
    if not v or not str(v).strip():
        return None
    m = re.match(r"\s*(\d{1,2})\s+([A-Za-z]{3})[a-z]*,?\s+(\d{4})", str(v))
    if not m:
        return None
    d, mon, y = int(m.group(1)), _MON.get(m.group(2).lower()), int(m.group(3))
    return f"{d:02d}/{mon:02d}/{y}" if mon else None


def load_arm(outdir):
    """statement_id -> the persisted per-call record."""
    out = {}
    d = os.path.join(outdir, "json")
    if not os.path.isdir(d):
        return out
    for f in sorted(os.listdir(d)):
        if f.endswith(".json"):
            out[f[:-5]] = json.loads(open(os.path.join(d, f)).read())
    return out


def parsed_of(rec):
    """The scoreable payload of a run record, or {} for any non-parsing outcome.

    A failed call scores as a total miss and is NEVER dropped from the denominator;
    its failure_class travels separately so infrastructure != model defect.
    """
    if not isinstance(rec, dict):
        return {}
    if rec.get("outcome") not in ("OK", "TRUNCATED_BUT_PARSED", "ESCAPED_TRANSACTIONS_STRING"):
        return {}
    p = rec.get("parsed_json")
    return sc.strip_provenance(p) if isinstance(p, dict) else {}


# ------------------------------------------------------------------ compare
def norm4(v):
    """lastFourDigit: keep only real trailing digits. Both the incumbent ('XX65')
    and SBI's own print ('XXXX XXXX XXXX XX21') leak the mask; a mask is not a digit.
    NOTE: SBI masks to only the last TWO digits on many statements, so a 2-digit
    value is legitimate here and must not be treated as malformed."""
    if v is None:
        return None
    s = re.sub(r"[^0-9]", "", str(v))
    return s[-4:] if s else None


def cmp_scalar(field, a, g):
    """-> (verdict, kind). verdict in correct / wrong_value / null_when_populated /
    hallucinated_when_null / both_null. `kind` flags a pure-format or lenient pass."""
    a_null = a is None or (isinstance(a, str) and not a.strip())
    g_null = g is None or (isinstance(g, str) and not g.strip())
    if a_null and g_null:
        return "both_null", None
    if a_null:
        return "null_when_populated", None
    if g_null:
        return "hallucinated_when_null", None

    if field == "cards[].cardMeta.lastFourDigit":
        if norm4(a) == norm4(g):
            return "correct", (None if str(a) == str(g) else "FORMAT")
        # SBI masks to 2 digits on many statements; a 4-digit value whose last 2
        # agree with a 2-digit reference is a MASK-DEPTH difference, not a wrong
        # card. Counted correct with the kind recorded so it stays visible.
        na, ng = norm4(a), norm4(g)
        if na and ng and (na.endswith(ng) or ng.endswith(na)):
            return "correct", "MASK_DEPTH"
        return "wrong_value", None
    if field in NUMF:
        na, ng = num(a), num(g)
        if na is not None and ng is not None and abs(na - ng) <= Decimal("0.01"):
            return "correct", None
        return "wrong_value", None
    if field in DATEF:
        da, dg = date_norm(a), date_norm(g)
        if da == dg:
            return "correct", (None if str(a) == str(g) else "FORMAT")
        return "wrong_value", None
    if field in LENIENT:
        if text(a) == text(g):
            return "correct", None
        if lenient_hit(a, g):
            return "correct", "LENIENT"
        return "wrong_value", None
    if field == "cards[].cardMeta.isPrimaryCard":
        return ("correct", None) if bool(a) == bool(g) else ("wrong_value", None)
    if text(a) == text(g):
        return "correct", (None if str(a) == str(g) else "FORMAT")
    return "wrong_value", None


def align_cards(pred_cards, ref_cards):
    """Pair cards by real trailing digits; fall back to index for the remainder.
    Card-count mismatch is reported separately, never silently absorbed."""
    pc, rc = list(pred_cards or []), list(ref_cards or [])
    pairs, used_p = [], set()
    for r in rc:
        r4 = norm4(((r or {}).get("cardMeta") or {}).get("lastFourDigit"))
        hit = None
        if r4:
            for i, p in enumerate(pc):
                if i in used_p:
                    continue
                p4 = norm4(((p or {}).get("cardMeta") or {}).get("lastFourDigit"))
                if p4 and (p4 == r4 or p4.endswith(r4) or r4.endswith(p4)):
                    hit = i
                    break
        if hit is None:
            for i in range(len(pc)):
                if i not in used_p:
                    hit = i
                    break
        if hit is not None:
            used_p.add(hit)
            pairs.append((pc[hit], r))
        else:
            pairs.append((None, r))
    for i, p in enumerate(pc):
        if i not in used_p:
            pairs.append((p, None))
    return pairs, len(pc), len(rc)


def dig(obj, path):
    cur = obj
    for p in path.split("."):
        cur = cur.get(p) if isinstance(cur, dict) else None
    return cur


def util_derive(obj):
    """utilisationPercent as-derived under the contract's pinned formula
    round(totalAmountDue / totalCreditLimit * 100, 2). No SBI PDF prints a
    utilisation figure, so this is arithmetic rather than extraction. Identical
    for GT, incumbent and Luna so the three are derived the same way."""
    tad = dig(obj, "statementLevelSummary.totalAmountDue")
    tcl = dig(obj, "statementLevelSummary.totalCreditLimit")
    for v in (tad, tcl):
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            return None
    if tcl == 0:
        return None
    return round(tad / tcl * 100, 2)


# ------------------------------- NON-CIRCULAR transaction matcher
def desc_sim(a, b):
    return SequenceMatcher(None, text(a) or "", text(b) or "").ratio()


def match_txns_by_description(pred, ref, threshold=0.60):
    """DESCRIPTION-ONLY, order-insensitive, provably 1:1.

    Every (pred_i, ref_j) pair is scored on description similarity alone; pairs are
    consumed in globally-descending similarity order and taken only if BOTH sides
    are still free, making the assignment 1:1 by construction and independent of
    row order on either side.

    date / amount / direction / currency are DELIBERATELY not used to admit a pair
    -- they are the fields being scored. The date tie-break below applies only at
    EQUAL similarity and cannot manufacture a match: `s >= threshold` is the sole
    admission test. Without it, a statement that repeats one narration verbatim
    (SBI does this constantly -- three identical 'UPI-Blinkit' rows) gets an
    arbitrary pairing and the scorer then charges the arbitrary permutation as
    date/amount defects, which is a matcher artifact rather than an extraction
    error. A genuinely wrong date still scores wrong.
    """
    pred, ref = list(pred or []), list(ref or [])
    cand = []
    for i, p in enumerate(pred):
        if not isinstance(p, dict):
            continue
        for j, r in enumerate(ref):
            if not isinstance(r, dict):
                continue
            s = desc_sim(p.get("description"), r.get("description"))
            if s >= threshold:
                tb = 0 if (date_norm(p.get("date")) == date_norm(r.get("date"))) else 1
                cand.append((-s, tb, i, j))
    cand.sort()
    fp, fr, pairs = set(), set(), []
    for negs, _tb, i, j in cand:
        if i in fp or j in fr:
            continue
        fp.add(i)
        fr.add(j)
        pairs.append({"pi": i, "rj": j, "sim": -negs, "pred": pred[i], "ref": ref[j]})
    assert len({x["pi"] for x in pairs}) == len(pairs), "pred index reused"
    assert len({x["rj"] for x in pairs}) == len(pairs), "ref index reused"
    return (pairs,
            [pred[i] for i in range(len(pred)) if i not in fp],
            [ref[j] for j in range(len(ref)) if j not in fr])
