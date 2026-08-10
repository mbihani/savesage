#!/usr/bin/env python3
"""Adjudicate every Luna-vs-incumbent-CSV disagreement AGAINST THE PDF.

The CSV is the incumbent parser's output, not truth, so a disagreement is not
evidence of a Luna error. Each one is resolved against the printed PDF using
PyMuPDF page/coordinate evidence and classified:

  LUNA_WRONG        the PDF supports the incumbent's value
  CSV_WRONG         the PDF supports Luna's value
  BOTH_WRONG        the PDF supports neither
  AMBIGUOUS_IN_PDF  the PDF does not decide it

Two ICICI-specific evidence rules, both established from the printed PDFs and
applied uniformly (never per-statement special-casing):

  * NETWORK. The ONLY network mention in an ICICI statement is the fuel-surcharge
    disclaimer, which names all four networks and identifies none. So a non-null
    network is unsupported unless the token appears OUTSIDE that sentence.
  * SIGNED AMOUNTS / programType / date FORMAT. The client's own prompt pins the
    contract (amount always positive; programType is a type not a wallet name;
    dates DD/MM/YYYY). A value that breaks the client's stated contract is wrong
    even where the PDF is silent -- recorded as CONTRACT_VIOLATION with the rule cited.

A corrected score is then reported: agreement with the PDF, not with the incumbent.
"""
import json
import os
import re
import sys
from collections import Counter, defaultdict

import fitz

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import icici_lib as L
import score_lib as S

HERE = L.HERE
LUNA_DIR = os.path.join(HERE, "luna_refined")
DISCLAIMER = re.compile(
    r"For\s+RuPay\s*/\s*American\s+Express\s*/?\s*Visa\s*/\s*Mastercard\s+Credit\s+Cards",
    re.I)
# the pre-printed Minimum-Amount-Due worked example (specimen values, no cardholder)
BOILERPLATE = re.compile(
    r"On statement dated\s+\w+\s+\d{1,2},\s*\d{4},\s*following Minimum Amount Due is calculated",
    re.I)

SCALARS = ["statementMeta.issuerName", "statementMeta.statementDate", "statementMeta.dueDate",
           "statementLevelSummary.totalAmountDue", "statementLevelSummary.totalMinimumAmountDue",
           "statementLevelSummary.totalCreditLimit",
           "statementLevelSummary.availableCreditLimit"]


class Pdf:
    """Page text + a numeric/token locator that reports page and bbox."""

    def __init__(self, path):
        self.doc = fitz.open(path)
        self.pages = [p.get_text("text") for p in self.doc]
        self.full = "\n".join(self.pages)
        self.boiler_pages = {i for i, t in enumerate(self.pages, 1) if BOILERPLATE.search(t)}

    def close(self):
        self.doc.close()

    def find(self, needle, exclude_disclaimer=False, exclude_boilerplate=False):
        """-> list of {page, bbox, snippet}. Case-insensitive, WHITESPACE-FLEXIBLE.

        Whitespace-flexible is not a nicety: PyMuPDF's page text carries the PDF's own
        hard line breaks, so ICICI's card names genuinely extract as
        "Amazon Pay ICICI Bank\\nCredit Card". An exact-substring probe reports those as
        absent, and the caller turns "absent" into a fabrication verdict -- so the
        earlier exact-match version manufactured LUNA_WRONG on values the PDF really
        does print. Every run of whitespace in the needle therefore matches any run of
        whitespace (including newlines) in the page.
        """
        if needle is None or str(needle).strip() == "":
            return []
        pat = r"\s+".join(re.escape(w) for w in str(needle).split())
        hits = []
        for pno, page in enumerate(self.doc, 1):
            txt = self.pages[pno - 1]
            for m in re.finditer(pat, txt, re.I):
                a = max(0, m.start() - 110)
                snip = re.sub(r"\s+", " ", txt[a:m.end() + 110])
                if exclude_disclaimer and DISCLAIMER.search(snip):
                    continue
                if exclude_boilerplate and pno in self.boiler_pages:
                    continue
                # search_for is itself literal, so a needle spanning the PDF's line break
                # yields no rect. Fall back to the matched text, then to its first word,
                # so a confirmed textual hit still carries a coordinate.
                rects = (page.search_for(m.group(0)[:60])
                         or page.search_for(str(needle)[:60])
                         or page.search_for(str(needle).split()[0]))
                hits.append({"page": pno,
                             "bbox": [round(v, 1) for v in rects[0]] if rects else None,
                             "snippet": snip})
                break
        return hits

    def has_number(self, v, exclude_boilerplate=True):
        """A money/number value, in any of ICICI's printed spellings."""
        if v is None:
            return []
        try:
            f = float(v)
        except (TypeError, ValueError):
            return []
        forms = set()
        for x in (f, abs(f)):
            forms.add(f"{x:,.2f}")
            forms.add(f"{x:,.0f}")
            if x == int(x):
                forms.add(f"{int(x):,}")
                forms.add(str(int(x)))
            forms.add(f"{x:.2f}")
            # INDIAN DIGIT GROUPING (lakh/crore): ICICI prints 100000 as "1,00,000.00", never
            # "100,000.00". Without these forms the probe reported a printed credit limit as
            # absent and the caller called it a Luna fabrication (238910814).
            forms |= {_indian(x, 2), _indian(x, 0)}
        out = []
        for s in forms:
            out += self.find(s, exclude_boilerplate=exclude_boilerplate)
        return out


