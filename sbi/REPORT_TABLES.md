### Scoreable set

- PDFs discovered: **300**
- CSV rows joining a PDF: **300**
- excluded, no CSV row: 0
- excluded, GT missing/unusable: 200
- excluded, Luna not run: 24
- **scoreable: 76**   held-out (minus 10 tuning): **75**

### Luna (refined) vs Opus-5 GT — ACCURACY — all statements (n=76)

| field | n | correct % | wrong | null_when_populated | hallucinated_when_GT_null | both_null (excl.) |
|---|---:|---:|---:|---:|---:|---:|
| `cardDisplayName` ⚠ | 76 | 98.7 | 1 | 0 | 0 | 0 |
| `lastFourDigit` | 76 | 100.0 | 0 | 0 | 0 | 0 |
| `network` ⚠ | 3 | 33.3 | 0 | 0 | 2 | 73 |
| `sls.totalAmountDue` | 76 | 100.0 | 0 | 0 | 0 | 0 |
| `sls.availableCreditLimit` | 76 | 100.0 | 0 | 0 | 0 | 0 |
| `sls.utilisationPercent` ⚠ | 0 | n/a | 0 | 0 | 0 | 76 |
| `sls.totalCreditLimit` | 76 | 100.0 | 0 | 0 | 0 | 0 |
| `sls.totalMinimumAmountDue` | 76 | 100.0 | 0 | 0 | 0 | 0 |
| `meta.issuerName` ⚠ | 76 | 96.1 | 3 | 0 | 0 | 0 |
| `meta.statementDate` | 76 | 100.0 | 0 | 0 | 0 | 0 |
| `meta.dueDate` | 76 | 78.9 | 0 | 0 | 16 | 0 |
| `txn.date` | 854 | 97.5 | 0 | 0 | 21 | 12 |
| `txn.description` | 866 | 99.9 | 1 | 0 | 0 | 0 |
| `txn.amount` | 866 | 100.0 | 0 | 0 | 0 | 0 |
| `txn.direction` | 866 | 99.8 | 2 | 0 | 0 | 0 |
| `txn.currency` ⚠ | 866 | 100.0 | 0 | 0 | 0 | 0 |

`utilisationPercent` **as-derived** (same formula all three sources): n=76, correct=100.0%, wrong=0, both_null=0

### Luna (refined) vs Opus-5 GT — ACCURACY — HELD-OUT only (n=75)

| field | n | correct % | wrong | null_when_populated | hallucinated_when_GT_null | both_null (excl.) |
|---|---:|---:|---:|---:|---:|---:|
| `cardDisplayName` ⚠ | 75 | 98.7 | 1 | 0 | 0 | 0 |
| `lastFourDigit` | 75 | 100.0 | 0 | 0 | 0 | 0 |
| `network` ⚠ | 3 | 33.3 | 0 | 0 | 2 | 72 |
| `sls.totalAmountDue` | 75 | 100.0 | 0 | 0 | 0 | 0 |
| `sls.availableCreditLimit` | 75 | 100.0 | 0 | 0 | 0 | 0 |
| `sls.utilisationPercent` ⚠ | 0 | n/a | 0 | 0 | 0 | 75 |
| `sls.totalCreditLimit` | 75 | 100.0 | 0 | 0 | 0 | 0 |
| `sls.totalMinimumAmountDue` | 75 | 100.0 | 0 | 0 | 0 | 0 |
| `meta.issuerName` ⚠ | 75 | 96.0 | 3 | 0 | 0 | 0 |
| `meta.statementDate` | 75 | 100.0 | 0 | 0 | 0 | 0 |
| `meta.dueDate` | 75 | 78.7 | 0 | 0 | 16 | 0 |
| `txn.date` | 789 | 97.3 | 0 | 0 | 21 | 12 |
| `txn.description` | 801 | 99.9 | 1 | 0 | 0 | 0 |
| `txn.amount` | 801 | 100.0 | 0 | 0 | 0 | 0 |
| `txn.direction` | 801 | 99.8 | 2 | 0 | 0 | 0 |
| `txn.currency` ⚠ | 801 | 100.0 | 0 | 0 | 0 | 0 |

`utilisationPercent` **as-derived** (same formula all three sources): n=75, correct=100.0%, wrong=0, both_null=0

### Incumbent CSV vs Opus-5 GT — the incumbent's OWN accuracy — all statements (n=76)

