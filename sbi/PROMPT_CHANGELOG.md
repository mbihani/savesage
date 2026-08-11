# SBI prompt changelog — every change tied to a measured defect

## Baseline used

| | |
|---|---|
| **File** | `/Users/mayanck.bihani/Savesage/SYSTEM PROMPT.txt` (the client's own production prompt) |
| **File size on disk** | 10,111 bytes, sha256 `c618380769746a4abe988cb12cb947d979bac81424deb371286183a3454ca362` (matches the brief) |
| **What was actually sent** | the **inner string only** — 10,041 chars / **10,088 bytes** UTF-8, sha256 `9dc59e63b6957bf2…` |
| **Why they differ** | the file is Python source: it opens `SYSTEM_PROMPT = """` and closes `"""`. Stripping the 20-char prefix + 3-char suffix + trailing newline accounts for all 23 bytes. `sbi_lib.strip_python_wrapper()` fails loud rather than silently shipping the wrapper. |
| **Schema** | `luna_prompt/LUNA_SCHEMA.json`, **UNCHANGED**, verified byte-identical to `gt298_lib.GT_SCHEMA` (33 leaves). Identical across all 3 banks and all arms. |

`luna_prompt/LUNA_PROMPT.txt` was **not** used as the baseline. It is
ground-truth-flavoured ("this output will be used to score other models") and shares
the GT's prompt instrument, so it is the wrong instrument for a production baseline.

> **Correction to an earlier pass in this session.** A first Phase-1 run did use
> `LUNA_PROMPT.txt` by mistake. It was discarded and Phase 1 was re-run from scratch
> against the client prompt; every number below comes from the re-run
> (`run_p1_client/`). The mis-prompted run is retained at `run_luna_generic/` and is
> **not** used for any reported metric.

## Phase-1 baseline result (10 statements, client prompt, Luna 5.6 native PDF)

10/10 `OK`, `finish_reason=stop` on all 10, zero truncation, zero 429s, zero schema
violations. **Truncation is not the SBI risk it was predicted to be** — worst
statement (141 rows) used 7,627 completion tokens against a 96,000 cap (7.9%).

Defect tally over the 10 statements, every one adjudicated against the PDF with
PyMuPDF page/coordinate evidence:

| count | defect | verdict |
|---:|---|---|
| 8 | `LUNA_DROPPED_PRINTED_ROW__BEFORE_periodStart` | LUNA_WRONG, caused by a baseline rule |
| 3 | `LUNA_DROPPED_PRINTED_ROW__LEADING_BAND` | LUNA_WRONG, caused by a baseline rule |
| 1 | `LUNA_DROPPED_PRINTED_ROW__unclassified` | LUNA_WRONG |
| 2 | `LUNA_PAYMENT_RECEIVED_AS_DEBIT` | LUNA_WRONG, **caused by the baseline prompt being wrong** |
| 2 | `LUNA_DIRECTION_VS_CD_MARKER` | same 2 rows as above |
| 1 | `LUNA_AMOUNT_SCALED__DIV1000` | LUNA_WRONG |
| 4 | `LUNA_CLOSINGPOINTS_IS_CYCLE_CLOSING` | **CORRECT** — logged to prove the SBI rule holds |
| 0 | summary label mis-binding (either side) | clean on this sample |

---

## (a) VALIDATING the baseline's existing SBI rule — it is CORRECT, keep it

Baseline clause: *"For SBI cards: if closingPoints and cashback are not mentioned,
closing points must not be taken from total cashback earned or total reward points
earned from card issue date; it should reflect only the current statement cycle."*

This was an untested client assertion. **Measured: it is right, and it is load-bearing.**
SBI genuinely prints two different reward tables, and they hold different numbers:

- **stmt 1707857175** — the SAVINGS AND BENEFITS row prints `1072 | 5077 | 6590` under
  `For this statement | For this year | From the card issue date`. Luna returned
  `closingPoints = 1072`, the per-statement figure. Taking the lifetime column would
  have returned 6590 — a **6.1x** error. The incumbent also returned 1072.
- **stmts 837436340 / 1560321887 / 365516172 / 546216901** print the 4-cell cycle strip
  (`Previous Balance | Earned | Redeemed/Expired/Forfeited | Closing Balance`), e.g.
  `1256 | 109 | 0 | 1365` on 837436340. Luna took `1365` (Closing Balance) on all four.
  Verified by regex against the raw PDF text in `phase2_measure._points_evidence`.

**Change made:** the rule is **kept verbatim**, and *reinforced* with the layout
mechanics that make it followable — the two tables are now named explicitly, the
four cycle-strip cells are mapped one-to-one onto the four rewards fields, and the
prompt states that the labels are printed *after* their values in PDF text order
(they are, which is why a reading-order parse mis-binds them).

**Residual finding, not fixed by prompt text — stmt 515948911.** The PDF prints
`-1467 | -1467 | 44136` in the savings row and a `CASHBACK` block, with no cycle strip.
Luna returned `closingPoints = null`; the incumbent returned `1467` (sign dropped).
The printed value is **negative**. Neither source is right: `LUNA_WRONG` (null when a
value is printed) and `CSV_WRONG` (sign inverted) — a genuine `BOTH_WRONG`. The prompt
already says "RewardPoints can be negative"; the value is a cashback figure, so under
"if both points and cashback are present, select points" the correct answer is arguably
null. Flagged as **AMBIGUOUS** rather than papered over.

## (b) STRIPPING other banks' rules

Removed every rule that cannot fire on an SBI statement. Occurrence counts,
client → refined: HDFC 6→0, ICICI 1→0, INDUSIND 1→0, AU Bank 1→0,
Standard Chartered 1→0, IDFC 1→0, RBL 1→0, Marriott 2→0, eDGE 1→0,
Membership Rewards 1→0. (The one remaining `EDGE` is the `EDGE_CASES:` section header.)

That removed ~2,050 chars of dead weight. **The net prompt is still LONGER, not
shorter: 10,041 → 14,887 chars (+48.3%).** The brief's expectation, carried over from
the Axis corpus, was that stripping boilerplate would make the prompt shorter *and*
better. On SBI the strip is worth doing but is outweighed by the layout guidance the
corpus demanded. The output-token headroom argument for a leaner prompt **does not
apply here**: this is *input* tokens, and the measured constraint is not near any cap
(worst case 7,627 of 96,000 output tokens). Prompt input cost rises ~4,800 chars
(~1,200 tokens) per call against a ~19,000-token PDF payload — about +6% input.

---

## Rules ADDED — each with the observed defect that forced it

### 1. `SBI STATEMENT LAYOUT` block (new, at the top)
**Defect:** the ACCOUNT SUMMARY grid on page 1 emits all labels first and then all
values in a *different* order (verified: `page.get_text()` returns
`Credit Limit / Cash Limit / Available Credit Limit / Available Cash Limit` and the
figures in an unrelated sequence). A reading-order parse binds figures to the wrong
labels. **Statement id:** all 10; the geometry is documented in
`sbi_pdf_evidence.summary_evidence`. Luna got all four right on all 10, so this is
*prophylaxis for the 290 unseen statements*, honestly labelled as such — no observed
Luna defect here.

### 2. Leading-band transaction rows are transactions — **8 dropped rows fixed**
**Defect:** SBI prints statement-level credits ABOVE the
`TRANSACTIONS FOR <NAME>` header, and dates them *later* than the rows that follow.
Luna dropped the entire band on 2 of 10.
- **stmt 1707857175:** dropped `01 May 26 PAYMENT RECEIVED 000BD016121BALAAAJGB4VZ
  1,28,100.00 C` (p1 y=460) and `03 May 26 FUEL SURCHARGE WAIVER EXCL TAX 5.36 C`
  (p1 y=471) — 64 rows emitted vs 71 printed.
- **stmt 837436340:** dropped `01 Jul 26 PAYMENT RECEIVED … 15,050.00 C` (p1 y=460)
  — 137 vs 141.

### 3. The date-window rule must never DELETE a row — **the single biggest recall defect**
**Defect (root cause of 8 of the 12 dropped rows).** The baseline says
*"Transaction Date must not exceed the Statement Date, nor fall more than two months
prior to it"* and *"transaction dates must fall within statementPeriodStart and
statementPeriodEnd (inclusive) … If a parsed date fails this check, swap day/month and
re-validate."* Luna applied it as a **filter**, silently deleting legitimate printed rows:
- **stmt 837436340** (period 23/06–22/07/2026): dropped 4 rows printed `22 Jun 26`,
  one day before periodStart — `UPI-MONIKA LALIT CHANG 10.00 D`,
  `UPI-L A ENTERPRISE 10.00 D`, `UPI-PRAHALAD DHOBI 48.00 D`, `UPI-DINESHBHAI 115.00 D`.
- **stmt 1707857175** (period 17/04–16/05/2026): dropped 4 rows printed `16 Apr 26` —
  `UPI-REDEFINED PRIVATE L 20.00 D`, `… 105.00 D`, `… 20.00 D`,
  `UPI-A M R TEMPTATIONS 265.00 D`.

**Corpus-wide exposure, measured:** of 304 statements with a printed period, **76
(25.0%)** have at least one incumbent transaction dated outside it — **124 rows total**.
All 124 fall **BEFORE periodStart**; **0** fall after periodEnd and **0** after the
statement date. So on SBI the harmful half of the rule is the *lower* bound, not the
"must not exceed the Statement Date" clause the brief flagged from Axis.

**Change made — and this is a RECOMMENDATION, not a unilateral rewrite.** The user
previously chose to KEEP this rule as-is on Axis. I did **not** delete it. I narrowed
it to what it was presumably for (fixing an ambiguous day/month reading) and forbade
the destructive interpretation: *"THIS CHECK MAY ONLY CORRECT A DATE — IT MUST NEVER
DELETE A TRANSACTION."* The `must not exceed the Statement Date / two months prior`
sentence is dropped, because on this corpus it cannot help (0 rows violate the upper
bound) and it is the sentence that licenses deletion. If you want the original
wording restored, that is a one-line revert; the measured cost is ~124 rows across
the corpus.

### 4. The printed C/D column overrides everything — **fixes a baseline prompt ERROR**
**Defect:** the baseline says `DEBIT: … payment` and *"Payments TO the bank → DEBIT"*.
For a credit-card statement that is **backwards**: a payment received reduces the
balance and SBI prints it with `C`. Luna followed the prompt and got it wrong:
- **stmt 1120623464:** `PAYMENT RECEIVED 000DP016063184952z1QEsy` 34.00 →
  Luna `DEBIT`, PDF prints `C`, incumbent `CREDIT`. This is the statement's **only**
  transaction, so its transaction-level direction accuracy was 0%.
- **stmt 734857498:** `PAYMENT RECEIVED 000DP216187LEE0ICGZWZW9` 1074.00 →
  Luna `DEBIT`, PDF prints `C`, incumbent `CREDIT`.

**Corpus-wide:** the incumbent labels **485/485** `PAYMENT RECEIVED` rows `CREDIT`.
Luna-on-baseline got 8/10 right in the sample — i.e. the prompt made it wrong 20% of
the time on the highest-frequency credit row in the corpus.
**Change made:** `C → CREDIT`, `D → DEBIT`, `T → CREDIT` declared authoritative and
above description-reading; `PAYMENT RECEIVED` called out explicitly; the misleading
`payment → DEBIT` marker list removed; `C`/`D` added to the "never emit raw markers" list.

### 5. Comma is a thousands separator — **1 magnitude error**
**Defect — stmt 837436340:** printed `15,050.00`, Luna emitted **15.05**, a 1000x
error on a payment row (also the row it dropped from the leading band in the same
statement; both defects are logged separately). Change: Indian digit grouping spelled
out with `15,050.00` and `1,28,100.00` as worked examples.

### 6. Two-digit-year transaction dates
**Defect:** SBI prints transaction dates as `22 Jun 26`. Luna already normalised to
DD/MM/YYYY on 522/522 rows, so no Luna defect — but the same 2-digit form broke my
*scorer* (see below), which is direct evidence the format is a live hazard. Added as
prophylaxis with the explicit `22 Jun 26 → 22/06/2026` mapping and "never read the day
as the year".

### 7. `network` — the schema asks, the baseline never mentioned it
**Defect:** `network` has **0 occurrences** in the baseline prompt (as the brief noted)
yet is required by the schema. Baseline behaviour: Luna emitted `null` on 9/10 and
**`"VISA"` on stmt 1220226393**. Adjudicated against the PDF: `VISA` appears only in
boilerplate — the dispute paragraph *"All transaction disputes are resolved as per the
Network (VISA, MasterCard, Rupay, Amex) Guidelines"* (p7), a fee table (p6), and the
*"VISA Credit Card Pay"* payment-option blurb (p7). **Nothing** identifies this card's
network → `LUNA_WRONG`, `hallucinated_when_GT_null`. The incumbent hallucinates the
same way, on more statements: 37/315 rows carry a network value (`VISA` 23, `Visa` 8,
`RuPay` 2, `RUPAY` 2, `Rupay` 1, `Mastercard` 1) — those need per-statement
adjudication, reported in `SBI_REPORT.md`.
**Change made:** allowed values enumerated, and the three boilerplate locations named
as explicit non-evidence, with "a null is correct; a guessed network is a fabrication".

### 8. `issuerName` — 0 occurrences in the baseline
Baseline behaviour: 9/10 `"SBI Card"`, but **stmt 924475136** returned
`"SBI Cards and Payment Services Limited"` (the legal entity) where the incumbent said
`"SBI Card"`. Change: `issuerName = "SBI Card"`, with co-brand partners and the legal
entity name both named as wrong answers. **Flagged as non-discriminating** — the
incumbent is `SBI Card`/`SBI CARD`/`SBI card` on 314/315 rows, so a high score here is
casing normalisation, not extraction skill.

### 9. `totalMinimumAmountDue` and the four confusable summary labels
`totalMinimumAmountDue` has **0 occurrences** in the baseline. A new
`STATEMENT_LEVEL_SUMMARY` block maps all four labels and names the traps
(`Total Outstanding` ≠ `Total Amount Due`; `Cash Limit` ≠ `Credit Limit`;
`Available Cash Limit` ≠ `Available Credit Limit`; `Credit Limit` is a substring of
`Available Credit Limit`). No observed Luna defect on the 10 — prophylaxis, labelled.

### 10. `lastFourDigit` two-digit mask
SBI prints `XXXX XXXX XXXX XX25` — only the last **two** digits are real. Baseline
behaviour was already correct (`XX25`, 10/10 agreeing with the incumbent, whose values
are `XX99`-shaped on 312/315 and `xx99` on 3). Made explicit to protect against
backfilling to `0025`, which the baseline's own `0576` example invites.

### 11. `rawStatementId`
**Defect:** SBI **does** print `STMT No. : H26072450588` on page 1 — verified present
on **10/10** sampled statements. Luna extracted it on 10/10 unprompted. The GT prompt
(a different instrument) asserts this is "almost certainly NOT present"; on SBI that is
false. Added the `STMT No.` label so the behaviour is specified rather than incidental.

### 12. `COMPLETENESS IS MANDATORY`
Given SBI's density (mean 12.6, max 141 rows) and the 12 dropped rows above, an
explicit completeness instruction now names the grey-shaded and `#`-marked rows as
still-transactions, and the non-transaction blocks to exclude. Grey/`#` rows are real:
p1 of 837436340 prints *"Transactions highlighted in grey colour, if any, do not form
part of Purchases & Other Debits; #Transactions fully/partially converted to
Flexipay/Encash/Merchant EMI"* — a statement about which *subtotal* they belong to,
not about whether they are transactions.

### 13. `utilisationPercent`
**Measured: 0 of 300 SBI PDFs contain the string `utilis`/`utiliz`** (full text of all
300 scanned). The baseline's inference rule is retained unchanged, with "(SBI
statements do not print one.)" added so the field is understood as arithmetic, not
extraction. Reported both as-extracted and as-derived.

