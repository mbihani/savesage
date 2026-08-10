## Corpus and join

| | |
|---|---:|
| PDFs on disk | 281 |
| CSV data rows | 300 |
| **Joined / scoreable** | **281** |
| CSV rows not joining | 19 |
| PDFs with no CSV row | 0 |
| Opus-5 GT usable | 69 |

join reaches 281/300 once the CSV link basename is URL-DECODED; the 19 non-joining CSV rows are exactly the 19 entries of failed-download-links.txt (PDFs never downloaded)


## Outcome tally

| run | outcomes |
|---|---|
| gt_opus | `{'OK': 69}` |
| luna_generic_sample | `{'OK': 10}` |
| luna_refined | `{'OK': 14}` |
| luna_refined_sample | `{'OK': 10}` |


## Token accounting

| run | calls | input total | output total | reasoning total | grand total | in mean/med/max | out mean/med/max | reasoning mean/med/max | p+c==total | reasoning inside completion |
|---|---:|---:|---:|---:|---:|---|---|---|---:|---|
| gt_opus | 69 | 882,327 | 149,054 | 0 | 1,031,381 | 12787.3/12988/17238 | 2160.2/1662/7058 | None/None/None | 69/69 | None |
| luna_generic_sample | 10 | 94,718 | 46,350 | 4,823 | 141,068 | 9471.8/9717.0/13198 | 4635.0/4069.0/11688 | 482.3/381.5/1024 | 10/10 | True |
| luna_refined | 14 | 129,715 | 23,463 | 7,795 | 153,178 | 9265.4/9377.0/11145 | 1675.9/1872.5/3073 | 556.8/541.5/940 | 14/14 | True |
| luna_refined_sample | 10 | 114,748 | 47,741 | 6,269 | 162,489 | 11474.8/11720.0/15201 | 4774.1/4317.0/11553 | 626.9/629.0/985 | 10/10 | True |

Opus-5 GT cost at published rate ($5/M in, $25/M out): **$8.14**. Luna's price is unpublished — token counts only, no dollar figure.


## Field-by-field

**Refined Luna vs Opus-5 GT — ALL statements** — n=14 statements. ACCURACY (vs Opus-5 GT)

| field | n | accuracy | wrong | null_when_populated | hallucinated_when_GT_null | note |
|---|---:|---:|---:|---:|---:|---|
| cardDisplayName | 14 | 85.71% | 2 | 0 | 0 | LENIENT (containment); unstable run-to-run |
| lastFourDigit | 14 | 100.00% | 0 | 0 | 0 |  |
| network | 14 | 100.00% | 0 | 0 | 0 |  |
| sls.totalAmountDue | 14 | 100.00% | 0 | 0 | 0 |  |
| sls.availableCreditLimit | 14 | 100.00% | 0 | 0 | 0 |  |
| sls.utilisationPercent (as-extracted) | 14 | 100.00% | 0 | 0 | 0 | printed in 0/281 PDFs |
| sls.utilisationPercent (derived) | 14 | 100.00% | 0 | 0 | 0 |  |
| sls.totalCreditLimit | 14 | 100.00% | 0 | 0 | 0 |  |
| sls.totalMinimumAmountDue | 14 | 100.00% | 0 | 0 | 0 |  |
| meta.issuerName | 14 | 100.00% | 0 | 0 | 0 | NON-DISCRIMINATING (single issuer, 281/281 HDFC Bank) |
| meta.statementDate | 14 | 100.00% | 0 | 0 | 0 |  |
| meta.dueDate | 14 | 100.00% | 0 | 0 | 0 |  |
| transactions.date | 234 | 100.00% | 0 | 0 | 0 |  |
| transactions.description | 234 | 97.01% | 7 | 0 | 0 |  |
| transactions.amount | 234 | 100.00% | 0 | 0 | 0 |  |
| transactions.direction | 234 | 98.29% | 4 | 0 | 0 |  |
| transactions.currency | 234 | 98.72% | 3 | 0 | 0 | NEAR-CONSTANT (98.7% INR) |

Transaction matching (description-only 1:1): pairs=234, pred-only=0, gold-only=0, **P=100.00% R=100.00% F1=100.00%**, description exact-match=97.01%, mean similarity=0.9958


**Refined Luna vs Opus-5 GT — HELD-OUT (excl. 10 tuning)** — n=14 statements. ACCURACY (vs Opus-5 GT)

