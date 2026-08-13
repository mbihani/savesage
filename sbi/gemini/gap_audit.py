"""STEP 2 -- guidance-coverage audit: OUR refined SBI prompt vs the CLIENT's prompt.

THE TRAP THIS FILE EXISTS TO AVOID
---------------------------------
On HDFC, four fields looked like gaps (ours 0 mentions, client 1) but ALL FOUR of the
client's 'hits' were on line 64 -- the SCHEMA TYPE-MAP STRING, not prompt guidance.
The client gave zero guidance too; there was no gap. The client prompt BODY is
lines 1..61 only, and line 64 is EXCLUDED here.

It also lists ORPHAN rules: guidance in our prompt that governs a field the client's
26-leaf schema CANNOT emit. With additionalProperties:false those are dead
instructions at best and an instruction/schema conflict at worst. Every orphan line
is printed in full so a LIVE rule hiding inside an orphan section is not deleted by
accident -- on HDFC, BONUS_POINTS_RULE contained a live pointsEarnedThisCycle rule
that a naive section delete would have silently lost.
"""

import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
OURS = os.path.join(os.path.dirname(HERE), "SBI_PROMPT.txt")
CLIENT_SRC = "/Users/mayanck.bihani/Downloads/gemini-3-flash--prompt-shcema.txt"
CLIENT_BODY_LINES = (1, 61)          # inclusive, 1-indexed. Line 64 is the SCHEMA.
OUT = os.path.join(HERE, "probe", "gap_audit.json")

# The client's 26 leaves, exactly as the type-map names them.
LEAVES = [
    "statementMeta.issuerName", "statementMeta.statementDate", "statementMeta.dueDate",
    "statementLevelSummary.totalAmountDue", "statementLevelSummary.totalMinimumAmountDue",
    "statementLevelSummary.totalCreditLimit", "statementLevelSummary.availableCreditLimit",
    "cards[].cardMeta.cardDisplayName", "cards[].cardMeta.productFamily",
    "cards[].cardMeta.lastFourDigit", "cards[].cardMeta.network",
    "cards[].cardMeta.isPrimaryCard",
    "transactions[].date", "transactions[].description", "transactions[].amount",
    "transactions[].direction", "transactions[].txnType",
    "transactions[].rewardPointsOnThisTransaction", "transactions[].currency",
    "rewards.programType", "rewards.openingPoints", "rewards.pointsEarnedThisCycle",
    "rewards.pointsRedeemedThisCycle", "rewards.closingPoints",
    "rewards.pointsExpiringNext30Days", "rewards.pointsExpiringNext60Days",
]

# Natural-language aliases per leaf: a prompt mentions a field by prose, not by path.
ALIASES = {
    "statementMeta.issuerName": ["issuerName", "issuing bank", "issuer"],
    "statementMeta.statementDate": ["statementDate", "Statement Date"],
    "statementMeta.dueDate": ["dueDate", "Payment Due Date", "due date"],
    "statementLevelSummary.totalAmountDue": ["totalAmountDue", "Total Amount Due"],
    "statementLevelSummary.totalMinimumAmountDue": ["totalMinimumAmountDue",
                                                    "Minimum Amount Due"],
    "statementLevelSummary.totalCreditLimit": ["totalCreditLimit", "Credit Limit"],
    "statementLevelSummary.availableCreditLimit": ["availableCreditLimit",
                                                   "available credit limit"],
    "cards[].cardMeta.cardDisplayName": ["cardDisplayName", "card product name"],
    "cards[].cardMeta.productFamily": ["productFamily", "product family"],
    "cards[].cardMeta.lastFourDigit": ["lastFourDigit", "last 4", "last four"],
    "cards[].cardMeta.network": ["network", "VISA", "MasterCard", "RuPay"],
    "cards[].cardMeta.isPrimaryCard": ["isPrimaryCard", "primary card", "Primary Card"],
    "transactions[].date": ["transaction date", "Transaction Date", "date column"],
    "transactions[].description": ["description", "narration", "merchant"],
    "transactions[].amount": ["amount", "Amount"],
    "transactions[].direction": ["direction", "DEBIT", "CREDIT", "debit/credit"],
    "transactions[].txnType": ["txnType", "transaction type"],
    "transactions[].rewardPointsOnThisTransaction": ["rewardPointsOnThisTransaction"],
    "transactions[].currency": ["currency", "ISO 4217"],
    "rewards.programType": ["programType", "program type"],
    "rewards.openingPoints": ["openingPoints", "opening points", "Previous Balance"],
    "rewards.pointsEarnedThisCycle": ["pointsEarnedThisCycle", "points earned",
                                      "cashback earned"],
    "rewards.pointsRedeemedThisCycle": ["pointsRedeemedThisCycle", "points redeemed",
                                        "cashback credited", "Redeemed"],
    "rewards.closingPoints": ["closingPoints", "closing points", "Closing Points",
                              "Closing Balance", "rewards balance"],
    "rewards.pointsExpiringNext30Days": ["pointsExpiringNext30Days", "30 days",
                                         "30-day", "expiring"],
    "rewards.pointsExpiringNext60Days": ["pointsExpiringNext60Days", "60 days",
                                         "60-day"],
}

