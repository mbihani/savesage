# Hand-verified adjudications (PDF-checked individually)

Each entry below was confirmed by reading the PDF text directly, not by the
mechanical adjudicator alone. These are the anchors the automated verdicts are
validated against (`test_adjudicator.py`).

## Incumbent DROPPED a real transaction row (most material single defect found)

`decrypt_2050509744_193d3164f9e1b9b7_4854XXXXXXXXXX47_16-12-2024` — Luna 24 rows,
incumbent 23. The extra Luna row is genuine:

```
12/09/2024
mahaecoCRcard            Nagpur
- 32
1,200.00 Cr
```

Printed once, in the Domestic Transactions table, with an explicit `Cr` suffix.
Luna: `date=12/09/2024, description='mahaecoCRcard Nagpur', amount=1200,
direction=CREDIT` — correct on all four. **The incumbent omits a ₹1,200 CREDIT
entirely.** A dropped credit is materially worse than a field typo: it
understates what the bank owes the customer and cannot be caught downstream by
any field-level validation.

## Luna `network` hallucination that survived the hardened rule

`decrypt_1756410838_199f656352543609_6529XXXXXXXXXX34_17-10-2025_944` — Luna
returned `MASTERCARD`; the incumbent returned null. Regex over the full page text
for `visa|master|rupay|diners|amex` returns **no match anywhere in the PDF**, so
this is pure BIN inference. Incumbent correct.
This is the residual risk called out in the verdict: even with an explicit
quote-it-or-null instruction plus a named ban on leading-digit inference, Luna
still fabricates this field occasionally.

## The rupee glyph (the corpus-wide finding)

`decrypt_738368244_19f70d2e2c77ebd5_6530XXXXXXXXXX38_16-07-2026_366`, page 1:
`font=ITFRupee,Bold size=15.0 raw='C' cp=['0x43']`. The headline figures carry the
same prefix (`TOTAL AMOUNT DUE → C13,507.00`), which is what refutes the client
prompt's `"C" ⇒ CREDIT` rule from inside the document. 179/281 PDFs affected.
Real credit markers: leading `+` (`+ C 2,600.00`) or trailing `Cr`.

## Column bleed vs legitimate wrap (both directions verified)

- FX amount column is NOT narration: `CURSOR, AI POWERED IDECURSOR.COM` /
  `USD 20.00` / ` C 1,849.76` are three separate printed lines. The incumbent
  appends `USD 20.00` to the description — incumbent wrong.
- Badge line is NOT narration: `EMI` prints on its own line above
  `PRESTIGEGHAZIABAD`. Luna prepends it — Luna wrong.
- A narration DOES legitimately wrap mid-reference:
  `IGST-VPS2718699250565-RATE 18.0 -06 (Ref#` / `09999999980704001141587)`.
  The incumbent truncates at the break — incumbent wrong.

## Scorer/adjudicator bugs these checks exposed (all in MY code)

1. `direction` judged repeated narrations against the first PDF occurrence only,
   producing a self-contradictory LUNA_WRONG + CSV_WRONG on the same row.
2. Substring test scored the incumbent's truncated values as "printed", hiding 30
   of 33 description defects as AMBIGUOUS.
3. `amount` comma-flattening made `94022.00` a substring of `194022.00`, so the
   lakh-digit defect read AMBIGUOUS. Fixed with a digit-boundary match.
4. `norm_date` returned None for HDFC's `'18/04/2026 | 00:00'` (pipe separator),
   scoring 23 pure FORMAT differences on one statement as incumbent date errors.
   Fixed; those 23 went to 0.

## Foreign-currency rows: Luna reports the FX amount, not the billed rupee amount

Found at n=130 while auditing `transactions.currency` (98.2%, 28 wrong — every one
an FX row). Verified against the PDF:

```
17/03/2026 | 11:30
CURSOR, AI POWERED IDECURSOR.COM
USD 20.00          <- foreign amount column
 C 1,849.76        <- amount actually billed, in rupees
```

| source | amount | currency |
|---|---:|---|
| Luna (refined) | 20 | USD |
| Opus-5 GT | 1849.76 | INR |