| field | n | accuracy | wrong | null_when_populated | hallucinated_when_GT_null | note |
|---|---:|---:|---:|---:|---:|---|
| cardDisplayName | 14 | 85.71% | 2 | 0 | 0 | LENIENT (containment); unstable run-to-run |
| lastFourDigit | 14 | 100.00% | 0 | 0 | 0 |  |
| network | 14 | 100.00% | 0 | 0 | 0 |  |
| sls.totalAmountDue | 14 | 100.00% | 0 | 0 | 0 |  |
| sls.availableCreditLimit | 14 | 100.00% | 0 | 0 | 0 |  |
| sls.utilisationPercent (as-extracted) | 14 | 100.00% | 0 | 0 | 0 | printed in 0/281 PDFs |
| sls.utilisationPercent (derived) | 14 | 100.00% | 0 | 0 | 0 |  |
| sls.totalCreditLimit | 14 | 100.00% | 0 | 0 | 0 |  |
| sls.totalMinimumAmountDue | 14 | 100.00% | 0 | 0 | 0 |  |
| meta.issuerName | 14 | 100.00% | 0 | 0 | 0 | NON-DISCRIMINATING (single issuer, 281/281 HDFC Bank) |
| meta.statementDate | 14 | 100.00% | 0 | 0 | 0 |  |
| meta.dueDate | 14 | 100.00% | 0 | 0 | 0 |  |
| transactions.date | 234 | 100.00% | 0 | 0 | 0 |  |
| transactions.description | 234 | 97.01% | 7 | 0 | 0 |  |
| transactions.amount | 234 | 100.00% | 0 | 0 | 0 |  |
| transactions.direction | 234 | 98.29% | 4 | 0 | 0 |  |
| transactions.currency | 234 | 98.72% | 3 | 0 | 0 | NEAR-CONSTANT (98.7% INR) |

Transaction matching (description-only 1:1): pairs=234, pred-only=0, gold-only=0, **P=100.00% R=100.00% F1=100.00%**, description exact-match=97.01%, mean similarity=0.9958


**Incumbent CSV vs Opus-5 GT — ALL statements** — n=69 statements. ACCURACY (vs Opus-5 GT)

| field | n | accuracy | wrong | null_when_populated | hallucinated_when_GT_null | note |
|---|---:|---:|---:|---:|---:|---|
| cardDisplayName | 69 | 97.10% | 2 | 0 | 0 | LENIENT (containment); unstable run-to-run |
| lastFourDigit | 69 | 100.00% | 0 | 0 | 0 |  |
| network | 69 | 94.20% | 0 | 0 | 4 |  |
| sls.totalAmountDue | 69 | 100.00% | 0 | 0 | 0 |  |
| sls.availableCreditLimit | 69 | 100.00% | 0 | 0 | 0 |  |
| sls.utilisationPercent (as-extracted) | 69 | 57.97% | 0 | 0 | 29 | printed in 0/281 PDFs |
| sls.utilisationPercent (derived) | 69 | 100.00% | 0 | 0 | 0 |  |
| sls.totalCreditLimit | 69 | 100.00% | 0 | 0 | 0 |  |
| sls.totalMinimumAmountDue | 69 | 100.00% | 0 | 0 | 0 |  |
| meta.issuerName | 69 | 100.00% | 0 | 0 | 0 | NON-DISCRIMINATING (single issuer, 281/281 HDFC Bank) |
| meta.statementDate | 69 | 100.00% | 0 | 0 | 0 |  |
| meta.dueDate | 69 | 100.00% | 0 | 0 | 0 |  |
| transactions.date | 1137 | 100.00% | 0 | 0 | 0 |  |
| transactions.description | 1137 | 96.57% | 39 | 0 | 0 |  |
| transactions.amount | 1137 | 99.47% | 6 | 0 | 0 |  |
| transactions.direction | 1137 | 99.65% | 4 | 0 | 0 |  |
| transactions.currency | 1137 | 95.87% | 4 | 43 | 0 | NEAR-CONSTANT (98.7% INR) |

Transaction matching (description-only 1:1): pairs=1137, pred-only=0, gold-only=0, **P=100.00% R=100.00% F1=100.00%**, description exact-match=96.57%, mean similarity=0.9954