---

## Rule NOT added — a 14th change, found mid-run and deliberately deferred

**`TRANSFER TO …` rows carry no C/D marker.** Found while auditing the Phase-3 results:
`TRANSFER TO FLEXIPAY INSTALLMENT` (stmt 273593709) and `TRANSFER TO MERCHANT EMI`
(stmt 162725042) are printed with **no** direction marker. Verified across the corpus:
**14 statements have such a row and 0 of the 14 carry a C/D marker.** Both references
call them `CREDIT` (they move an amount out of the revolving purchase balance into an
instalment plan); the refined prompt's marker-less fallback list covers purchases, fees,
taxes, interest, markup and EMI instalments → DEBIT, so Luna returns `DEBIT` and scores
2 wrong on this pattern.

I wrote the fix, then **reverted it**, because the Phase-3 run was already in flight:
`run_arm.py` re-reads `SBI_PROMPT.txt` on every call, so editing it mid-run would have
produced two different prompts inside one arm and silently invalidated the arm's
provenance. Verified after reverting that all records carry a single
`prompt_sha256=da15a77509cca13e` matching the file on disk.

**Recommended next revision** (one clause, not applied here):

> "TRANSFER TO FLEXIPAY INSTALLMENT", "TRANSFER TO MERCHANT EMI", "TRANSFER TO ENCASH"
> and similar "TRANSFER TO …" rows are printed with NO C/D marker. They move an amount
> OUT of the revolving purchase balance into an instalment plan, so they are "CREDIT".

