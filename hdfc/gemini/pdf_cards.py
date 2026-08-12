"""Card-level PDF reference: printed card numbers, their last four, and any printed network.

`cards[]` is an ARRAY, and one file in this set (decrypt_495459059, Pixel Play) really is
a TWO-CARD statement -- it prints `442144-xxxxxx-1048` and `652989-xxxxxx-4493` under
separate `Card no. XX1048 - Visa` / `Card no. XX4493 - Rupay` transaction blocks. So the
reference must be a LIST, and a model emitting one card there is dropping a card rather
than merely differing.

That same file is also the only place in this 15-file corpus where a NETWORK NAME is
printed as text ("- Visa", "- Rupay"). The HDFC prompt's network rule says answer null
unless the network word is literally printed; this reference is what makes that rule
measurable instead of assumed.

Card-number candidates are filtered to the CREDIT-CARD shape (6 leading digits, a masked
middle, 4 trailing digits) so that HDFC's other long numerics -- the 19-digit Alternate
Account Number, 23-digit transaction Ref#, CKYC ids, phone numbers -- cannot be mistaken
for a card number. That filter matters: `rawStatementId`/`lastFourDigit` fabrication from
those numbers is a known failure mode.
"""

import re

import pdf_rows as P

# Leading real digits, then a masked run (X/x/*), then exactly 4 real digits. Separators
# (space/dash) allowed anywhere. This is HDFC's printed credit-card form.
# The leading run is 6-8 digits, not exactly 6: decrypt_10378 prints `00361147XXXX4148`
# (EIGHT leading digits, four-char mask). Pinning \d{6} silently found no card at all on
# that file, which would have been reported as the model inventing a lastFourDigit.
CARD = re.compile(r"\b(\d{6,8})[\s\-]*([Xx*]{4,})[\s\-]*(\d{4})\b")
# "Card no. XX1048 - Visa"  /  "Card no. XX4493 - Rupay"
CARD_NET = re.compile(r"Card\s*no\.?\s*[Xx*]*\s*(\d{4})\s*[-–]\s*([A-Za-z][A-Za-z ]{2,12})",
                      re.I)
NETWORK_WORDS = ["VISA", "MASTERCARD", "MASTER CARD", "RUPAY", "DINERS", "AMEX",
                 "AMERICAN EXPRESS"]


def extract(path):
    """-> {"cards":[{last4, printed_form, network}], "network_words_in_text":[...]}"""
    txt = P.full_text(path)
    flat = re.sub(r"[ \t]+", " ", txt)

    nets = {}
    for m in CARD_NET.finditer(flat):
        nets[m.group(1)] = m.group(2).strip()

    seen, cards = set(), []
    for m in CARD.finditer(flat):
        last4 = m.group(3)
        if last4 in seen:
            continue
        seen.add(last4)
        cards.append({
            "last4": last4,
            "printed_form": re.sub(r"\s+", " ", m.group(0)).strip(),
            "network": nets.get(last4),
        })

    present = []
    up = re.sub(r"\s+", " ", flat).upper()
    for w in NETWORK_WORDS:
        if re.search(r"\b" + w.replace(" ", r"\s*") + r"\b", up):
            present.append(w)
    return {"cards": cards, "network_words_in_text": present}
