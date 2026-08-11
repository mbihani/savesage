# ICICI roll-up: measurement defect and repair (2026-08-11)

Blocking defect in `icici/final_scores.json` as published at `c3100c2`. Nothing was
re-extracted and no model was called; the repair is entirely a re-reading of
`scores_phase3.json`, which already contained the honest numbers.

## 1. The defect

`final_scores.json` reported

```
transaction_metrics: micro_precision = micro_recall = micro_f1 = macro_f1 = 1.0000
                     rows_matched = 4097   row_count_exact_match_statements = 304
headline_verdicts:   luna_transaction_micro_f1 = 1.0
```

**Mechanism.** `score_phase3.run_pair` computes `micro_precision = rows_matched / rows_pred`
and `micro_recall = rows_matched / rows_ref`, where `rows_matched` is the output of
`score_lib.match_txns_by_description` — a matcher that admits a pair on **description
similarity alone** (`>= 0.60`, strict 1:1, order-insensitive) and *deliberately excludes*
date, amount, direction and currency from admission, precisely so those four can be scored
non-tautologically afterwards. Those ratios therefore answer one question — "did this row find
a description twin?" — and answer nothing about whether the paired values agree. ICICI
narrations transcribe near-perfectly (mean description similarity **0.9992**; 4,097/4,097 rows
admitted; 304/304 statements with equal row counts), so precision, recall, micro-F1 and
macro-F1 all saturate at exactly 1.0 **by construction**. `build_final_scores` then
republished that block verbatim as `transaction_metrics` and hoisted `micro_f1` into
`headline_verdicts` as `luna_transaction_micro_f1`, converting an alignment statistic into a
correctness verdict.

`test_matcher_noncircular.py` passed throughout, and correctly: it proves the **matcher** is
non-circular. It says nothing about how the **roll-up** labels the matcher's output. The
circularity re-entered one level up. The per-field transaction verdicts that do measure
correctness were being computed all along and were never 1.0 — they were simply not the number
anyone read.

## 2. Corrected transaction numbers (Luna refined vs Opus-5 GT, all 304)

Row alignment and value correctness are now separate blocks, and the alignment ratios are
renamed `pairing_*` with `is_correctness_claim: false`.

### Row alignment — NOT a correctness claim

| | Luna | incumbent CSV |
|---|---:|---:|
| rows paired / reference rows | 4,097 / 4,097 | 3,932 / 4,097 |
| `pairing_f1` | 1.0000 *(by construction)* | 0.9794 |
| statements with equal row count | 304 / 304 | 295 / 304 |

### Value correctness over the matched pairs — existing `score_lib` normalisation

Luna, per field. `format_only` = agrees after normalisation, serialised differently; it is
**not** counted as `wrong_value`.

| field | accuracy | wrong_value | format_only | byte-identical | note |
|---|---:|---:|---:|---:|---|
| date | 1.0000 | 0 | 0 | 4,097 | earned byte-for-byte, 487 distinct reference values |
| amount | 0.9995 | 2 | **2,345** | 1,750 | 57% of pairs are `180` vs `180.0` |
| direction | 0.9988 | 5 | 0 | 4,092 | |
| currency | 1.0000 | 0 | 0 | 4,097 | **1.0 by construction** — single value (INR) corpus-wide |
| description | 0.9280 | 295 | 25 | 3,777 | 283 of the 295 are text-fidelity only |

**Format-only share, quantified:** 2,370 of the 4,097×5 = 20,485 priority comparisons agree
only after normalisation — 2,345 amounts (int-vs-float serialisation) and 25 descriptions.
Charged as errors by a naive `str()` comparison; they are not errors. The incumbent's
format-only share is larger still (2,225 amounts **and** 1,469 dates, the latter because the
CSV emits ISO `2026-02-20` where both models emit `20/02/2026`).

### Joint row correctness — the number to quote

Rows correct on **all five** priority fields, denominator = every reference row:

| | Luna | incumbent CSV |
|---|---:|---:|
| strict | 3,798 / 4,097 = **92.70 %** | 3,859 / 4,097 = **94.19 %** |
| excluding narration fidelity-only defects | 4,081 / 4,097 = **99.61 %** | 3,860 / 4,097 = **94.22 %** |
| rows defective on date/amount/direction/currency | **5** | 68 |

Held-out (294 statements, 3,544 rows): Luna strict **91.68 %**.

Both readings are published because they rank the arms differently. Strictly, the incumbent
leads, because Luna's 295 description defects outnumber the incumbent's 6. Of Luna's 295,
`desc_defect_classes.json` classes 191 as spacing-only and 92 as a dropped trailing `IN`,
leaving **12 real character differences** — and several of the 191 are the *GT* carrying an
intra-cell line-wrap space mid-word (`Amazon P ay`, `SHIL PA`), i.e. the reference is the
weaker side. Picking one reading silently would repeat the error this fix exists to remove.

## 3. Verdict on the `statementLevelSummary` 1.0 fields

**Not a self-comparison.** 0 of 304 `luna_refined/json/*.json` are byte-identical to their
`opus_gt` counterpart, and four fields disagree loudly (`rawStatementId` null on 304/304;
`rewards.programType` 303/304 wrong; `transactions[].txnType` 484 wrong;
`lastFourDigit` 15 wrong). Recorded in `final_scores.json → self_comparison_guard`.

