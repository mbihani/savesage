# HDFC — Luna 5.6 native-PDF extraction evaluation (FINAL)

**Run status: COMPLETE — 281/281 statements scored on every arm.**

Every number here is recomputed from `hdfc/final_scores.json`, the machine-readable
source of truth. Where this document and the older `HDFC_REPORT.md` /
`REPORT_TABLES.md` / `NOTES_verified_findings.md` disagree, **this document is
correct** — those three were written against a partial run (GT 154/281, Luna 131/281)
and against a truncated adjudication. See §6.

| role | system | instrument |
|---|---|---|
| **Challenger** | `databricks-gpt-5-6-luna`, native PDF | refined `HDFC_PROMPT.txt` |
| **Reference ("GT")** | `databricks-claude-opus-5`, native PDF | `gt298_lib.GT_PROMPT` + `GT_SCHEMA`, unchanged |
| **Incumbent** | the client's existing **Gemini** parser | its output as delivered in `hdfc.csv` |

> **The incumbent CSV is NOT ground truth.** It is one more system under test.
> Luna-vs-Opus is reported as ACCURACY; Luna-vs-CSV as AGREEMENT. Every Luna-vs-CSV
> disagreement is adjudicated against the PDF itself with PyMuPDF coordinate evidence
> into LUNA_WRONG / CSV_WRONG / BOTH_WRONG / AMBIGUOUS_IN_PDF. Opus is a strong
> reference, not an oracle — where it and the CSV disagree, the PDF decides.

---

## 1. Headline

**Transaction value correctness (micro, over all scored cells):**

| arm | accuracy | correct / n |
|---|---:|---|
| **Luna vs GT** | **98.69%** | 22,886 / 23,190 |
| Incumbent CSV vs GT | 97.77% | 22,667 / 23,185 |
| Luna vs CSV (agreement, not correctness) | 97.22% | 22,535 / 23,180 |

**Luna leads the incumbent on HDFC by ~0.9pp of transaction value correctness.**
Unlike ICICI, the HDFC verdict does not flip under any reading.

Per-field, Luna vs GT:

| field | accuracy | wrong_value |
|---|---:|---:|
| `date` | 100.00% | 0 |
| `amount` | 99.66% | 16 |
| `direction` | 99.01% | 46 |
| `currency` | 99.03% | 45 |
| `description` | 95.75% | 197 |

Luna's only material weakness is `description` fidelity. `date` is genuinely
byte-perfect across all 4,638 scored cells.

## 2. Read this before quoting any number

Two classes of metric are reported and **they must not be conflated**:

- **ROW ALIGNMENT** — whether a Luna row found its GT twin. Pairing is on
  **description similarity only** (threshold 0.55, strict 1:1, order-insensitive), so
  `date`, `amount`, `direction` and `currency` never enter the matcher. Alignment F1
  is 99.98% (Luna vs GT). **This is a pairing figure, NOT a correctness claim** — on a
  corpus with clean narrations it saturates by construction.
- **VALUE CORRECTNESS** — whether the paired values agree, using the scorer's
  canonical normalisation. These are the numbers in §1.

Formatting differences (e.g. `180` vs `180.0`) are classified `format_only` and are
**not** charged as errors. This matters: on sibling corpora up to 64% of raw `amount`
string mismatches were pure serialisation.

## 3. Corpus and join — closed accounting

| quantity | value |
|---|---:|
| directory entries | 282 |
| PDFs on disk | 281 |
| `failed-download-links.txt` entries | 19 |
| CSV rows | 300 |
| joined (scoreable) | 281 |
| CSV rows unmatched | 19 |
| PDFs without a CSV row | 0 |

281 + 19 = 300 exactly, and the 19 unmatched CSV rows were proven **set-equal** to
`failed-download-links.txt` — not merely equal in count. 92 filenames use uppercase
`.PDF`, 10 contain spaces; the PDF **filename** (not a parsed id) is the join key.
`detectionSource` is `GEMINI` for all 300 rows.

## 4. PDF adjudication of Luna-vs-CSV disagreements

Transaction-level, complete (no truncation):

| verdict | count |
|---|---:|
| CSV_WRONG (incumbent wrong, Luna right) | 394 |
| LUNA_WRONG | 136 |
| AMBIGUOUS_IN_PDF (counted against neither) | 108 |
| BOTH_WRONG | 7 |

Where the PDF can decide, **Luna is right ~74% of the time**. Glaring-miss extraction
yields **420 incumbent** vs **148 Luna** substantive errors. Three statements have
rows Luna found that the CSV lacks; two the reverse.

## 5. Token usage and cost

| arm | calls | input | output | total |
|---|---:|---:|---:|---:|
| Luna (refined) | 281 | 2,530,225 | 464,737 | 2,994,962 |
| Opus-5 GT | 281 | 3,537,611 | 601,505 | 4,139,116 |

Luna's 464,737 output tokens **include** 150,562 reasoning tokens (32.4% of output;
reasoning is nested inside completion, verified on all 281 calls). Luna uses **1.40x
fewer input** and **1.29x fewer output** tokens than the GT.

**Opus-5 GT cost: $17.69 input + $15.04 output = $32.73 total, $0.1165 per statement**
at the published $5.00/M input, $25.00/M output. No Luna price is published, so Luna
cost is reported as token counts only — **do not infer a Luna dollar figure.**

No record hit the output cap (max completion observed 21,356 against a 64,000 cap;
33.3% headroom against the lowest cap in play), and all finish reasons are `stop`.

## 6. Corrections to the earlier HDFC reports

A findings cap in `adjudicate_txn.py` (`< 120` per field) truncated the adjudication
**while the counts dict was incremented before the cap** — so totals and the evidence
list disagreed, and every artifact derived from the list undercounted.

| claim | stale value | corrected |
|---|---|---|
| coverage | GT 154/281, Luna 131/281 | **281/281 both** |
| description disagreements | 120 | **385** (+265) |
| description CSV_WRONG | 88 | **273** (+185) |
| description LUNA_WRONG | 31 | **110** (+79) |
| transaction `overall` | CSV_WRONG 116, LUNA_WRONG 49, ambig 101, both 1 | **CSV_WRONG 394, LUNA_WRONG 136, ambig 108, both 7** |
| glaring-miss totals | 53 Luna / 128 incumbent | **148 Luna / 420 incumbent** |

Note the fix **also increased Luna's error count** (+79 LUNA_WRONG) — it was not a
correction that happened to flatter the challenger.

`adjudicate.py` (statement-level) was checked and is **clean**: its `[:limit]` bounds
duplicate rectangle-search results per value, not findings or totals.

## 7. Limitations

- The GT is a strong reference, not an oracle. Where the GT prompt and Luna's own
  prompt differ in what they *ask for*, a measured "Luna error" may be a prompt gap.
  `corrected_score.py` reports two such corrections (CSV_WRONG cells not charged to
  Luna; an FX rupee-vs-foreign-leg asymmetry) as **separately rejectable** items with
  their own deltas — deliberately not folded into the headline. On the FX rows Luna is
  **still wrong for the client**, who wants the billed rupee figure; that is a
  one-line prompt fix, unverified until a sweep runs with it.
- `HDFC_REPORT.md`, `REPORT_TABLES.md` and `NOTES_verified_findings.md` are retained
  for their qualitative analysis (the `"C"`-is-a-rupee-sign finding, prompt-refinement
  narrative, truncation audit) but **their numbers are superseded by this document.**