| field | n | correct % | wrong | null_when_populated | hallucinated_when_GT_null | both_null (excl.) |
|---|---:|---:|---:|---:|---:|---:|
| `cardDisplayName` ⚠ | 76 | 97.4 | 2 | 0 | 0 | 0 |
| `lastFourDigit` | 76 | 100.0 | 0 | 0 | 0 | 0 |
| `network` ⚠ | 7 | 0.0 | 0 | 1 | 6 | 69 |
| `sls.totalAmountDue` | 76 | 100.0 | 0 | 0 | 0 | 0 |
| `sls.availableCreditLimit` | 76 | 100.0 | 0 | 0 | 0 | 0 |
| `sls.utilisationPercent` ⚠ | 55 | 0.0 | 0 | 0 | 55 | 21 |
| `sls.totalCreditLimit` | 76 | 100.0 | 0 | 0 | 0 | 0 |
| `sls.totalMinimumAmountDue` | 76 | 100.0 | 0 | 0 | 0 | 0 |
| `meta.issuerName` ⚠ | 76 | 96.1 | 3 | 0 | 0 | 0 |
| `meta.statementDate` | 76 | 100.0 | 0 | 0 | 0 | 0 |
| `meta.dueDate` | 76 | 78.9 | 0 | 0 | 16 | 0 |
| `txn.date` | 863 | 96.5 | 0 | 0 | 30 | 3 |
| `txn.description` | 866 | 100.0 | 0 | 0 | 0 | 0 |
| `txn.amount` | 866 | 99.4 | 5 | 0 | 0 | 0 |
| `txn.direction` | 866 | 100.0 | 0 | 0 | 0 | 0 |
| `txn.currency` ⚠ | 866 | 100.0 | 0 | 0 | 0 | 0 |

`utilisationPercent` **as-derived** (same formula all three sources): n=76, correct=100.0%, wrong=0, both_null=0

### Incumbent CSV vs Opus-5 GT — the incumbent's OWN accuracy — HELD-OUT only (n=75)

| field | n | correct % | wrong | null_when_populated | hallucinated_when_GT_null | both_null (excl.) |
|---|---:|---:|---:|---:|---:|---:|
| `cardDisplayName` ⚠ | 75 | 97.3 | 2 | 0 | 0 | 0 |
| `lastFourDigit` | 75 | 100.0 | 0 | 0 | 0 | 0 |
| `network` ⚠ | 7 | 0.0 | 0 | 1 | 6 | 68 |
| `sls.totalAmountDue` | 75 | 100.0 | 0 | 0 | 0 | 0 |
| `sls.availableCreditLimit` | 75 | 100.0 | 0 | 0 | 0 | 0 |
| `sls.utilisationPercent` ⚠ | 55 | 0.0 | 0 | 0 | 55 | 20 |
| `sls.totalCreditLimit` | 75 | 100.0 | 0 | 0 | 0 | 0 |
| `sls.totalMinimumAmountDue` | 75 | 100.0 | 0 | 0 | 0 | 0 |
| `meta.issuerName` ⚠ | 75 | 96.0 | 3 | 0 | 0 | 0 |
| `meta.statementDate` | 75 | 100.0 | 0 | 0 | 0 | 0 |
| `meta.dueDate` | 75 | 78.7 | 0 | 0 | 16 | 0 |
| `txn.date` | 798 | 96.2 | 0 | 0 | 30 | 3 |
| `txn.description` | 801 | 100.0 | 0 | 0 | 0 | 0 |
| `txn.amount` | 801 | 99.4 | 5 | 0 | 0 | 0 |
| `txn.direction` | 801 | 100.0 | 0 | 0 | 0 | 0 |
| `txn.currency` ⚠ | 801 | 100.0 | 0 | 0 | 0 | 0 |

`utilisationPercent` **as-derived** (same formula all three sources): n=75, correct=100.0%, wrong=0, both_null=0

### Luna (refined) vs incumbent CSV — AGREEMENT, not correctness — all statements (n=76)

