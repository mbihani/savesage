# ICICI — Luna 5.6 native-PDF extraction evaluation (FINAL)

**Run status: COMPLETE — 304/304 statements on every arm.**

**This document supersedes `ICICI_REPORT.md`.** Every number here is recomputed from
`icici/final_scores.json`; `MEASUREMENT_FIX.md` documents the defect that made the
earlier headline wrong. Guard suites `test_rollup_honesty.py` (7 obligations) and
`test_matcher_noncircular.py` (4 obligations) both pass against the published artifact.

| role | system | instrument |
|---|---|---|
| **Challenger** | `databricks-gpt-5-6-luna`, native PDF | refined `ICICI_PROMPT.txt` |
| **Reference ("GT")** | `databricks-claude-opus-5`, native PDF | `gt298_lib.GT_PROMPT` + `GT_SCHEMA`, unchanged |
| **Incumbent** | the client's existing parser | its output as delivered in `icici.csv` |

Excluded arms: `phase1_luna_client` (10-record tuning baseline) and
`phase1_luna_generic` (10-record Axis-contaminated generic arm).

---

## 1. Headline — and it is genuinely two-sided

**Do not quote a bare "Luna wins" for ICICI.** Which system leads depends on whether
you charge Luna for narration *fidelity*, so both readings are published.

Rows correct on **all five** priority fields, over all 4,097 reference rows:

| reading | Luna | incumbent CSV | leader |
|---|---:|---:|---|
| **Strict** (narration fidelity charged) | **92.70%** (3,798) | **94.19%** (3,859) | **incumbent** |
| **Narration-fidelity forgiven** | **99.61%** (4,081) | **94.22%** (3,860) | **Luna** |
| Rows defective on `date`/`amount`/`direction`/`currency` | **5** | **68** | **Luna** |

The honest summary: **on the financial values that matter, Luna is dramatically more
accurate — 5 defective rows against the incumbent's 68.** It loses the strict reading
solely on cosmetic transcription of narration text.

Per-field, Luna vs GT:

| field | accuracy | wrong_value | format_only (not charged) |
|---|---:|---:|---:|
| `date` | 100.00% | 0 | 0 |
| `amount` | 99.95% | 2 | 2,345 |
| `direction` | 99.88% | 5 | 0 |
| `currency` | 100.00% (by construction) | 0 | 0 |
| `description` | 92.80% | 295 | 25 |

Incumbent for comparison: `date` 99.95% (2 wrong), `amount` 98.68% (**52** wrong),
`direction` 99.87% (5), `currency` 99.62% (3), `description` 99.85% (6).

### The 295 description defects decompose as

| class | count | severity |
|---|---:|---|
| spacing only | 191 | cosmetic |
| dropped trailing country code (`IN`) | 92 | cosmetic |
| **real character difference** | **12** | genuine |

Only **12 of 4,097 rows** carry a real narration character difference, and those are
largely casing (`fuel Surcharge` vs `Fuel Surcharge`, `MAKE MY TRIP` vs `Make My
Trip`). This was independently re-derived by a second method that agreed on 12.
**This is a prompt-level normalisation fix, not a model capability gap** — but it is a
real gap and should not be waved away.

## 2. Read this before quoting any number

The earlier headline said **transaction micro-F1 = 1.0000**. That number was
**withdrawn** — it measured *row admission*, not correctness:

`micro_precision = rows_matched / rows_pred`, `micro_recall = rows_matched / rows_ref`,
where matching admits a pair on **description similarity alone** (≥0.60, strict 1:1),
deliberately excluding `date`/`amount`/`direction`/`currency` so those can be scored
non-tautologically afterwards. ICICI narrations transcribe near-perfectly (mean
similarity **0.9992**), so every row finds a twin and all four pairing ratios saturate
at 1.0 **by construction**. The per-field verdicts that *do* measure correctness were
always being computed and were never 1.0 — they simply were not the number being read.

