## Scope actually measured

| | count |
|---|---:|
| PDFs in the ICICI corpus | **304** |
| CSV data rows | 315 |
| CSV rows joined to a PDF (URL-decoded basename) | **304** |
| CSV rows with no PDF on disk | 11 |
| statements with an Opus-5 GT | 97 |
| statements with an incumbent CSV extraction | 304 |
| **scoreable 3-way intersection** | **97** |
| held-out (intersection minus the 10 tuning statements) | **94** |

## Outcome tally

| arm | NETWORK_ERROR | OK | 429-affected calls |
|---|---:|---:|---:|
| `luna_refined` | 1 | 54 | 0 |
| `luna_client_p1` | 0 | 10 | 0 |
| `opus_gt` | 1 | 97 | 0 |

## Token accounting (verbatim `usage`)

| arm | model | calls | input | output | reasoning | total | out/stmt median | out/stmt max |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| Luna 5.6 (refined prompt) | `databricks-gpt-5-6-luna` | 54 | 790,067 | 76,733 | 21,870 | 866,800 | 1,115 | 8,093 |
| Luna 5.6 (client baseline, 10 only) | `databricks-gpt-5-6-luna` | 10 | 164,708 | 36,154 | 4,674 | 200,862 | 3,073 | 8,142 |
| Opus 5 (GT) | `databricks-claude-opus-5` | 97 | 2,518,409 | 184,142 | not reported | 2,702,551 | 1,354 | 14,048 |

* `luna_refined`: {'prompt+completion==total (reasoning INSIDE completion)': 54, 'prompt+completion+reasoning==total (reasoning OUTSIDE)': 0, 'unresolved': 1, 'n': 55}
* `luna_client_p1`: {'prompt+completion==total (reasoning INSIDE completion)': 10, 'prompt+completion+reasoning==total (reasoning OUTSIDE)': 0, 'unresolved': 0, 'n': 10}
* `opus_gt`: {'prompt+completion==total (reasoning INSIDE completion)': 97, 'prompt+completion+reasoning==total (reasoning OUTSIDE)': 0, 'unresolved': 1, 'n': 98}

* Opus 5 cost at its published rate ($5.0/M in, $25.0/M out): **$17.2**
* Luna cost: **UNPUBLISHED_PRICE__TOKEN_COUNTS_ONLY** — Luna's price is unpublished, so no dollar figure is given and none is interpolated from a sibling model.

## Field-by-field — ALL statements

### Luna (refined) vs Opus-5 GT — ACCURACY

| field | n | scored | acc | wrong | null | halluc |
|---|---:|---:|---:|---:|---:|---:|
| `cards[].cardMeta.cardDisplayName` | 70 | 70 | **94.29%** | 3 | 1 | 0 |
| `cards[].cardMeta.lastFourDigit` | 70 | 70 | **94.29%** | 4 | 0 | 0 |
| `cards[].cardMeta.network` | 70 | 3 | **0.00%** | 0 | 3 | 0 |
| `statementLevelSummary.totalAmountDue` | 54 | 54 | **100.00%** | 0 | 0 | 0 |
| `statementLevelSummary.availableCreditLimit` | 54 | 54 | **100.00%** | 0 | 0 | 0 |
| `statementLevelSummary.utilisationPercent@extracted` | 54 | 0 | **n/a** | 0 | 0 | 0 |
| `statementLevelSummary.utilisationPercent@derived` | 54 | 54 | **100.00%** | 0 | 0 | 0 |
| `statementLevelSummary.totalCreditLimit` | 54 | 54 | **100.00%** | 0 | 0 | 0 |
| `statementLevelSummary.totalMinimumAmountDue` | 54 | 54 | **100.00%** | 0 | 0 | 0 |
| `statementMeta.issuerName` | 54 | 54 | **100.00%** | 0 | 0 | 0 |
| `statementMeta.statementDate` | 54 | 54 | **100.00%** | 0 | 0 | 0 |
| `statementMeta.dueDate` | 54 | 54 | **100.00%** | 0 | 0 | 0 |
| `transactions[].date` | 801 | 801 | **100.00%** | 0 | 0 | 0 |
| `transactions[].description` | 801 | 801 | **98.00%** | 16 | 0 | 0 |
| `transactions[].amount` | 801 | 801 | **100.00%** | 0 | 0 | 0 |
| `transactions[].direction` | 801 | 801 | **100.00%** | 0 | 0 | 0 |
| `transactions[].currency` | 801 | 801 | **100.00%** | 0 | 0 | 0 |

### Incumbent CSV vs Opus-5 GT — INCUMBENT ACCURACY

