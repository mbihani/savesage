# SBI Card — Luna 5.6 native-PDF extraction evaluation (FINAL)

**Run status: COMPLETE — 300/300 statements on every arm.**

Every number here is recomputed from `sbi/final_scores.json`. Where this document and
`SBI_REPORT.md` / `REPORT_TABLES.md` / `GLARING_MISSES.md` disagree, **this document is
correct** — see §6. `test_rollup_honesty.py` (7 obligations) and the SBI matcher test
both pass against the published artifact.

| role | system | instrument |
|---|---|---|
| **Challenger** | `databricks-gpt-5-6-luna`, native PDF | refined SBI prompt |
| **Reference ("GT")** | `databricks-claude-opus-5`, native PDF | `GT_PROMPT` + `GT_SCHEMA`, unchanged |
| **Incumbent** | the client's existing parser | its output as delivered in the SBI CSV |

Excluded arms: `run_luna_client` (partial, cancelled) and `run_luna_generic`
(10-record generic arm).

---

## 1. Headline

Rows correct on **all five** priority fields, over all 3,527 reference rows:

| reading | Luna | incumbent CSV | leader |
|---|---:|---:|---|
| **Strict** | **95.01%** | **94.64%** | Luna |
| **Narration-fidelity forgiven** | **95.38%** | **95.01%** | Luna |

**Luna leads under both readings — the SBI verdict does not flip.** (Contrast ICICI,
where it does.) The margin is modest, ~0.4pp.

Per-field, Luna vs GT:

| field | accuracy | wrong_value | format_only (not charged) |
|---|---:|---:|---:|
| `date` | 97.87% | 0 | 0 |
| `amount` | 100.00% | 0 | 2,255 (63.94%) |
| `direction` | 99.80% | 7 | 0 |
| `currency` | 100.00% (by construction) | 0 | 0 |
| `description` | 98.78% | 43 | 909 |

Incumbent: `date` 96.84% (0 wrong), `amount` 99.80% (7), `direction` 99.86% (5),
`currency` 98.87% (**9**), `description` 99.60% (14).

Luna's `description` defects split **30 real character differences** + 13 added
trailing country codes. Its `currency` advantage is real (0 vs 9 incumbent errors),
though see the by-construction caveat below.

## 2. Read this before quoting any number

The earlier reports presented **P/R/F1 = 100%** as "exact transaction extraction". That
is **row alignment only**. Pairing admits on description similarity alone, so 3,527
clean narrations guarantee admission **without proving a single value correct**; mean
admitted-pair similarity is **0.9992**, so the ratios saturate by construction. Luna
recovers 3,527/3,527 reference rows — a real result, but **not** accuracy.

**`amount` at 100.00% is EARNED but only after normalisation.** 2,255 of 3,527 cells
(**63.94%**) agree only once serialisation is normalised (`180` vs `180.0`); the
incumbent's share is 65.30%. Zero *value* disagreements over **2,105 distinct reference
values** — verified independently: restricted to 1,569 unambiguously-paired rows, true
numeric mismatches were **0**. So the 100% is real, but it must never be quoted as raw
string agreement.

**`currency` at 100.00% is BY CONSTRUCTION** — the reference has one distinct value
corpus-wide, so it is solved by emitting a constant. **Non-differentiating; do not read
it as extraction skill.** Genuinely earned 1.0s: `amount` (2,105 distinct values), total
amount due (247), minimum amount due (178), raw statement ID (300), statement date
(188), period end (177), period start (180).

Self-comparison was ruled out: **0 of 300** artifact pairs are byte-identical.

## 3. The reference instrument is itself imperfect

Read this before treating any GT-relative number as absolute. Documented GT defects
(detailed in `SBI_REPORT.md` §2) mean some measured "Luna errors" are reference errors:
`meta.dueDate`, `txn.date` (71 of 74 apparent hallucinations were GT defects), and
`meta.issuerName` (a naming convention, not an error). The `date` figure of 97.87% with
**0 wrong_value** reflects this — the shortfall is null/population disagreement against
a defective reference, not wrong values.

## 4. Adjudication and tokens

PDF adjudication of disagreements is in `final_scores.json` under `pdf_adjudication`
with per-item evidence; `GLARING_MISSES.md` enumerates them qualitatively.

| arm | calls | input | output | total |
|---|---:|---:|---:|---:|
| Luna (refined) | 300 | 5,585,261 | 387,419 | 5,972,680 |

Luna output includes 134,705 reasoning tokens; 272,640 cached input tokens were
observed. `prompt + completion == total` holds on 300/300 records, all finish reasons
`stop`, and no record hit the 64,000 output cap. No Luna price is published — **token
counts only, no dollar figure.**

## 5. Findings-cap audit

SBI was checked for the truncation defect found in HDFC (a per-field findings cap while
counts incremented before it). **Absent** — no capped adjudication list and no
count/list divergence.

## 6. Corrections to the earlier SBI reports

| location | stale claim | corrected |
|---|---|---|
| `SBI_REPORT.md` 41-43, 241-252, 575-576; `REPORT_TABLES.md` 154-160 | P/R/F1 100% as "exact transaction extraction" | **row alignment only**; add value correctness Luna 95.01% vs 94.64% strict, 95.38% vs 95.01% forgiven |
| `SBI_REPORT.md` 252 | "not a single transaction row missed or invented" | scope to **Luna-vs-GT alignment**; the incumbent has one extra row, and alignment never proves values correct |
| `SBI_REPORT.md` 176-182; `REPORT_TABLES.md` 32-35 | per-field percentages omit the serialisation split | add `format_only`: `amount` 2,255/3,527 (63.94%) Luna, 2,303/3,527 (65.30%) incumbent; mark `currency` 100% **by construction** and `amount` 100% **earned after normalisation** |

## 7. Limitations

- The GT is a strong reference, not an oracle (§3).
- `SBI_REPORT.md`, `REPORT_TABLES.md` and `GLARING_MISSES.md` retain their qualitative
  analysis and per-item evidence, but **their transaction headline numbers are
  superseded by this document.**