# Fields OUR prompt governs that the client's 26-leaf schema CANNOT emit.
UNEMITTABLE = [
    "financeChargesThisCycle", "bonusPointsThisCycle", "utilisationPercent",
    "statementPeriodStart", "statementPeriodEnd", "rawStatementId",
    "cardCreditLimit", "cardAvailableCreditLimit", "bigPicture",
]


def count_mentions(text, needles):
    """Word-bounded, case-insensitive, whitespace-flexible. Returns (n, lines)."""
    n, lines = 0, []
    for i, ln in enumerate(text.splitlines(), 1):
        hit = False
        for nd in needles:
            pat = r"\b" + r"\s*".join(re.escape(c) for c in nd if not c.isspace())
            pat = re.escape(nd).replace(r"\ ", r"\s+")
            if re.search(r"(?<![A-Za-z])" + pat + r"(?![A-Za-z])", ln, re.I):
                hit = True
                break
        if hit:
            n += 1
            lines.append(i)
    return n, lines


def main():
    ours = open(OURS, encoding="utf-8").read()
    raw = open(CLIENT_SRC, encoding="utf-8").read().splitlines()
    lo, hi = CLIENT_BODY_LINES
    body = "\n".join(raw[lo - 1:hi])
    # strip the python wrapper from line 1
    body = re.sub(r'^\s*SYSTEM_PROMPT\s*=\s*"""', "", body)
    body = body.rstrip().removesuffix('"""')
    schema_line = raw[63] if len(raw) > 63 else ""
    assert schema_line.lstrip().startswith("SCHEMA"), \
        f"line 64 is not the SCHEMA line: {schema_line[:60]!r}"

    rows, port_in = [], []
    for leaf in LEAVES:
        al = ALIASES[leaf]
        no, lo_ours = count_mentions(ours, al)
        nc, lo_cli = count_mentions(body, al)
        ns, _ = count_mentions(schema_line, al)
        rows.append({"leaf": leaf, "ours": no, "client_body": nc,
                     "client_schema_line64_only": ns,
                     "ours_lines": lo_ours, "client_lines": lo_cli})
        if nc > 0 and no == 0:
            port_in.append(leaf)

    print(f"{'leaf':46s} {'OURS':>5s} {'CLIENT(1-61)':>13s} {'line64':>7s}  gap?")
    for r in rows:
        gap = "GAP" if (r["client_body"] > 0 and r["ours"] == 0) else ""
        zero = "BOTH-ZERO" if (r["client_body"] == 0 and r["ours"] == 0) else ""
        print(f"{r['leaf']:46s} {r['ours']:5d} {r['client_body']:13d} "
              f"{r['client_schema_line64_only']:7d}  {gap}{zero}")

    print("\n=== PORT_IN candidates (client body has guidance, ours has none) ===")
    print(port_in or "  (none)")

    print("\n=== ORPHAN RULES in OUR prompt (govern fields the 26-leaf schema cannot emit) ===")
    orphans = []
    for i, ln in enumerate(ours.splitlines(), 1):
        for u in UNEMITTABLE:
            if re.search(r"(?<![A-Za-z])" + re.escape(u) + r"(?![A-Za-z])", ln, re.I):
                orphans.append({"line": i, "field": u, "text": ln})
                break
    for o in orphans:
        print(f"  L{o['line']:3d} [{o['field']:28s}] {o['text'][:100]}")

    json.dump({"rows": rows, "port_in": port_in, "orphans": orphans,
               "client_body_line_range": [lo, hi],
               "client_schema_line_excluded": 64},
              open(OUT, "w"), indent=1)
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