| field | n | correct % | wrong | null_when_populated | hallucinated_when_GT_null | both_null (excl.) |
|---|---:|---:|---:|---:|---:|---:|
| `cardDisplayName` ⚠ | 76 | 98.7 | 1 | 0 | 0 | 0 |
| `lastFourDigit` | 76 | 100.0 | 0 | 0 | 0 | 0 |
| `network` ⚠ | 8 | 12.5 | 0 | 5 | 2 | 68 |
| `sls.totalAmountDue` | 76 | 100.0 | 0 | 0 | 0 | 0 |
| `sls.availableCreditLimit` | 76 | 100.0 | 0 | 0 | 0 | 0 |
| `sls.utilisationPercent` ⚠ | 55 | 0.0 | 0 | 55 | 0 | 21 |
| `sls.totalCreditLimit` | 76 | 100.0 | 0 | 0 | 0 | 0 |
| `sls.totalMinimumAmountDue` | 76 | 100.0 | 0 | 0 | 0 | 0 |
| `meta.issuerName` ⚠ | 76 | 100.0 | 0 | 0 | 0 | 0 |
| `meta.statementDate` | 76 | 100.0 | 0 | 0 | 0 | 0 |
| `meta.dueDate` | 76 | 100.0 | 0 | 0 | 0 | 0 |
| `txn.date` | 865 | 98.5 | 0 | 11 | 2 | 1 |
| `txn.description` | 866 | 99.9 | 1 | 0 | 0 | 0 |
| `txn.amount` | 866 | 99.4 | 5 | 0 | 0 | 0 |
| `txn.direction` | 866 | 99.8 | 2 | 0 | 0 | 0 |
| `txn.currency` ⚠ | 866 | 100.0 | 0 | 0 | 0 | 0 |

`utilisationPercent` **as-derived** (same formula all three sources): n=76, correct=100.0%, wrong=0, both_null=0

### Luna (refined) vs incumbent CSV — AGREEMENT, not correctness — HELD-OUT only (n=75)

| field | n | correct % | wrong | null_when_populated | hallucinated_when_GT_null | both_null (excl.) |
|---|---:|---:|---:|---:|---:|---:|
| `cardDisplayName` ⚠ | 75 | 98.7 | 1 | 0 | 0 | 0 |
| `lastFourDigit` | 75 | 100.0 | 0 | 0 | 0 | 0 |
| `network` ⚠ | 8 | 12.5 | 0 | 5 | 2 | 67 |
| `sls.totalAmountDue` | 75 | 100.0 | 0 | 0 | 0 | 0 |
| `sls.availableCreditLimit` | 75 | 100.0 | 0 | 0 | 0 | 0 |
| `sls.utilisationPercent` ⚠ | 55 | 0.0 | 0 | 55 | 0 | 20 |
| `sls.totalCreditLimit` | 75 | 100.0 | 0 | 0 | 0 | 0 |
| `sls.totalMinimumAmountDue` | 75 | 100.0 | 0 | 0 | 0 | 0 |
| `meta.issuerName` ⚠ | 75 | 100.0 | 0 | 0 | 0 | 0 |
| `meta.statementDate` | 75 | 100.0 | 0 | 0 | 0 | 0 |
| `meta.dueDate` | 75 | 100.0 | 0 | 0 | 0 | 0 |
| `txn.date` | 800 | 98.4 | 0 | 11 | 2 | 1 |
| `txn.description` | 801 | 99.9 | 1 | 0 | 0 | 0 |
| `txn.amount` | 801 | 99.4 | 5 | 0 | 0 | 0 |
| `txn.direction` | 801 | 99.8 | 2 | 0 | 0 | 0 |
| `txn.currency` ⚠ | 801 | 100.0 | 0 | 0 | 0 | 0 |

`utilisationPercent` **as-derived** (same formula all three sources): n=75, correct=100.0%, wrong=0, both_null=0

### Transactions — precision / recall / F1 / description fidelity

| comparison | ref rows | pred rows | matched | P % | R % | F1 % | recall misses | false positives | description byte-exact |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Luna vs GT (all) | 866 | 866 | 866 | 100.0 | 100.0 | 100.0 | 0 | 0 | 676 (78.1%) |
| CSV vs GT (all) | 866 | 866 | 866 | 100.0 | 100.0 | 100.0 | 0 | 0 | 676 (78.1%) |
| Luna vs CSV (all) | 866 | 866 | 866 | 100.0 | 100.0 | 100.0 | 0 | 0 | 865 (99.9%) |
| Luna vs GT (held-out) | 801 | 801 | 801 | 100.0 | 100.0 | 100.0 | 0 | 0 | 615 (76.8%) |
| CSV vs GT (held-out) | 801 | 801 | 801 | 100.0 | 100.0 | 100.0 | 0 | 0 | 615 (76.8%) |
| Luna vs CSV (held-out) | 801 | 801 | 801 | 100.0 | 100.0 | 100.0 | 0 | 0 | 800 (99.9%) |