| field | n | scored | acc | wrong | null | halluc |
|---|---:|---:|---:|---:|---:|---:|
| `cards[].cardMeta.cardDisplayName` | 124 | 123 | **74.80%** | 10 | 18 | 3 |
| `cards[].cardMeta.lastFourDigit` | 124 | 124 | **75.00%** | 12 | 19 | 0 |
| `cards[].cardMeta.network` | 124 | 33 | **3.03%** | 0 | 3 | 29 |
| `statementLevelSummary.totalAmountDue` | 97 | 97 | **100.00%** | 0 | 0 | 0 |
| `statementLevelSummary.availableCreditLimit` | 97 | 97 | **100.00%** | 0 | 0 | 0 |
| `statementLevelSummary.utilisationPercent@extracted` | 97 | 62 | **0.00%** | 0 | 0 | 62 |
| `statementLevelSummary.utilisationPercent@derived` | 97 | 97 | **100.00%** | 0 | 0 | 0 |
| `statementLevelSummary.totalCreditLimit` | 97 | 97 | **100.00%** | 0 | 0 | 0 |
| `statementLevelSummary.totalMinimumAmountDue` | 97 | 97 | **100.00%** | 0 | 0 | 0 |
| `statementMeta.issuerName` | 97 | 97 | **100.00%** | 0 | 0 | 0 |
| `statementMeta.statementDate` | 97 | 97 | **100.00%** | 0 | 0 | 0 |
| `statementMeta.dueDate` | 97 | 97 | **100.00%** | 0 | 0 | 0 |
| `transactions[].date` | 1304 | 1304 | **100.00%** | 0 | 0 | 0 |
| `transactions[].description` | 1304 | 1304 | **99.92%** | 1 | 0 | 0 |
| `transactions[].amount` | 1304 | 1304 | **98.70%** | 17 | 0 | 0 |
| `transactions[].direction` | 1304 | 1304 | **100.00%** | 0 | 0 | 0 |
| `transactions[].currency` | 1304 | 1304 | **99.00%** | 1 | 12 | 0 |

### Luna (refined) vs incumbent CSV — AGREEMENT (not accuracy)

| field | n | scored | acc | wrong | null | halluc |
|---|---:|---:|---:|---:|---:|---:|
| `cards[].cardMeta.cardDisplayName` | 70 | 70 | **78.57%** | 4 | 1 | 10 |
| `cards[].cardMeta.lastFourDigit` | 70 | 70 | **81.43%** | 3 | 0 | 10 |
| `cards[].cardMeta.network` | 70 | 18 | **0.00%** | 0 | 18 | 0 |
| `statementLevelSummary.totalAmountDue` | 54 | 54 | **100.00%** | 0 | 0 | 0 |
| `statementLevelSummary.availableCreditLimit` | 54 | 54 | **100.00%** | 0 | 0 | 0 |
| `statementLevelSummary.utilisationPercent@extracted` | 54 | 31 | **0.00%** | 0 | 31 | 0 |
| `statementLevelSummary.utilisationPercent@derived` | 54 | 54 | **100.00%** | 0 | 0 | 0 |
| `statementLevelSummary.totalCreditLimit` | 54 | 54 | **100.00%** | 0 | 0 | 0 |
| `statementLevelSummary.totalMinimumAmountDue` | 54 | 54 | **100.00%** | 0 | 0 | 0 |
| `statementMeta.issuerName` | 54 | 54 | **100.00%** | 0 | 0 | 0 |
| `statementMeta.statementDate` | 54 | 54 | **100.00%** | 0 | 0 | 0 |
| `statementMeta.dueDate` | 54 | 54 | **100.00%** | 0 | 0 | 0 |
| `transactions[].date` | 777 | 777 | **100.00%** | 0 | 0 | 0 |
| `transactions[].description` | 777 | 777 | **97.94%** | 16 | 0 | 0 |
| `transactions[].amount` | 777 | 777 | **98.20%** | 14 | 0 | 0 |
| `transactions[].direction` | 777 | 777 | **100.00%** | 0 | 0 | 0 |
| `transactions[].currency` | 777 | 777 | **100.00%** | 0 | 0 | 0 |

## Field-by-field — HELD OUT (excludes the 10 tuning statements)

### Luna (refined) vs Opus-5 GT — held-out ACCURACY