In `final_scores.json` alignment figures are now namespaced
`row_alignment__NOT_A_CORRECTNESS_CLAIM` and carry an explicit reading rule. Row
recovery remains a real (and favourable) result: **Luna pairs 4,097/4,097 reference
rows; the incumbent 3,932/4,097, losing 165** — just don't call it accuracy.

## 3. Which 1.0s are real

Of twelve statement fields reading 1.0, **eight are earned** and **four are artifacts**:

- **Earned** — the four money fields (120–296 distinct reference values each) and four
  `statementMeta` dates (byte-for-byte over 138/139 distinct values). That 72–285 of
  304 agree only *after* numeric normalisation is itself evidence the two systems are
  independent.
- **By construction** — `issuerName` (one value corpus-wide); `utilisationPercent`
  *derived* (arithmetic on two fields that already agree; as-extracted is
  `both_null` 304/304); `rewards.openingPoints` / `closingPoints` (1 of 304 scorable).
- `currency` at 100% is likewise **by construction** — a single distinct reference
  value across the corpus, solved by emitting a constant. **Non-differentiating.**

Self-comparison was ruled out directly: **0 of 304** artifact files are byte-identical,
and `rawStatementId` (null 304/304), `programType` (303 wrong) and `txnType` (484
wrong) disagree loudly.

## 4. Card identity and fabrication

| metric | Luna | incumbent |
|---|---:|---:|
| `network` fabrications vs PDF | **0** | **72** |
| `lastFourDigit` accuracy | 95.79% | — |
| `cardDisplayName` accuracy | 91.86% | — |

The incumbent inventing a card `network` on **72** statements is a material data-quality
finding: the generic rule set invites fabrication where the PDF states no network.
Luna fabricates none. Luna's own weakness is `lastFourDigit` (95.79%).

## 5. Corpus and tokens

304 PDFs / 304 Luna / 304 GT / 304 joined CSV rows — closed at 304/304/304. The PDF
**filename** is the join key; the upstream numeric-id regex would have silently dropped
4 statements whose token is non-numeric (`decrypt_gmail:...`), so the id scheme was
widened. Dropping 4 statements to keep a regex is a measurement error, not a shortcut.

| arm | calls | input | output | total |
|---|---:|---:|---:|---:|
| Luna (refined) | 304 | 4,259,358 | 413,267 | 4,672,625 |
| Opus-5 GT | 304 | 7,551,311 | 571,289 | 8,122,600 |

Luna output includes 123,818 reasoning tokens. **Opus-5 GT cost $52.04.** No Luna
price is published — token counts only, **no Luna dollar figure should be inferred.**
No record hit the 32,000 output cap.

## 6. Corrections to the earlier report

| claim | stale | corrected |
|---|---|---|
| transaction headline | micro-F1 **1.0000** as correctness | **withdrawn**; alignment 1.0 by construction, correctness 92.70% strict / 99.61% forgiven |
| held-out rows | 3,961 / 3,802 | **3,544 / 3,379** |
| adjudication | 347 CSV_WRONG / 25 LUNA_WRONG | **349 / 23** |
| Luna:incumbent win ratio | "14:1" | **15.2:1** |

The held-out figure was stale because the 10 tuning statements hold **553** GT rows
(two alone hold 150 and 140), so the correct arithmetic is 4,097−553 and 3,932−553; the
prose's 3,961 implies only 136 rows for those 10, and its 3,802 was a cell copied from
the wrong table row. The adjudication moved by exactly two items — statements
**647130** and **870931682** (`cards[].cardMeta.network`) flipped LUNA_WRONG →
CSV_WRONG after the adjudicator was hardened, so 25−2=23 and 347+2=349.

## 7. Limitations

- A standing instrument caveat applies to the GT: where the GT prompt and Luna's own
  prompt ask for different things, a measured "Luna error" may be a prompt gap rather
  than a model defect.
- `ICICI_REPORT.md` is **superseded** by this document. It retains value as
  qualitative narrative (the co-brand trap that did not reproduce, the fabrication trap
  the generic rules create, statement `238910814` as a total incumbent failure), but
  **its numbers — including the withdrawn 1.0 headline — must not be quoted.**