**Incumbent CSV vs Opus-5 GT — HELD-OUT** — n=67 statements. ACCURACY (vs Opus-5 GT)

| field | n | accuracy | wrong | null_when_populated | hallucinated_when_GT_null | note |
|---|---:|---:|---:|---:|---:|---|
| cardDisplayName | 67 | 97.01% | 2 | 0 | 0 | LENIENT (containment); unstable run-to-run |
| lastFourDigit | 67 | 100.00% | 0 | 0 | 0 |  |
| network | 67 | 94.03% | 0 | 0 | 4 |  |
| sls.totalAmountDue | 67 | 100.00% | 0 | 0 | 0 |  |
| sls.availableCreditLimit | 67 | 100.00% | 0 | 0 | 0 |  |
| sls.utilisationPercent (as-extracted) | 67 | 58.21% | 0 | 0 | 28 | printed in 0/281 PDFs |
| sls.utilisationPercent (derived) | 67 | 100.00% | 0 | 0 | 0 |  |
| sls.totalCreditLimit | 67 | 100.00% | 0 | 0 | 0 |  |
| sls.totalMinimumAmountDue | 67 | 100.00% | 0 | 0 | 0 |  |
| meta.issuerName | 67 | 100.00% | 0 | 0 | 0 | NON-DISCRIMINATING (single issuer, 281/281 HDFC Bank) |
| meta.statementDate | 67 | 100.00% | 0 | 0 | 0 |  |
| meta.dueDate | 67 | 100.00% | 0 | 0 | 0 |  |
| transactions.date | 1092 | 100.00% | 0 | 0 | 0 |  |
| transactions.description | 1092 | 96.43% | 39 | 0 | 0 |  |
| transactions.amount | 1092 | 99.54% | 5 | 0 | 0 |  |
| transactions.direction | 1092 | 99.63% | 4 | 0 | 0 |  |
| transactions.currency | 1092 | 95.70% | 4 | 43 | 0 | NEAR-CONSTANT (98.7% INR) |

Transaction matching (description-only 1:1): pairs=1092, pred-only=0, gold-only=0, **P=100.00% R=100.00% F1=100.00%**, description exact-match=96.43%, mean similarity=0.9952


**Refined Luna vs Incumbent CSV — ALL (AGREEMENT)** — n=14 statements. AGREEMENT (incumbent CSV is not ground truth)

| field | n | accuracy | wrong | null_when_populated | hallucinated_when_GT_null | note |
|---|---:|---:|---:|---:|---:|---|
| cardDisplayName | 14 | 92.86% | 1 | 0 | 0 | LENIENT (containment); unstable run-to-run |
| lastFourDigit | 14 | 100.00% | 0 | 0 | 0 |  |
| network | 14 | 92.86% | 0 | 1 | 0 |  |
| sls.totalAmountDue | 14 | 100.00% | 0 | 0 | 0 |  |
| sls.availableCreditLimit | 14 | 100.00% | 0 | 0 | 0 |  |
| sls.utilisationPercent (as-extracted) | 14 | 85.71% | 0 | 2 | 0 | printed in 0/281 PDFs |
| sls.utilisationPercent (derived) | 14 | 100.00% | 0 | 0 | 0 |  |
| sls.totalCreditLimit | 14 | 100.00% | 0 | 0 | 0 |  |
| sls.totalMinimumAmountDue | 14 | 100.00% | 0 | 0 | 0 |  |
| meta.issuerName | 14 | 100.00% | 0 | 0 | 0 | NON-DISCRIMINATING (single issuer, 281/281 HDFC Bank) |
| meta.statementDate | 14 | 100.00% | 0 | 0 | 0 |  |
| meta.dueDate | 14 | 100.00% | 0 | 0 | 0 |  |
| transactions.date | 234 | 100.00% | 0 | 0 | 0 |  |
| transactions.description | 234 | 97.01% | 7 | 0 | 0 |  |
| transactions.amount | 234 | 98.29% | 4 | 0 | 0 |  |
| transactions.direction | 234 | 98.29% | 4 | 0 | 0 |  |
| transactions.currency | 234 | 98.72% | 3 | 0 | 0 | NEAR-CONSTANT (98.7% INR) |

Transaction matching (description-only 1:1): pairs=234, pred-only=0, gold-only=0, **P=100.00% R=100.00% F1=100.00%**, description exact-match=97.01%, mean similarity=0.9936