| field | n | scored | acc | wrong | null | halluc |
|---|---:|---:|---:|---:|---:|---:|
| `cards[].cardMeta.cardDisplayName` | 68 | 68 | **95.59%** | 3 | 0 | 0 |
| `cards[].cardMeta.lastFourDigit` | 68 | 68 | **94.12%** | 4 | 0 | 0 |
| `cards[].cardMeta.network` | 68 | 3 | **0.00%** | 0 | 3 | 0 |
| `statementLevelSummary.totalAmountDue` | 52 | 52 | **100.00%** | 0 | 0 | 0 |
| `statementLevelSummary.availableCreditLimit` | 52 | 52 | **100.00%** | 0 | 0 | 0 |
| `statementLevelSummary.utilisationPercent@extracted` | 52 | 0 | **n/a** | 0 | 0 | 0 |
| `statementLevelSummary.utilisationPercent@derived` | 52 | 52 | **100.00%** | 0 | 0 | 0 |
| `statementLevelSummary.totalCreditLimit` | 52 | 52 | **100.00%** | 0 | 0 | 0 |
| `statementLevelSummary.totalMinimumAmountDue` | 52 | 52 | **100.00%** | 0 | 0 | 0 |
| `statementMeta.issuerName` | 52 | 52 | **100.00%** | 0 | 0 | 0 |
| `statementMeta.statementDate` | 52 | 52 | **100.00%** | 0 | 0 | 0 |
| `statementMeta.dueDate` | 52 | 52 | **100.00%** | 0 | 0 | 0 |
| `transactions[].date` | 643 | 643 | **100.00%** | 0 | 0 | 0 |
| `transactions[].description` | 643 | 643 | **97.82%** | 14 | 0 | 0 |
| `transactions[].amount` | 643 | 643 | **100.00%** | 0 | 0 | 0 |
| `transactions[].direction` | 643 | 643 | **100.00%** | 0 | 0 | 0 |
| `transactions[].currency` | 643 | 643 | **100.00%** | 0 | 0 | 0 |

### Incumbent CSV vs Opus-5 GT — held-out

| field | n | scored | acc | wrong | null | halluc |
|---|---:|---:|---:|---:|---:|---:|
| `cards[].cardMeta.cardDisplayName` | 121 | 120 | **75.00%** | 9 | 18 | 3 |
| `cards[].cardMeta.lastFourDigit` | 121 | 121 | **74.38%** | 12 | 19 | 0 |
| `cards[].cardMeta.network` | 121 | 33 | **3.03%** | 0 | 3 | 29 |
| `statementLevelSummary.totalAmountDue` | 94 | 94 | **100.00%** | 0 | 0 | 0 |
| `statementLevelSummary.availableCreditLimit` | 94 | 94 | **100.00%** | 0 | 0 | 0 |
| `statementLevelSummary.utilisationPercent@extracted` | 94 | 61 | **0.00%** | 0 | 0 | 61 |
| `statementLevelSummary.utilisationPercent@derived` | 94 | 94 | **100.00%** | 0 | 0 | 0 |
| `statementLevelSummary.totalCreditLimit` | 94 | 94 | **100.00%** | 0 | 0 | 0 |
| `statementLevelSummary.totalMinimumAmountDue` | 94 | 94 | **100.00%** | 0 | 0 | 0 |
| `statementMeta.issuerName` | 94 | 94 | **100.00%** | 0 | 0 | 0 |
| `statementMeta.statementDate` | 94 | 94 | **100.00%** | 0 | 0 | 0 |
| `statementMeta.dueDate` | 94 | 94 | **100.00%** | 0 | 0 | 0 |
| `transactions[].date` | 1127 | 1127 | **100.00%** | 0 | 0 | 0 |
| `transactions[].description` | 1127 | 1127 | **99.91%** | 1 | 0 | 0 |
| `transactions[].amount` | 1127 | 1127 | **99.20%** | 9 | 0 | 0 |
| `transactions[].direction` | 1127 | 1127 | **100.00%** | 0 | 0 | 0 |
| `transactions[].currency` | 1127 | 1127 | **98.85%** | 1 | 12 | 0 |

## Transactions

| metric | luna_refined_vs_GT__all | CSV_vs_GT__all | luna_refined_vs_GT__heldout | CSV_vs_GT__heldout |
|---|---:|---:|---:|---:|
| statements | 54 | 97 | 52 | 94 |
| rows (pred) | 801 | 1,304 | 643 | 1,127 |
| rows (ref) | 801 | 1,328 | 643 | 1,151 |
| rows matched | 801 | 1,304 | 643 | 1,127 |
| micro precision | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| micro recall | 1.0000 | 0.9819 | 1.0000 | 0.9791 |
| micro F1 | 1.0000 | 0.9909 | 1.0000 | 0.9895 |
| macro F1 (per-statement mean) | 1.0000 | 0.9954 | 1.0000 | 0.9952 |
| mean description similarity | 0.9998 | 1.0000 | 0.9998 | 1.0000 |
| descriptions exact char-for-char | 784 | 1,301 | 628 | 1,124 |
| descriptions exact (casefold) | 785 | 1,303 | 629 | 1,126 |
| statements with exact row count | 54 | 95 | 52 | 92 |

## Fields where a high score must NOT be read as earned