Expected effect: fixes 2 known direction errors; upper bound ~14 rows corpus-wide
(≈0.4% of 3,769). It is UNVERIFIED against the model — no run used it.

## 2026-08-11 — SBI-only prompt repair after the 300-statement review

No inference or re-sweep was run for these edits. Every impact below is a
**PREDICTION / UNVERIFIED** pending an authorised re-sweep.

### Edit 1 — preserve implausible-looking narration literally

Strengthened literal transcription so mangled, misspelled, mid-word-truncated, and
broken-URL-looking merchant text is preserved as the statement printed it, without
completion, correction, expansion, or reconstruction. This targets **17 description
cells (PREDICTION / UNVERIFIED)**, including 14 repeated broken-URL narrations on
statement 1712093656 and the observed `ONLIOLUTION`, `RANGAREDD`, and `INTE` cases.

### Edit 2 — keep the date column out of the description

Made date/narration separation independent of the `TRANSACTIONS FOR <NAME>` header:
the date column must never be prepended to the description, whether or not that header
is printed. This targets **5 description cells on statement 636217952 (PREDICTION /
UNVERIFIED)**; the layout is exposed on 65 statements without that header.

### Edit 3 — preserve printed terminal tokens

Required every visibly printed terminal description token, including country codes and
qualifiers, to be retained, while forbidding supply of tokens not printed. This targets
**3 description cells (PREDICTION / UNVERIFIED)**. The 13 printed trailing `IN` tokens
on statement 1349187066 are already emitted correctly and are reference defects, so the
rule deliberately preserves rather than removes them.