def _indian(x, dp):
    """Format with Indian digit grouping: 100000 -> '1,00,000'; 1234567.5 -> '12,34,567.50'."""
    neg = x < 0
    x = abs(x)
    whole = int(x)
    frac = f"{x - whole:.{dp}f}"[2:] if dp else ""
    s = str(whole)
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts + [tail])
    return ("-" if neg else "") + s + (("." + frac) if dp else "")


def verdict_for(field, luna, csv, pdf):
    """-> (classification, reason, evidence)"""
    ln = luna is None or (isinstance(luna, str) and not luna.strip())
    cn = csv is None or (isinstance(csv, str) and not csv.strip())

    # ---------- network: the disclaimer is not evidence
    if field == "cards[].cardMeta.network":
        # WORD-BOUNDED, or a merchant/city name supplies a false positive: "RAVI RAYS
        # VISAKHAPATNAM IN" contains "VISA", which made a substring probe report Visa as
        # supported outside the disclaimer on 1381311717 and charged Luna's correct null as
        # LUNA_WRONG. Only a standalone network token counts as evidence.
        def net_supported(tok):
            if not tok:
                return []
            pat = re.compile(r"(?<![A-Za-z])" + r"\s+".join(re.escape(w) for w in str(tok).split())
                             + r"(?![A-Za-z])", re.I)
            out = []
            for pno, t in enumerate(pdf.pages, 1):
                for m in pat.finditer(t):
                    a = max(0, m.start() - 160)
                    snip = re.sub(r"\s+", " ", t[a:m.end() + 160])
                    if DISCLAIMER.search(snip):
                        continue
                    out.append({"page": pno, "bbox": None, "snippet": snip})
                    break
            return out

        if not cn:
            ev = net_supported(csv)
            if not ev:
                if ln:
                    return ("CSV_WRONG",
                            "network appears only inside the four-network fuel-surcharge "
                            "disclaimer, which identifies no card; Luna's null is correct", [])
                lev = net_supported(luna)
                if not lev:
                    return ("BOTH_WRONG",
                            "neither network token appears outside the disclaimer", [])
                return ("CSV_WRONG", "only Luna's network token is supported outside the "
                        "disclaimer", lev)
            if ln:
                return ("LUNA_WRONG", "the incumbent's network IS printed outside the "
                        "disclaimer; Luna returned null", ev)
        if not ln:
            lev = net_supported(luna)
            if not lev:
                return ("LUNA_WRONG", "Luna emitted a network with no support outside the "
                        "four-network disclaimer (fabrication)", [])
        return ("AMBIGUOUS_IN_PDF", "both null or both supported", [])

    # ---------- utilisationPercent is NEVER PRINTED on an ICICI statement
    # Measured: 0 of 304 PDFs contain the string "utilis"/"utiliz" at all. So an
    # as-extracted figure cannot be adjudicated as an extraction at all -- the incumbent
    # DERIVED it (it emits one on 155/304), and Luna's null is the correct extraction under
    # the client's own MISSING_DATA_RULE. Classifying Luna's null as LUNA_WRONG here would
    # charge Luna for declining to fabricate.
    if field.startswith("statementLevelSummary.utilisationPercent"):
        if ln and not cn:
            return ("CSV_WRONG",
                    "no ICICI PDF prints a utilisation figure (0/304 contain 'utilis'); the "
                    "incumbent's value is DERIVED, not extracted, so Luna's null is the "
                    "correct extraction", [])
        return ("AMBIGUOUS_IN_PDF", "utilisationPercent is not printed in any ICICI PDF", [])

    # ---------- contract violations pinned by the CLIENT'S OWN prompt
    if field == "transactions[].amount":
        try:
            if csv is not None and float(csv) < 0 and luna is not None and \
                    abs(abs(float(csv)) - abs(float(luna))) < 0.01:
                return ("CSV_WRONG",
                        "CONTRACT_VIOLATION: the client prompt states transactions.amount is "
                        "ALWAYS positive and must never be negated; the incumbent negated a "
                        "credit, Luna reported the magnitude", pdf.has_number(luna))
        except (TypeError, ValueError):
            pass
    if field == "rewards.programType":
        wallets = ("amazon pay", "my cash", "adani one", "makemytrip", "payback", "balance")
        if csv and any(w in str(csv).lower() for w in wallets):
            return ("CSV_WRONG",
                    "CONTRACT_VIOLATION: the client prompt states 'DO NOT copy payment methods "
                    "or wallet names as programType'; the incumbent returned a wallet/co-brand "
                    "name", [])
    if field in ("statementMeta.statementDate", "statementMeta.dueDate"):
        if luna and csv and S.date_norm(luna) == S.date_norm(csv):
            return ("AMBIGUOUS_IN_PDF", "same day, format difference only", [])
        if csv and re.match(r"^[A-Za-z]+\s+\d{1,2},\s*\d{4}$", str(csv).strip()):
            return ("CSV_WRONG",
                    "CONTRACT_VIOLATION: the client prompt requires DD/MM/YYYY for all date "
                    "fields; the incumbent emitted long-form English", [])

    # ---------- currency is DERIVED FROM A GLYPH, never printed as a code
    # ICICI prints the rupee as `₹` or, under the RupeeForadian font, as a BACKTICK -- the
    # string "INR" appears nowhere (verified: 0 hits on statements where Luna emits INR on
    # every row). So "is the value printed in the PDF" is the wrong test here and the generic
    # branch below scored Luna's correct INR as a fabrication 13x on one statement.
    # The real question is whether the row is a FOREIGN row, which ICICI marks by printing an
    # ISO code in the `Intl.# amount` column (e.g. "88.43 USD 8,629.20").
    if field == "transactions[].currency":
        lu, cv = (str(luna).upper() if luna else None), (str(csv).upper() if csv else None)
        codes = {c for c in re.findall(r"\b(USD|EUR|GBP|AED|SGD|AUD|CAD|JPY|CHF|THB|MYR)\b",
                                      pdf.full)}
        # A non-INR claim needs that code printed somewhere; INR needs nothing (it is the default).
        l_ok = (lu in (None, "INR")) or (lu in codes)
        c_ok = (cv in (None, "INR")) or (cv in codes)
        if l_ok and not c_ok:
            return ("CSV_WRONG", f"the incumbent claims {cv} but no such code is printed", [])
        if c_ok and not l_ok:
            return ("LUNA_WRONG", f"Luna claims {lu} but no such code is printed", [])
        if lu == "INR" and cv in codes:
            # Both defensible: the PDF shows a foreign code AND a rupee amount on that row.
            # Which is right depends on WHICH amount was reported -- out of scope for a
            # field-local verdict, so it is not charged to either side.
            return ("AMBIGUOUS_IN_PDF",
                    f"row carries a foreign code ({cv}) and a rupee amount; correctness depends "
                    f"on which amount was reported, not on the currency alone", [])
        return ("AMBIGUOUS_IN_PDF", "currency is inherited from the statement default, not "
                "printed per row", [])

    # ---------- cardDisplayName: a CARDHOLDER NAME is a contract violation
    # The client prompt: "extract only the credit card product name ... Do NOT include the
    # cardholder's name or account holder name." A personal name here is wrong even though the
    # PDF does print it, so the generic "is it printed?" test gets this exactly backwards.
    if field == "cards[].cardMeta.cardDisplayName":
        def looks_like_person(v):
            s = str(v or "").strip()
            if not s or re.search(r"(?i)\b(card|bank|icici|coral|sapphiro|rubyx|emeralde|"
                                  r"platinum|amazon|adani|hpcl|expressions|mine|manu|"
                                  r"makemytrip|mmt)\b", s):
                return False
            return bool(re.match(r"(?i)^(mr|mrs|ms|miss|dr|shri|smt)\b", s)) or (
                s.isupper() and 1 < len(s.split()) <= 4)
        if looks_like_person(csv) and not looks_like_person(luna):
            return ("CSV_WRONG",
                    "CONTRACT_VIOLATION: the incumbent put the CARDHOLDER'S NAME in "
                    "cardDisplayName; the client prompt forbids it", [])
        if looks_like_person(luna) and not looks_like_person(csv):
            return ("LUNA_WRONG",
                    "CONTRACT_VIOLATION: Luna put the cardholder's name in cardDisplayName", [])
        # The product name is frequently NOT IN THE TEXT LAYER AT ALL. Measured: on 123 of 298
        # product-labelled PDFs the product token (Coral/Sapphiro/Rubyx/...) has ZERO text hits,
        # while the page carries ~10-14 images and the statement prints only the generic
        # "ICICI Bank Credit Card". Both Luna and the Opus GT nonetheless return the correct
        # product on those files, matching the filename -- i.e. both are reading the CARD-ART
        # IMAGE, which a PyMuPDF text probe cannot see. A text-only adjudicator therefore CANNOT
        # judge this field, and the generic "is it printed?" branch was labelling correct
        # image-derived answers as Luna fabrications. Declared undecidable rather than guessed.
        return ("AMBIGUOUS_IN_PDF",
                "cardDisplayName is often carried only by the card-art IMAGE, not the text "
                "layer (123/298 PDFs have zero text hits for their own product name); a "
                "text-based probe cannot adjudicate it", [])

    # ---------- description: distinguish PRINTED-SPACING fidelity from CORRUPTION
    # ICICI wraps long UPI narrations mid-word inside the cell, so the PDF genuinely prints
    # "Google P lay", "SWIGGY I NSTAMART", "TACO BEL L". The client prompt requires the
    # description copied EXACTLY, so silently closing that gap is a fidelity defect -- but it
    # is a DIFFERENT and much milder defect than changing letters. Both are reported, split.
    if field == "transactions[].description":
        squash = lambda s: re.sub(r"\s+", "", str(s or "")).casefold()
        pdf_sq = squash(pdf.full)
        l_sq, c_sq = squash(luna), squash(csv)
        l_in, c_in = (l_sq in pdf_sq), (c_sq in pdf_sq)
        if l_in and not c_in:
            return ("CSV_WRONG", "ignoring the PDF's intra-cell line-wrap spacing, only Luna's "
                    "narration matches the printed text", pdf.find(str(csv)[:18]))
        if c_in and not l_in:
            # letters differ -> corruption; letters identical -> spacing-only fidelity miss
            if l_sq and c_sq and l_sq != c_sq:
                return ("LUNA_WRONG", "Luna's narration does not match the printed characters "
                        "even ignoring whitespace (text corrupted, not merely re-spaced)",
                        pdf.find(str(csv)[:18]))
            return ("LUNA_WRONG", "Luna normalised the PDF's printed intra-word spacing; the "
                    "client prompt requires the description copied EXACTLY "
                    "(SPACING_FIDELITY_ONLY)", pdf.find(str(csv)[:18]))
        if l_in and c_in:
            return ("AMBIGUOUS_IN_PDF", "both narrations match the printed text once the PDF's "
                    "line-wrap spacing is ignored", [])
        return ("BOTH_WRONG", "neither narration matches the printed text even ignoring "
                "whitespace", [])

    # ---------- DATE fields: probe every spelling ICICI actually prints
    # ICICI prints dates as long-form English WITHOUT zero-padding ("December 4, 2025"), while
    # the contract requires DD/MM/YYYY. A literal probe for "04/12/2025" therefore finds
    # nothing and the generic branch called a correct dueDate a fabrication (238910814).
    if field in ("statementMeta.statementDate", "statementMeta.dueDate",
                 "statementMeta.statementPeriodStart", "statementMeta.statementPeriodEnd"):
        import datetime as _dt

        def date_forms(v):
            n = S.date_norm(v)
            if not n or not re.fullmatch(r"\d{2}/\d{2}/\d{4}", str(n)):
                return []
            d = _dt.datetime.strptime(n, "%d/%m/%Y")
            return [n, d.strftime("%d-%m-%Y"), d.strftime("%Y-%m-%d"),
                    d.strftime("%B %d, %Y"), d.strftime("%b %d, %Y"),
                    f"{d.strftime('%B')} {d.day}, {d.year}", f"{d.strftime('%b')} {d.day}, {d.year}",
                    f"{d.day} {d.strftime('%B')} {d.year}", f"{d.day} {d.strftime('%b')} {d.year}",
                    d.strftime("%d %b %Y"), d.strftime("%d %B %Y")]

        def date_hits(v):
            for f_ in date_forms(v):
                h = pdf.find(f_)
                if h:
                    return h
            return []

        lev, cev = date_hits(luna), date_hits(csv)
        if not ln and cn:
            return (("CSV_WRONG", "Luna's date is printed; the incumbent returned null", lev)
                    if lev else
                    ("LUNA_WRONG", "Luna emitted a date not printed in the PDF", []))
        if ln and not cn:
            return (("LUNA_WRONG", "the incumbent's date is printed; Luna returned null", cev)
                    if cev else
                    ("CSV_WRONG", "the incumbent's date is not printed in the PDF", []))
        if not ln and not cn:
            if S.date_norm(luna) == S.date_norm(csv):
                return ("AMBIGUOUS_IN_PDF", "same day, format difference only", [])
            if lev and not cev:
                return ("CSV_WRONG", "only Luna's date is printed", lev)
            if cev and not lev:
                return ("LUNA_WRONG", "only the incumbent's date is printed", cev)
            return ("AMBIGUOUS_IN_PDF", "neither or both dates located in the PDF", [])

    # ---------- generic: whose value does the PDF actually print?
    numeric = (field in S.NUMF) or field.startswith("statementLevelSummary.") \
        or field == "transactions[].amount"
    lev = pdf.has_number(luna) if numeric else pdf.find(luna, exclude_boilerplate=True)
    cev = pdf.has_number(csv) if numeric else pdf.find(csv, exclude_boilerplate=True)
    if not ln and not cn:
        if lev and not cev:
            return ("CSV_WRONG", "only Luna's value is printed in the PDF", lev)
        if cev and not lev:
            return ("LUNA_WRONG", "only the incumbent's value is printed in the PDF", cev)
        if not lev and not cev:
            return ("BOTH_WRONG", "neither value is printed in the PDF", [])
        return ("AMBIGUOUS_IN_PDF", "both values appear in the PDF", (lev or [])[:1] + (cev or [])[:1])
    if ln and not cn:
        if cev:
            return ("LUNA_WRONG", "the incumbent's value is printed; Luna returned null", cev)
        return ("CSV_WRONG", "the incumbent's value is NOT printed in the PDF; null is correct", [])
    if cn and not ln:
        if lev:
            return ("CSV_WRONG", "Luna's value is printed; the incumbent returned null", lev)
        return ("LUNA_WRONG", "Luna emitted a value not printed in the PDF (fabrication)", [])
    return ("AMBIGUOUS_IN_PDF", "both null", [])