Discriminating power MEASURED in the Opus-5 GT — a field whose single most common value covers ~all instances is trivially solved, so a high score on it reflects the corpus, not the model.

| field | instances | distinct values | top value | top share | verdict |
|---|---:|---:|---|---:|---|
| `statementLevelSummary.utilisationPercent` | 100 | 1 | `None` | 100.0% | **TRIVIALLY_SOLVED (near-constant)** |
| `statementMeta.issuerName` | 100 | 1 | `ICICI Bank` | 100.0% | **TRIVIALLY_SOLVED (near-constant)** |
| `transactions[].currency` | 1,401 | 1 | `INR` | 100.0% | **TRIVIALLY_SOLVED (near-constant)** |
| `cards[].cardMeta.network` | 127 | 3 | `None` | 96.9% | **TRIVIALLY_SOLVED (near-constant)** |
| `transactions[].direction` | 1,401 | 2 | `DEBIT` | 82.8% | **LOW_DISCRIMINATION** |
| `cards[].cardMeta.cardDisplayName` | 127 | 14 | `Amazon Pay ICICI Bank Credit` | 47.2% | DISCRIMINATING |
| `statementLevelSummary.totalMinimumAmountDue` | 100 | 72 | `0.0` | 13.0% | DISCRIMINATING |
| `transactions[].description` | 1,401 | 678 | `BBPS Payment received` | 8.6% | DISCRIMINATING |
| `statementLevelSummary.totalAmountDue` | 100 | 93 | `0.0` | 8.0% | DISCRIMINATING |
| `statementMeta.statementDate` | 100 | 51 | `12/04/2026` | 6.0% | DISCRIMINATING |
| `statementMeta.dueDate` | 100 | 51 | `30/04/2026` | 6.0% | DISCRIMINATING |
| `statementLevelSummary.totalCreditLimit` | 100 | 59 | `1000000.0` | 5.0% | DISCRIMINATING |
| `cards[].cardMeta.lastFourDigit` | 127 | 87 | `9000` | 3.1% | DISCRIMINATING |
| `transactions[].date` | 1,401 | 263 | `31/03/2026` | 2.5% | DISCRIMINATING |
| `statementLevelSummary.availableCreditLimit` | 100 | 99 | `0.0` | 2.0% | DISCRIMINATING |
| `transactions[].amount` | 1,401 | 1032 | `400.0` | 1.5% | DISCRIMINATING |

* `statementMeta.issuerName` — NON-DISCRIMINATING: single issuer, 303/304 incumbent rows are 'ICICI Bank'
* `transactions[].currency` — NEAR-CONSTANT: 3,917/3,932 incumbent rows are INR
* `cards[].cardMeta.cardDisplayName` — LENIENT SCORING (substring match); unstable run-to-run even inside the GT
* `cards[].cardMeta.network` — TRIVIAL-NULL: not printed on ICICI statements; almost all pairs are both_null
* `statementLevelSummary.utilisationPercent@extracted` — NOT PRINTED IN ANY PDF (0/304 contain 'utilis'); no model emits it
* `statementLevelSummary.utilisationPercent@derived` — ARITHMETIC, not extraction: each side derived from its OWN totalAmountDue/totalCreditLimit

## Adjudication of Luna-vs-incumbent disagreements (against the PDF)

340 priority-field disagreements adjudicated across 127 statements.

| classification | count |
|---|---:|
| **CSV_WRONG** | 165 |
| **AMBIGUOUS_IN_PDF** | 164 |
| **LUNA_WRONG** | 11 |

| field | AMBIGUOUS_IN_PDF | CSV_WRONG | LUNA_WRONG |
|---|---:|---:|---:|
| `transactions[].description` | 111 | 3 | 5 |
| `statementLevelSummary.utilisationPercent@extracted` | 0 | 77 | 0 |
| `cards[].cardMeta.cardDisplayName` | 35 | 3 | 0 |
| `cards[].cardMeta.network` | 0 | 35 | 0 |
| `cards[].cardMeta.lastFourDigit` | 1 | 22 | 6 |
| `transactions[].amount` | 4 | 18 | 0 |
| `transactions[].currency` | 13 | 0 | 0 |
| `statementLevelSummary.availableCreditLimit` | 0 | 1 | 0 |
| `statementLevelSummary.totalAmountDue` | 0 | 1 | 0 |
| `statementLevelSummary.totalCreditLimit` | 0 | 1 | 0 |
| `statementLevelSummary.totalMinimumAmountDue` | 0 | 1 | 0 |
| `statementMeta.dueDate` | 0 | 1 | 0 |
| `statementMeta.issuerName` | 0 | 1 | 0 |
| `statementMeta.statementDate` | 0 | 1 | 0 |