### Edit 4 — direction authority (C/D marker is authoritative)

Reinforced that SBI's printed C/D marker overrides narration wording whenever present:
*"Never infer direction from narration when a C/D marker is present."* SBI's C/D column
is safe and no other bank's glyph caveat applies. **Expected gain: 0 cells** — this is a
robustness/wording improvement, not a defect fix, because all 7 disputed direction cells
sit on rows that print no marker at all (see below).

A first version of this edit also added a blanket *"marker-less `TRANSFER TO ...` rows ->
`CREDIT`"* fallback. **That rule was wrong and has been removed.** It contradicted the
very principle this edit introduced — it inferred direction from narration — and the
evidence does not support it. See the next section.

### Edit 4b — the 7 marker-less `TRANSFER TO` direction cells are NOT prompt-fixable

**Finding: no reliable printed discriminator exists. No rule was written.** The blanket
`CREDIT` mapping was removed and deliberately not replaced with any other
narration-to-direction mapping. The generic marker-less fallback already in the prompt is
the only route for these rows, and the prompt now explicitly forbids pinning a direction
to a printed merchant phrase.

**Evidence 1 — the reference itself splits these rows almost exactly evenly.** Across all
300 statements there are exactly **14** `TRANSFER TO` rows, and the reference direction is
**DEBIT 7 / CREDIT 7**. The *same* description string carries *opposite* directions on
different statements:

| statement | description | reference direction | amount |
|---|---|---|---|
| 1040768215 | TRANSFER TO MERCHANT EMI | DEBIT | 22,251.04 |
| 162725042 | TRANSFER TO MERCHANT EMI | CREDIT | 5,114.00 |
| 1784860961 | TRANSFER TO MERCHANT EMI | CREDIT | 12,705.00 |
| 186548429 | TRANSFER TO MERCHANT EMI | CREDIT | 31,595.35 |
| 1939828045 | TRANSFER TO MERCHANT EMI | DEBIT | 6,176.27 |
| 423235138 | TRANSFER TO MERCHANT EMI | DEBIT | 27,528.36 |
| 525973295 | TRANSFER TO MERCHANT EMI | DEBIT | 34,994.20 |
| 664657130 | TRANSFER TO MERCHANT EMI | CREDIT | 32,778.70 |
| 749834844 | TRANSFER TO MERCHANT EMI | DEBIT | 13,910.00 |
| 807587861 | TRANSFER TO MERCHANT EMI | CREDIT | 88,770.63 |
| 834382309 | TRANSFER TO MERCHANT EMI | DEBIT | 2,29,470.49 |
| 273593709 | TRANSFER TO FLEXIPAY INSTALLMENT | CREDIT | 46,000.00 |
| 648670268 | TRANSFER TO FLEXIPAY INSTALLMENT | DEBIT | 51,601.00 |
| gmail_384287 | TRANSFER TO MERCHANT EMI | CREDIT | 74,857.60 |

