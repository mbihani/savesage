"""Add `enum` and `description` to GEMINI_SCHEMA.json -- and NOTHING else.

convert_schema.py generates the schema from the client's line-64 type map and
deliberately adds no enums. This script applies the SBI-specific tightening on top,
so the tightening is reproducible and reviewable instead of a hand-edited JSON blob.
Run order:  convert_schema.py -> patch_schema.py -> assert_schema.py

NULLABILITY IS THE DANGER HERE. The conversion expresses nullable as a TYPE ARRAY,
`"type": ["string","null"]`, NOT anyOf. The schema is sent with strict:True, so an
enum on a nullable leaf that omits null makes a CORRECT null unrepresentable: the
decoder would either hard-400 every call or force a non-null value where null was the
right answer -- strictly worse than the problem being fixed. Every enum below
therefore INCLUDES null, and assert_schema.py enforces the biconditional
(null in type) <=> (null in enum) so this cannot regress silently.

WHICH LEAVES GET A DESCRIPTION, AND WHY THOSE FOUR
--------------------------------------------------
gap_audit.py counted guidance per leaf in sbi/SBI_PROMPT.txt and in the client's
prompt BODY (lines 1-61; line 64 is the type map and is excluded). Exactly four
leaves have ZERO guidance from BOTH prompts:

    cards[].cardMeta.productFamily
    transactions[].txnType
    rewards.pointsExpiringNext30Days
    rewards.pointsExpiringNext60Days

Those four get a description. The wording is SBI-specific and derived from this
corpus's measured layout (probe_5fields.py / probe_rewards.py), not ported from HDFC.
"""

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
SCHEMA_PATH = os.path.join(HERE, "GEMINI_SCHEMA.json")

# ---------------------------------------------------------------- enums
# direction: the model already emits only DEBIT/CREDIT on this corpus (41/152, an
# exact match to the incumbent's split), so the enum documents observed behaviour
# rather than forcing a change. null stays representable.
DIRECTION_ENUM = ["DEBIT", "CREDIT", None]

# txnType: SBI_PROMPT.txt previously carried NO txnType vocabulary at all (0 mentions,
# and the client prompt has 0 too). The vocabulary is being added to the prompt in the
# same change, and this enum mirrors it VERBATIM. It is deliberately NOT narrowed to
# the ten values this 12-statement sample happens to emit -- CASH_ADVANCE is included
# because SBI cash-withdrawal rows exist in the wider corpus even though this sample
# has none. Fitting the enum to a 12-file sample would bake the sample into the
# contract.
TXNTYPE_ENUM = ["PURCHASE", "PAYMENT", "REFUND", "REVERSAL", "CASHBACK", "FEE",
                "TAX", "INTEREST", "EMI", "CASH_ADVANCE", "UPI", None]

DESCRIPTIONS = {
    ("cards", "cardMeta", "productFamily"):
        "The SBI product / co-brand family name, i.e. cardDisplayName with the issuer "
        "words 'SBI', 'Card', 'Credit Card' and the cardholder's name removed: e.g. "
        "'CASHBACK', 'Club Vistara PRIME', 'BPCL OCTANE', 'Tata Neu Infinity', "
        "'IRCTC Platinum', 'OLA Money', 'Flipkart'. Take it from the product name in "
        "the page-1 statement header or the product line; never from a promotional "
        "offer, a fee table or a transaction narration, and never from the cardholder "
        "name. Null only when no product name is printed anywhere. NEVER infer it from "
        "the card number, its leading digits, or the payment network.",

    ("transactions", "txnType"):
        "The kind of transaction this row is, from the closed vocabulary in the prompt: "
        "PURCHASE, PAYMENT, REFUND, REVERSAL, CASHBACK, FEE, TAX, INTEREST, EMI, "
        "CASH_ADVANCE, UPI. Classify from the printed narration and the row's role: SBI "
        "prints 'PAYMENT RECEIVED ...' (PAYMENT), 'IGST DB @ 18.00%' and other tax "
        "lines (TAX), 'ANNUAL FEE CHARGED' / 'FUEL SURCHARGE WAIVER EXCL TAX' style fee "
        "and waiver lines (FEE), 'FP EMI nn/nn' Flexipay instalments (EMI), "
        "'CARD CASHBACK CREDIT' (CASHBACK). Use null when the row's kind is not "
        "determinable from what is printed; never guess a type to avoid a null.",

    ("rewards", "pointsExpiringNext30Days"):
        "The number of reward points printed as expiring within the next 30 days. On "
        "SBI statements this appears ONLY as a 'Points Expiry Details' cell inside the "
        "four-cell reward strip, and on this corpus its printed value is the word "
        "'NONE', which means zero points are expiring and must be emitted as 0. If a "
        "figure is printed instead, copy it verbatim, including a printed 0. Null when "
        "no expiry label is printed at all, which is the case on the large majority of "
        "SBI statements -- the cashback, 'Points Earned' and NeuCoins layouts print no "
        "expiry cell whatsoever. Never computed, never carried over from another field, "
        "and never inferred from a points balance.",

    ("rewards", "pointsExpiringNext60Days"):
        "The number of reward points printed as expiring within the next 60 days, taken "
        "only from a cell that distinctly says 60 days. SBI statements on this corpus "
        "print NO 60-day expiry figure anywhere, so null is the expected answer. Do NOT "
        "copy the 30-day figure into this field, do not derive it from a 'Points Expiry "
        "Details' cell that names no period, and do not compute it from a points "
        "balance. Populate it only when a 60-day figure is explicitly printed.",
}


def get(node, path):
    for k in path:
        if node.get("type") == "array":
            node = node["items"]
        node = node["properties"][k]
    while node.get("type") == "array":
        node = node["items"]
    return node


def main():
    schema = json.load(open(SCHEMA_PATH))

    d = get(schema, ("transactions", "direction"))
    assert "null" in d["type"], "direction is not nullable -- enum design assumed it was"
    d["enum"] = DIRECTION_ENUM

    t = get(schema, ("transactions", "txnType"))
    assert "null" in t["type"], "txnType is not nullable -- enum design assumed it was"
    t["enum"] = TXNTYPE_ENUM

    for path, text in DESCRIPTIONS.items():
        get(schema, path)["description"] = text

    with open(SCHEMA_PATH, "w") as fh:
        json.dump(schema, fh, indent=2)
        fh.write("\n")
    print(f"patched {SCHEMA_PATH}")
    print(f"  direction enum : {DIRECTION_ENUM}")
    print(f"  txnType   enum : {TXNTYPE_ENUM}")
    print(f"  descriptions   : {[' .'.join(p) for p in DESCRIPTIONS]}")


if __name__ == "__main__":
    main()