Luna is **internally consistent** — it reports USD 20.00 and labels it USD, so the
pair is not self-contradictory. But the billed figure is ₹1,849.76, and both the
GT prompt and the client's own baseline say to report the rupee amount with INR
when a foreign spend is converted ("If the statement shows a foreign spend
converted to rupees, report the rupee amount with INR"). So Luna is wrong on the
value the client wants, and the defect is a **gap in MY refined prompt**: it
inherited the baseline's currency rules but never states which of the two printed
amounts wins on an HDFC FX row.

Scope: **28 rows across 9 of 130 scored statements**; corpus-wide, **15/281 PDFs
(5.3%)** print a standalone FX amount line.

**Deliberately NOT fixed mid-run.** Editing `HDFC_PROMPT.txt` now would mean the
Phase 3 sweep was executed under two different instruments, making the run
uninterpretable. The fix is specified below and left for a follow-up sweep; the
current numbers stand as measured, with this defect disclosed rather than
patched over.

Proposed rule (untested — UNVERIFIED until a sweep runs with it):

> On a foreign-currency transaction HDFC prints TWO amounts: the foreign amount on
> its own line (`USD 20.00`) and the rupee amount actually billed (`C 1,849.76`).
> Always report the RUPEE amount in `amount` with `currency` = "INR". Never report
> the foreign amount. The foreign amount line is not part of the description either.

## Luna misses the `+` credit marker when a reward-points column intervenes

The Phase 2 fix eliminated the *false*-CREDIT epidemic (108 → 0). The residual
`direction` errors run the OTHER way: Luna says DEBIT where the row is a genuine
CREDIT. Verified against the PDF — the `+` marker is printed:

```
19/01/2026| 00:00
FRIDOPUNE
- 60               <- reward-points column (points reversed)
+  C 1,850.00      <- '+' = CREDIT, per my own prompt's rule
```

Luna returned `DEBIT`; Opus-5 GT returned `CREDIT`. **Opus is right, Luna is wrong.**
Both agree on the amount (1850.0), so this is purely a marker-reading failure.

Measured over the direction mismatches at n=133:

| | count |
|---|---:|
| Luna-vs-GT direction mismatches examined | 34 |
| row DOES print `+` near it — Luna missed the marker | **16** |
| ...of which a reward-points column sits between the description and the `+` | **7** |
| no `+` in the window (other causes / genuinely ambiguous) | 18 |

So roughly half the residual direction errors are one recognisable pattern: the `+`
is separated from the narration by an intervening column value, and Luna stops
looking. `UPI-Flipkart` is a related case where the row carries an `EMI` badge line
instead.

**Deliberately NOT fixed mid-run** (same reasoning as the FX defect — changing the
instrument mid-sweep would make the run uninterpretable).

Proposed rule (untested — UNVERIFIED):

> The `+` credit marker is NOT always adjacent to the narration. HDFC may print a
> reward-points value (e.g. `- 60`, `+ 76`) and/or an `EMI` badge on their own lines
> between the description and the amount. Scan forward past ANY such intervening
> column value to the rupee amount, and treat a `+` immediately before that rupee
> amount as CREDIT. Do not confuse a signed reward-points figure with the amount's
> own `+` marker.

Note the trap in that rule: reward-points columns themselves carry `+`/`-` signs
(`- 60`, `+ 76`), so the instruction must anchor on the `+` that precedes the RUPEE
amount, not on any `+` in the row.

## Luna leaks the `+` credit marker into the description (7 rows)

```
14/04/2026| 00:00
1% Swiggy Cashback
+  C 22.07
```

Luna returned `description="1% Swiggy Cashback +"`, `direction=CREDIT`,
`amount=22.07`. It read the marker correctly for `direction` but also appended it to
the narration. The `+` is a column marker, not text. 7 rows affected.

### Breakdown of Luna's description defects (n=31 at 147/281 scored)

Corrected classification — my first pass overstated this as "all column bleed":

| shape | count | what it is |
|---|---:|---|
| `EMI` badge prepended | 8 | column bleed |
| `+` marker appended | 7 | column bleed |
| FX amount appended | 3 | column bleed |
| `(Ref# ...)` kept where CSV dropped it | 2 | **Luna correct**, incumbent truncated |
| other added text | 1 | column bleed |
| Luna dropped text | 1 | truncation by Luna |
| genuinely DIFFERENT string | **9** | not column bleed — see below |

So ~19 of 31 are column-boundary bleed, but **9 are a different failure**: small
character-level divergences from the printed narration, e.g.

- `AVENUE SUPERMARTS LIMITEDMEDCHAL` where the PDF prints `...LIMITMEDCHAL`
  (Luna "completed" a truncated merchant name)
- `CREDIT CARD PAYMENT Net Banking` where the PDF prints `PAYMENTNet Banking`
  (Luna inserted a missing space)
- `ANTHROPIC* CLAUDE SUBSAN FRANCISCO` for printed `...FRANCISC`

These matter more than the bleed cases because they show Luna sometimes **normalises
the statement's own mangled text into what it "should" say** — the exact behaviour the
prompt forbids, and harder to fix than a column boundary. It never invents a merchant
that is not there; it tidies one that is.

Proposed rule (untested — UNVERIFIED; folds together with the FX and badge rules):

> The description is the narration column ONLY. Never append or prepend anything from
> a neighbouring column: not the `EMI` badge above it, not the `+`/`Cr` direction
> marker, not the reward-points value, not the foreign-currency amount, not the rupee
> amount.

## MOST SERIOUS LUNA DEFECT: a duplicated (fabricated) transaction row

`decrypt_252502266_19bc220c2d3c07ef_4341XXXXXXXXXX35_14-01-2026_31` — Luna emitted
**52 rows where the PDF prints 51**. The extra row is a duplicate, not an invention
of new content, but its effect is a transaction that does not exist.

Counted mechanically from the PDF (narration line immediately followed by its rupee
amount line), for the heavily-repeated narration `PM *ViFun Live Co LiHongKong`:

| source | rows | amounts |
|---|---:|---|
| **PDF (printed)** | **18** | `329.00` ×14, `792.00` ×**4** |
| Luna (refined) | 19 | `329` ×14, `792` ×**5** |
| Opus-5 GT | 18 | `329.0` ×14, `792.0` ×4 |

**Luna emitted one extra `792.00` row. Opus matches the PDF exactly.**

Why this matters more than any field-value error found in this evaluation:

- It **inflates spend** by ₹792 on this statement. A duplicated debit is not
  recoverable by any downstream field validation — the row is internally consistent
  and individually plausible.
- It appears on a statement with an extreme repeated-narration structure (18 identical
  descriptions, only two distinct amounts). That is exactly the structure that defeats
  row-level de-duplication, both in the model and in any reconciliation logic.
- It is on a **tuning** statement — i.e. one of the 10 the prompt was fitted to — and
  it still occurs. Earlier iterations of this same statement returned 51 and 47 rows,
  so the row count for this layout is **unstable run to run**.

Note this cuts against the otherwise-clean transaction-recovery story: Luna's
transaction F1 vs Opus GT is 100.00% only because precision/recall are computed over
2,420 pairs where this is the single false positive. The headline "zero row
divergence" claim from earlier partial runs is **superseded** — at n=155 Luna has
1 spurious row, and the incumbent has 1 spurious + 2 missing.

Recommended production control: reconcile the extracted transaction total against the
statement's printed `PURCHASES/DEBIT` and `PAYMENTS/CREDITS RECEIVED` arithmetic strip.
Both a duplicated debit and a dropped credit break that identity, so one cheap check
catches the worst defect on each side.

## The reconciliation control, BUILT AND MEASURED (not just recommended)

`reconcile.py` sums each source's extracted rows by direction and compares against the
statement's own printed arithmetic strip:

```
PREVIOUS STATEMENT DUES | PAYMENTS/CREDITS RECEIVED | PURCHASES/DEBIT | FINANCE CHARGES
C16,403.27                C17,023.00                  C17,880.84       C0.00
```

The identity is `sum(DEBIT) == PURCHASES/DEBIT + FINANCE CHARGES` and
`sum(CREDIT) == PAYMENTS/CREDITS RECEIVED`. Tolerance ±₹1.

| source | statements with a strip | reconciles | rate |
|---|---:|---:|---:|
| **Opus-5 GT** | 128 | 127 | **99.2%** |
| Incumbent CSV | 179 | 174 | 97.2% |
| Luna (refined) | 124 | 112 | 90.3% |

It **does** flag Luna's duplicated row exactly: debit 18,672.84 vs printed 17,880.84,
delta **+792.00**. Opus's single miss is ₹5.99.

Two things I got wrong before measuring, both corrected in the code:

1. **The sign matters.** A credit balance prints `C-0.18`; capturing only `[\d,]+`
   skipped that cell and shifted every later cell by one, making a *correct* Opus
   extraction look 2,358 out. Fixed → Opus went 92% → 99.2%.
2. **FINANCE CHARGES is a separate cell** but appears in the table as an ordinary
   interest DEBIT row, so it belongs on the debit side of the identity. Omitting it made
   Opus appear to overshoot by exactly one statement's finance charge (10,031.84).

### What the control actually diagnoses

Luna's 11 overshoots/11 undershoots are mostly **mirror-image** pairs
(`debit_delta=+7212.2, credit_delta=-7212.2`) — i.e. the row is present with the right
amount but the wrong `direction`. That is the `+`-marker weakness documented above,
not row loss. Only one Luna flag (`+792.00`, credit delta 0) is a true row defect.

The incumbent's two largest flags turned out **not** to be dropped rows: on
`decrypt_535035…` and `decrypt_637806286…` it emits all 18/22 rows but with
`direction=null` on every one, so nothing sums. Measured corpus-wide: the incumbent
returns `direction=null` on **40 of 4,638 rows, confined to exactly those 2
statements** — a total-failure mode on 2 statements rather than a diffuse one.

### Limits (why this is a control, not a scorer)

- **59 of 281 statements print no such strip**, so the check simply does not apply
  there — including `decrypt_2050509744…`, the statement where the incumbent dropped a
  real ₹1,200 credit. So this control would NOT have caught that defect.
- A direction misclassification and a genuine row error both break the identity; the
  mirror-image signature distinguishes them but only heuristically.
- It cannot detect an error that preserves both sums (e.g. a description defect, or two
  compensating direction flips).