Twelve non-transaction fields score exactly 1.0. Each is now classified in
`comparisons[*].summary_field_audit`:

| field | classification |
|---|---|
| `totalAmountDue`, `availableCreditLimit`, `totalCreditLimit`, `totalMinimumAmountDue` | **EARNED_AFTER_NORMALISATION** — 120–296 distinct reference values, zero value disagreements; 72 / 74 / 285 / 270 of 304 pairs agree only after numeric normalisation (`415000` vs `415000.0`), which is itself evidence of two independent extractions |
| `statementDate`, `dueDate`, `statementPeriodStart`, `statementPeriodEnd` | **EARNED_BYTE_FOR_BYTE** — 138/139 distinct reference values, 304/304 identical raw strings |
| `statementMeta.issuerName` | **BY CONSTRUCTION** — one distinct value corpus-wide (`ICICI Bank`); solved by emitting a constant, non-differentiating |
| `utilisationPercent@derived` | **DERIVED AND DEPENDENT** — computed from each side's own `totalAmountDue`/`totalCreditLimit`, which already agree 304/304, so the 1.0 follows arithmetically and carries no independent information. The as-extracted counterpart is `both_null` on 304/304 |
| `rewards.openingPoints`, `rewards.closingPoints` | **NEGLIGIBLE SAMPLE** — 1 of 304 pairs scorable, 303 `both_null` |

So: eight genuinely earned (subject to the standing caveat that the GT is a reference, not
truth, and shares a schema instrument with the challenger), four artifacts.

## 4. Both prose-vs-artifact discrepancies adjudicated

**Held-out rows — the ARTIFACT is right (3,544 / 3,379); the prose (3,961 / 3,802) is stale.**
The 10 tuning statements in `phase1_sample.json` hold **553** GT transaction rows between them
(two of them alone hold 150 and 140), so held-out = 4,097 − 553 = 3,544 for Luna and
3,932 − 553 = 3,379 for the incumbent — exactly the artifact figures. The prose's 3,961 implies
those 10 statements hold only 136 rows, contradicting the per-statement artifacts. The prose
was written against an intermediate, partially-complete `scores_phase3.json`: the *committed*
`report_tables.md` at `c3100c2` still shows that snapshot (54 / 97 statements, 801 / 1,304
rows), while the regenerated `report_tables.md` agrees with the artifact at 3,544 / 3,379. The
prose's 3,802 additionally coincides with Luna's all-scope *descriptions exact (casefold)*
figure — a cell transcribed from the wrong row of the generated table.

**PDF adjudication — the ARTIFACT is right (349 CSV_WRONG / 23 LUNA_WRONG); the prose
(347 / 25) predates a fix to the adjudicator.** The decided total is 372 on both sides, so
nothing was added or dropped — exactly two items changed side. `adjudicate.py`'s `verdict_for`
was hardened after the prose was written (`MARKETING` and `TXNROW` snippet exclusions) so that
a network token found only in a cross-sell advert or inside a transaction row no longer counts
as evidence of *this* card's network. Statements **647130** and **870931682**, field
`cards[].cardMeta.network`, now carry `adjudication: CSV_WRONG` with zero `pdf_evidence` and
the reason *"network appears only inside the four-network fuel-surcharge disclaimer, which
identifies no card; Luna's null is correct"*. They were previously charged as LUNA_WRONG.
25 − 2 = 23 and 347 + 2 = 349. The hardening is right on the merits — the Opus GT independently
returns null for both — so **349 / 23** stands, and the report's "14:1" should read **15.2:1**.

## 5. Guard

`test_rollup_honesty.py` (sibling to `test_matcher_noncircular.py`, which still passes all 4
obligations) asserts, against `final_scores.json` as published:

1. no headline transaction metric is exactly 1.0 while the per-field verdicts hold any
   `wrong_value`, unless its path marks it as alignment or it is listed with a stated reason in
   `value_correctness.fields_at_exactly_1_0` — and `luna_transaction_micro_f1` can never return;
2. every exactly-1.0 metric carries an `exactly_1_0_annotation`;
3. `correct == correct_byte_identical + format_only`, verdicts sum to `n`, and `accuracy`
   matches its own numerator/denominator — so `format_only` cannot be folded into `wrong_value`;
4. alignment and correctness are separate, and `joint_row_correctness` is not saturated;
5. the description defect classes still match `desc_defect_classes.json`;
6. `self_comparison_guard` proves the arms are distinct and the 1.0 summary fields were
   re-examined;
7. the top-level and per-comparison keys `sbi/final_scores.json` uses are all still present.

Verified failing on four injected regressions: the withdrawn key reintroduced; an unannotated
1.0 under `value_correctness`; `format_only` folded into `wrong_value`; the alignment block
relabelled `is_correctness_claim: true`.

## 6. Not fixed here

`ICICI_REPORT.md` is hand-written prose, has uncommitted in-flight edits from another author,
and still carries the withdrawn 1.0 headline plus both stale figures. The exact corrections it
needs are enumerated machine-readably in
`final_scores.json → report_prose_corrections_required`.

`sbi/final_scores.json` has the same latent defect in its `txn` block
(`precision = recall = f1 = 1.0` on 3,527/3,527 rows). It never promoted it to a headline, and
that directory is owned by another worker, so it is flagged in
`final_scores.json → notes.transaction_metrics_schema` rather than changed.