Any blanket direction for this phrase is therefore wrong on ~7 of the 14 rows by
construction. A blanket `CREDIT` rule does not fix a 7-cell defect; it converts it into a
differently-distributed 7-cell defect while hardcoding a merchant-string-to-direction
mapping the data contradicts.

**Evidence 2 — PyMuPDF probe of all 14 rows in the source PDFs (read-only).** Nine
candidate discriminators were tested on raw, non-lowercased strings, with rows grouped by
a y-BAND rather than an exact baseline so a marker one or two points off still binds to
its row. Every one was eliminated:

| candidate discriminator | result |
|---|---|
| printed `C`/`D`/`T` marker on the row | **absent on all 14.** Corpus sweep of all 300 PDFs: 14 `TRANSFER TO` rows, **0** carrying any `C`/`D`/`T`/`M` marker |
| separate credit amount column | no — the amount's right edge matches the neighbouring `D` rows in the same layout (`x1=405.0`; `414.4` on the one page-2 case). Left edge varies only with digit count (right-aligned) |
| `+`/`-`/`CR`/`DR`/parenthesis sign token | none present on any of the 14 |
| text colour / font | identical on all 14 (colour `8355967`, font `SariOfLt`) |
| grey shading (the footer's "highlighted in grey" band) | no filled vector rect behind any of the 14, at any width; image counts are layout-driven, not direction-driven |
| section / table the row sits under | all 14 sit inside a `TRANSACTIONS FOR <NAME>` section (or the page-2 "Date / Transaction Details / Amount" continuation header). Same section type for both directions |
| wrapped-cell inheritance from the row above | **killed.** Each of the 14 prints its OWN date and sits at exactly the table's median row pitch (11.8 pt). The row above is a `D` row for both a CREDIT case (162725042) and a DEBIT case (1040768215), and two DEBIT cases (525973295, 648670268) sit directly under the section header with no row above to inherit from |
| `Previous Balance / Payments / Purchases` arithmetic strip | identical boilerplate on all 14; carries nothing direction-bearing |
| pairing with the `#`-marked source row of the same amount | cross-cutting, explains nothing: 4 of 7 CREDIT have a same-amount `#` row, and so do 2 of 7 DEBIT |

**Evidence 3 — the removed rule named a string that does not exist.** `TRANSFER TO ENCASH`
appears **0 times** in the 300-PDF corpus, so that clause was unjustified by the data
independently of the direction question.

**Conclusion.** On these rows the direction is not determinable from the row text or from
any printed structural feature of the document. The reference's own 7/7 split, on an
identical printed phrase with no printed marker to distinguish the cases, means the split
is either internally inconsistent or driven by information not present in the statement.
**These ~7 cells are not reliably prompt-fixable, and no prompt rule should claim them.**
Resolving them requires a client decision on how a marker-less balance transfer between
the revolving balance and an instalment plan should be signed — not a prompt edit.

### Deliberately not changed — non-prompt-fixable or harmful targets

- **52 date `both_null` rows:** Luna and the reference agree; the joint metric charge is
  a scorer defect.
- **71 undated tax/markup continuation rows:** inheriting the parent date is useful and
  conflicts with GT rule 14; no rule was added to null continuation-row dates. Only the
  3 inconsistent nulls merit a future consistency review.
- **14 description cells:** the reference drops text genuinely printed, including the
  trailing-country-code case; no prompt can improve those references.
- **2,255 amount `format_only` cells:** integer/float serialization has identical digits
  and numeric comparison already treats them as equal, so no serialization format was
  pinned and the expected gain is exactly zero cells.
- **14 `rewards.closingPoints` null disagreements:** the client cashback instruction and
  shared GT rule 13 contradict each other; this requires a client decision.
- **7 marker-less `TRANSFER TO` direction cells:** no printed discriminator separates the
  DEBIT cases from the CREDIT ones — nine candidates tested and eliminated against the
  PDFs, and the reference splits an identical phrase DEBIT 7 / CREDIT 7. No rule was
  written and the blanket `CREDIT` mapping was removed. See Edit 4b.
- The working leading-credit-band and two-reward-table rules were preserved; measured
  row alignment is 3,527/3,527 with zero lifetime leaks.
- No HDFC or ICICI rules were added, and the shared `GT_SCHEMA` was not changed.

## Anti-overfit note

These 13 changes were tuned on 10 statements and tested on ~300 — a 30x
extrapolation. Changes 2, 3, 4, 5 are backed by located defects; 1, 6, 9, 10 are
prophylactic and explicitly labelled as having **no** observed Luna defect. Phase 3
therefore reports every metric twice: over all statements, and over the ~290 held-out
statements excluding these 10. The held-out numbers are the ones to trust.

## Scorer defect found and fixed (not a prompt change, but it would have inverted a result)

The project's canonical `score.date_norm` does not parse SBI's dominant transaction-date
format, the 2-digit-year `DD Mon YY` — it returns the string unchanged, so
`'22 Jun 26' != '22/06/2026'`. **2,733 of 3,769 (72.5%)** incumbent transaction dates
are in that form, while Luna and Opus emit `DD/MM/YYYY` on 100% of rows. Unpatched, the
scorer would have charged the *incumbent* a fabricated 72.5% date-defect rate and
handed Luna an unearned win, while also corrupting the matcher's date tie-break.
`score_lib_sbi.date_norm` now wraps the canonical one (rather than editing it, so the
other two banks keep the identical yardstick).