**Refined Luna vs Incumbent CSV — HELD-OUT (AGREEMENT)** — n=14 statements. AGREEMENT (incumbent CSV is not ground truth)

| field | n | accuracy | wrong | null_when_populated | hallucinated_when_GT_null | note |
|---|---:|---:|---:|---:|---:|---|
| cardDisplayName | 14 | 92.86% | 1 | 0 | 0 | LENIENT (containment); unstable run-to-run |
| lastFourDigit | 14 | 100.00% | 0 | 0 | 0 |  |
| network | 14 | 92.86% | 0 | 1 | 0 |  |
| sls.totalAmountDue | 14 | 100.00% | 0 | 0 | 0 |  |
| sls.availableCreditLimit | 14 | 100.00% | 0 | 0 | 0 |  |
| sls.utilisationPercent (as-extracted) | 14 | 85.71% | 0 | 2 | 0 | printed in 0/281 PDFs |
| sls.utilisationPercent (derived) | 14 | 100.00% | 0 | 0 | 0 |  |
| sls.totalCreditLimit | 14 | 100.00% | 0 | 0 | 0 |  |
| sls.totalMinimumAmountDue | 14 | 100.00% | 0 | 0 | 0 |  |
| meta.issuerName | 14 | 100.00% | 0 | 0 | 0 | NON-DISCRIMINATING (single issuer, 281/281 HDFC Bank) |
| meta.statementDate | 14 | 100.00% | 0 | 0 | 0 |  |
| meta.dueDate | 14 | 100.00% | 0 | 0 | 0 |  |
| transactions.date | 234 | 100.00% | 0 | 0 | 0 |  |
| transactions.description | 234 | 97.01% | 7 | 0 | 0 |  |
| transactions.amount | 234 | 98.29% | 4 | 0 | 0 |  |
| transactions.direction | 234 | 98.29% | 4 | 0 | 0 |  |
| transactions.currency | 234 | 98.72% | 3 | 0 | 0 | NEAR-CONSTANT (98.7% INR) |

Transaction matching (description-only 1:1): pairs=234, pred-only=0, gold-only=0, **P=100.00% R=100.00% F1=100.00%**, description exact-match=97.01%, mean similarity=0.9936


_GENERIC-prompt Luna vs Opus-5 GT — ALL: not run._


_GENERIC-prompt Luna vs Incumbent CSV — ALL: not run._



## Adjudication of Luna-vs-CSV disagreements (statement level)

| field | disagreements | LUNA_WRONG | CSV_WRONG | BOTH_WRONG | AMBIGUOUS_IN_PDF | Luna right (of separable) |
|---|---:|---:|---:|---:|---:|---:|
| cardDisplayName | 1 | 1 | 0 | 0 | 0 | 0.00% |
| network | 1 | 0 | 1 | 0 | 0 | 100.00% |

Overall: `{'CSV_WRONG': 1, 'LUNA_WRONG': 1}`
Held-out only: `{'CSV_WRONG': 1, 'LUNA_WRONG': 1}`


## Adjudication of Luna-vs-CSV disagreements (transaction level)

| field | disagreements | LUNA_WRONG | CSV_WRONG | BOTH_WRONG | AMBIGUOUS_IN_PDF | Luna right (of separable) |
|---|---:|---:|---:|---:|---:|---:|
| direction | 4 | 2 | 2 | 0 | 0 | 50.00% |
| currency | 3 | 0 | 0 | 0 | 3 | — |
| amount | 1 | 0 | 0 | 0 | 1 | — |
| description | 1 | 0 | 0 | 0 | 1 | — |

Overall: `{'AMBIGUOUS_IN_PDF': 5, 'CSV_WRONG': 2, 'LUNA_WRONG': 2}`


## Glaring misses — counts

| | count |
|---|---:|
| ambiguous_in_pdf | 5 |
| both_wrong | 0 |
| incumbent_substantive_errors | 3 |
| luna_substantive_errors | 3 |
| statements_where_csv_lacks_rows_luna_has | 0 |
| statements_where_luna_lacks_rows_csv_has | 0 |

Held-out only: `{'incumbent_substantive_errors': 3, 'luna_substantive_errors': 3}`

By field — Luna: `{'cardDisplayName': 1, 'direction': 2}`

By field — incumbent: `{'direction': 2, 'network': 1}`