def main():
    corpus = L.discover_pdfs()
    path_by_sid = {sid: p for sid, _, p in corpus}
    name_by_sid = {sid: f for sid, f, _ in corpus}
    by_csv, _ = L.load_csv_incumbent()
    luna = S.load_arm(LUNA_DIR)

    tally = Counter()
    per_field = defaultdict(Counter)
    items = []

    for sid in sorted(luna, key=lambda s: (len(s), s)):
        p = S.model_as_extraction(luna[sid])
        e = by_csv.get(name_by_sid.get(sid))
        if p is None or e is None:
            continue
        c = S.csv_as_extraction(e)
        sc = S.score_statement(p, c, sid)
        bad = []
        for f, rows in sc["fields"].items():
            base = f.replace("@extracted", "").replace("@derived", "")
            if base not in S.PRIORITY:
                continue
            if f.endswith("@derived"):
                continue    # arithmetic, not extraction -- not adjudicable against the PDF
            for r in rows:
                if r["verdict"] in ("wrong_value", "null_when_populated",
                                    "hallucinated_when_null"):
                    bad.append((f, r))
        if not bad:
            continue
        pdf = Pdf(path_by_sid[sid])
        try:
            for f, r in bad:
                cls, why, ev = verdict_for(f, r["pred"], r["ref"], pdf)
                tally[cls] += 1
                per_field[f][cls] += 1
                items.append({"statement_id": sid, "pdf": name_by_sid[sid], "field": f,
                              "luna": r["pred"], "csv": r["ref"],
                              "scorer_verdict": r["verdict"], "adjudication": cls,
                              "reason": why, "pdf_evidence": ev[:2]})
        finally:
            pdf.close()

    out = {
        "n_luna_statements_adjudicated": len(luna),
        "n_disagreements": sum(tally.values()),
        "tally": dict(tally),
        "per_field": {k: dict(v) for k, v in sorted(per_field.items())},
        "items": items,
    }
    dest = os.path.join(HERE, "adjudication.json")
    with open(dest, "w") as fh:
        json.dump(out, fh, indent=1, default=str)
    print(f"wrote {dest}")
    print(f"\ndisagreements adjudicated: {sum(tally.values())}")
    for k, v in tally.most_common():
        print(f"  {v:>5}  {k}")
    print("\nby field:")
    for f, c in sorted(per_field.items(), key=lambda x: -sum(x[1].values())):
        print(f"  {f:<52} {dict(c)}")


if __name__ == "__main__":
    main()
