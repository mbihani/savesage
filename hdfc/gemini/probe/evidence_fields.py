"""Evidence for two prompt-port judgement calls that must not be guessed.

(1) lastFourDigit -- does HDFC print REAL trailing digits or a MASK?
    The generic prompt says preserve masking exactly ("XXXXXXX56" -> "xx56"). If HDFC
    prints real digits, that rule would DESTROY correct output. Prints every card-number
    -shaped string found in each PDF's text layer so the actual printed form is visible.
    NOTE the filenames also contain a masked form (5268XXXXXXXXXX21) -- that is the
    FILENAME, not the statement body, and is irrelevant since the model never sees it.

(2) Which rewards-balance LABELS actually occur, to decide whether the generic
    prompt's extra labels ("Marriott Bonvoy Points", "eDGE REWARD POINTS") are
    HDFC-applicable or belong to another issuer.
"""
import re
import sys

sys.path.insert(0, "..")
import pdf_rows as P  # noqa: E402

# card-number shapes: real digits, X-masked, dash/space grouped
CARD = re.compile(r"\b(?:\d[\dXx*\- ]{10,22}\d)\b")
LABELS = [
    "Reward Points", "RewardPoints", "Reward Point Balance", "Reward Points Balance",
    "Points Earned Till Date", "Closing Balance", "Closing Points", "Opening Balance",
    "NeuCoins", "Marriott Bonvoy", "eDGE", "EDGE REWARD", "Bonus", "Cash Back",
    "CashBack", "Cashback", "Disbursed", "Adjusted", "Lapsed", "Expiring",
]

print("=" * 78)
print("(1) CARD-NUMBER STRINGS PRINTED IN THE STATEMENT BODY")
print("=" * 78)
for sid, fn, path in P.corpus():
    txt = P.full_text(path)
    hits = []
    for m in CARD.finditer(txt):
        s = re.sub(r"\s+", " ", m.group(0)).strip()
        if len(re.findall(r"\d", s)) >= 6 and s not in hits:
            hits.append(s)
    print(f"\n{P.statement_id(fn) or '-':<12} {fn[:46]}")
    for h in hits[:6]:
        masked = bool(re.search(r"[Xx*]", h))
        tail = h[-4:]
        print(f"    {h!r:<34} masked_anywhere={masked!s:<5} last4={tail!r} "
              f"last4_all_digits={tail.isdigit()}")

print()
print("=" * 78)
print("(2) REWARDS LABELS PRESENT PER FILE")
print("=" * 78)
for sid, fn, path in P.corpus():
    flat = re.sub(r"\s+", " ", P.full_text(path))
    present = [lab for lab in LABELS if re.search(re.escape(lab), flat, re.I)]
    print(f"{P.statement_id(fn) or '-':<12} {', '.join(present) if present else '(none)'}")
