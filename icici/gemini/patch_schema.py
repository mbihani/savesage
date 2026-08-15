"""Add null-safe enums + ICICI-specific descriptions to the converted GEMINI_SCHEMA.json.

CONSTRAINT-TIGHTENING ONLY. Adds `enum` / `description` to existing leaves. Never
adds, removes or retypes a leaf -- assert_schema.py enforces that.

NULL SAFETY (the load-bearing point): the converted schema expresses nullability as
a TYPE ARRAY, e.g. "type": ["string","null"] -- not anyOf. This schema is sent with
strict:true. An enum on a nullable leaf that omits null makes a correct null
UNREPRESENTABLE: the call either hard-400s or the model is forced to invent a
non-null value where null was right. So every enum below includes null.

txnType vocabulary is taken VERBATIM from hdfc/ and sbi/ rather than narrowed to the
values this 11-statement sample happens to contain. Fitting the vocabulary to one
small sample was explicitly avoided on HDFC and keeping it identical across banks is
what makes cross-bank comparison valid.

Every description below is written from ICICI measurements in probe/ -- not copied
from HDFC or SBI, whose layouts and traps are different.
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCHEMA = os.path.join(HERE, "GEMINI_SCHEMA.json")

ENUMS = {
    ("transactions", "direction"): ["DEBIT", "CREDIT", None],
    ("transactions", "txnType"): [
        "PURCHASE", "PAYMENT", "REFUND", "REVERSAL", "CASHBACK", "FEE", "TAX",
        "INTEREST", "EMI", "CASH_ADVANCE", "UPI", None,
    ],
}

DESCRIPTIONS = {
    # --- zero prior guidance from either the prompt or the schema ---
    ("cards", "cardMeta", "productFamily"):
        "The ICICI product / co-brand series name for THIS card, with the issuer name and the "
        "generic words 'Credit Card' removed: e.g. 'Sapphiro', 'Coral', 'Rubyx', 'MakeMyTrip', "
        "'Amazon Pay', 'HPCL', 'Platinum', 'Emeralde', 'Expressions'. On the classic ICICI layout "
        "it is printed once, in the page-1 top-right identity block beside the 'ICICI Bank Credit "
        "Cards' logo. Null when that identity block shows no product name at all - some ICICI "
        "statements print only the logo and no product. NEVER infer it from the card BIN or "
        "leading digits, from the four-network fuel-surcharge disclaimer, from a marketing or "
        "cross-sell sentence elsewhere on the page, or from a merchant name in the transaction "
        "table.",
    ("rewards", "openingPoints"):
        "An OPENING or brought-forward rewards balance, only when the statement explicitly prints "
        "one with its own label. ICICI's rewards strips print cycle EARN and cycle TRANSFER cells "
        "and no opening balance, so this is normally null. Never derive it by subtracting earned "
        "from a closing figure, and never reuse the money 'Previous Balance' from the Statement "
        "Summary - that is rupees, not points.",
    ("rewards", "pointsExpiringNext30Days"):
        "The points figure printed against an explicit 'expiring in 30 days' style label. No ICICI "
        "rewards layout in this corpus prints an expiry cell, so this is normally null. A sentence "
        "in the terms and conditions describing WHEN points expire as a policy is not a value - do "
        "not turn it into a number, and never copy the earned or transferred figure here.",
    ("rewards", "pointsExpiringNext60Days"):
        "The points figure printed against an explicit 'expiring in 60 days' style label. As with "
        "the 30-day field, no ICICI layout in this corpus prints one, so this is normally null. "
        "Never computed, and never copied from the 30-day figure.",
    ("transactions", "txnType"):
        "The kind of row, from the closed list. ICICI-specific bindings, all observed in this "
        "corpus: rows reading 'Principal Amount Amortization - <n/m>MERCHANT' or 'Interest Amount "
        "Amortization - <n/m>MERCHANT' are EMI; 'IGST-CI@18%' and other GST/tax rows are TAX; "
        "'Interest Charges' is INTEREST; 'INFINITY PAYMENT RECEIVED' and 'BBPS Payment received' "
        "are PAYMENT; a narration beginning 'UPI-<ref>-' is UPI; 'Late Payment Fee', 'Processing "
        "Fee' and over-limit fees are FEE; an ordinary merchant purchase is PURCHASE. Use null "
        "only when the row genuinely fits none of these.",
    ("statementLevelSummary", "totalCreditLimit"):
        "The overall sanctioned credit limit, taken from the CREDIT SUMMARY label 'Credit Limit "
        "(Including cash)'. Do not take the adjacent 'Cash Limit', 'Available Credit (Including "
        "cash)' or 'Available Cash' figures, which sit in the same row of that block, and do not "
        "take any figure from the pre-printed illustrative Minimum-Amount-Due worked example.",
}

# isPrimaryCard is DELIBERATELY not given a description here. HDFC needed one because
# its prompt said nothing about the field (that description took it from 0 to 14
# populated). ICICI_PROMPT.txt already carries an explicit, measured isPrimaryCard rule
# and the field is populated as a boolean on every card in this corpus, so there is no
# defect for a description to fix. Adding one anyway would risk contradicting the prompt.
SKIPPED_WITH_REASON = {
    "cards[].cardMeta.isPrimaryCard": "already governed by an explicit ICICI_PROMPT rule; "
                                      "populated on 22/22 cards; no defect to fix",
    "cards[].cardMeta.network": "already governed by the evidence-first ICICI_PROMPT rule that "
                               "measured 0 fabrications; a description risks weakening it",
    "cards[].cardMeta.cardDisplayName": "identity block is vector artwork, not text; the "
                                       "reference convention is incoherent - convention decision "
                                       "belongs to the client, see PROMPT_CHANGELOG",
}


def at(schema, path):
    """Resolve a leaf path, stepping transparently through arrays."""
    node = schema
    for k in path:
        if node.get("type") == "array":
            node = node["items"]
        node = node["properties"][k]
    return node


def main():
    schema = json.load(open(SCHEMA))
    for path, vals in ENUMS.items():
        leaf = at(schema, path)
        types = leaf["type"]
        if "null" in types and None not in vals:
            sys.exit(f"refusing: {'.'.join(path)} is nullable but enum omits null")
        leaf["enum"] = vals
        print(f"enum        {'.'.join(path):46s} type={types} -> {vals}")
    for path, text in DESCRIPTIONS.items():
        leaf = at(schema, path)
        leaf["description"] = text
        print(f"description {'.'.join(path):46s} ({len(text)} chars)")
    json.dump(schema, open(SCHEMA, "w"), indent=2)
    print(f"\nwrote {SCHEMA}")
    print("\ndeliberately NOT described:")
    for k, v in SKIPPED_WITH_REASON.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