### Refinement lift — refined vs CLIENT-baseline prompt, same statements

Common scoreable set: **4** statements.

| field | baseline (client prompt) correct % | refined correct % | delta |
|---|---:|---:|---:|
| `cardDisplayName` | 100.0 | 100.0 | +0.0 |
| `lastFourDigit` | 100.0 | 100.0 | +0.0 |
| `sls.totalAmountDue` | 100.0 | 100.0 | +0.0 |
| `sls.availableCreditLimit` | 100.0 | 100.0 | +0.0 |
| `sls.totalCreditLimit` | 100.0 | 100.0 | +0.0 |
| `sls.totalMinimumAmountDue` | 100.0 | 100.0 | +0.0 |
| `meta.issuerName` | 75.0 | 100.0 | +25.0 |
| `meta.statementDate` | 100.0 | 100.0 | +0.0 |
| `meta.dueDate` | 50.0 | 50.0 | +0.0 |
| `txn.date` | 96.3 | 96.3 | +0.0 |
| `txn.description` | 100.0 | 100.0 | +0.0 |
| `txn.amount` | 100.0 | 100.0 | +0.0 |
| `txn.direction` | 100.0 | 100.0 | +0.0 |
| `txn.currency` | 100.0 | 100.0 | +0.0 |
| **txn recall** | 100.0 | 100.0 | +0.0 |
| **txn rows emitted** | 27 | 27 | +0 |

### Adjudication of Luna-vs-incumbent disagreements against the PDF

77 statements, **14** disagreements adjudicated.

| verdict | count |
|---|---:|
| CSV_WRONG | 9 |
| AMBIGUOUS_IN_PDF | 5 |

| field | verdicts |
|---|---|
| `cards[].cardMeta.network` | CSV_WRONG=4, AMBIGUOUS_IN_PDF=3 |
| `transactions[].amount` | CSV_WRONG=5 |
| `transactions[].direction` | AMBIGUOUS_IN_PDF=2 |

### Token accounting (from VERBATIM persisted `usage` blocks)

| arm | n | input sum | input mean | output sum | output mean | output median | output max | reasoning sum | total sum |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| luna_refined | 77 | 1,442,054 | 18,728 | 97,683 | 1,269 | 1,020 | 3,717 | 34,184 | 1,539,737 |
| luna_client_phase1 | 10 | 182,730 | 18,273 | 32,975 | 3,298 | 3,610 | 7,627 | 4,907 | 215,705 |
| opus_gt | 101 | 2,896,892 | 28,682 | 167,092 | 1,654 | 1,119 | 6,418 | 0 | 3,063,984 |

| arm | prompt+completion==total | records w/ reasoning>0 | reasoning inside completion? | truncated | 429s | attempts>1 |
|---|---|---:|---|---:|---:|---:|
| luna_refined | 77/77 (100.0%) | 77 | YES - prompt+completion==total holds while reasoning>0 | 0 | 0 | 2 |
| luna_client_phase1 | 10/10 (100.0%) | 10 | YES - prompt+completion==total holds while reasoning>0 | 0 | 0 | 0 |
| opus_gt | 101/101 (100.0%) | 0 | N/A - 0 records report reasoning>0 | 0 | 0 | 2 |

- **luna_refined cost**: UNPUBLISHED for databricks-gpt-5-6-luna -- token counts only; no dollar estimate and no interpolation from a sibling model's rate
- **luna_client_phase1 cost**: UNPUBLISHED for databricks-gpt-5-6-luna -- token counts only; no dollar estimate and no interpolation from a sibling model's rate
- **opus_gt cost**: {"note": "Opus 5 published list price, per 1M tokens", "rate_in_per_m": 5.0, "rate_out_per_m": 25.0, "input_usd": 14.4845, "output_usd": 4.1773, "total_usd": 18.6618, "per_statement_usd": 0.18477}

### Outcome tally

| arm | OK |
|---|---:|
| luna_refined | 77 |
| luna_client_phase1 | 10 |
| opus_gt | 101 |
