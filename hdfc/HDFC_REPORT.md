# HDFC — Luna 5.6 native-PDF extraction evaluation

**Run status: PARTIAL — GT 154/281 statements, challenger Luna 131/281**

Three systems over one corpus of HDFC Bank credit-card statement PDFs:

| role | system | instrument |
|---|---|---|
| **Challenger** | `databricks-gpt-5-6-luna`, native PDF | refined `HDFC_PROMPT.txt` |
| **Reference ("GT")** | `databricks-claude-opus-5`, native PDF | `gt298_lib.GT_PROMPT` + `GT_SCHEMA`, **unchanged** |
| **Incumbent** | the client's existing **Gemini** parser | its output as delivered in `hdfc.csv` |

> **The incumbent CSV is NOT ground truth.** It is one more system under test.
> Luna-vs-Opus is therefore reported as **ACCURACY**; Luna-vs-CSV as **AGREEMENT**,
> and every Luna-vs-CSV disagreement is adjudicated against the PDF itself with
> PyMuPDF coordinate evidence into LUNA_WRONG / CSV_WRONG / BOTH_WRONG /
> AMBIGUOUS_IN_PDF. Opus is a strong reference, not an oracle — where it and the
> CSV disagree the PDF decides.

## 1. Corpus and join

| quantity | measured |
|---|---:|
| PDFs on disk | 281 |
| CSV data rows | 300 |
| **joined, scoreable** | **281** |
| CSV rows that do not join | 19 |
| PDFs with no CSV row | 0 |

join reaches 281/300 once the CSV link basename is URL-DECODED; the 19 non-joining CSV rows are exactly the 19 entries of failed-download-links.txt (PDFs never downloaded).

**Correction to the brief.** The brief specified a scoreable set of **271**
statements. The measured intersection is **281**. The difference is
URL-decoding: 10 HDFC PDFs are stored on disk with literal spaces while the CSV
`link` column percent-encodes them (`%20`), so a raw-basename join drops them.
`hdfc_lib.join_key()` unquotes before matching. The 19 CSV rows that still do
not join are byte-identical to the entries of `failed-download-links.txt` — PDFs
that were never downloaded. That is a collection gap, not a join defect.

**Correction to the brief — transaction density.** The brief stated SBI was the
densest corpus. Measured from the CSVs, **HDFC is the densest of the three**
(16.19 txn/statement mean, max 223), which makes output truncation this
evaluation's primary technical risk. It is audited in §6.

## 2. Phase 1 — the client's production prompt, as-is

Baseline = the inner string of `SYSTEM_PROMPT` in
`/Users/mayanck.bihani/Savesage/SYSTEM PROMPT.txt` (the client's **own**
production prompt), extracted by AST parse. Schema unchanged.

### 2.1 The headline finding: `"C"` is the rupee sign, not a credit marker

The client's production prompt contains:

> `- If in the transaction amount have a "+" or "Cr" or "C" or "CREDIT" symbols, set direction to "CREDIT".`

The `"C"` clause is **factually wrong on HDFC statements.** These PDFs embed a
font literally named **`ITFRupee` / `ITFRupee,Bold`** in which the rupee sign ₹
sits at code point `0x43` — ASCII capital `C`. Every rupee amount therefore
extracts with a leading `C`: `C13,507.00` **is** ₹13,507.00.

| evidence | measurement |
|---|---|
| Font identified | PyMuPDF span dump p1: `font=ITFRupee,Bold size=15.0 raw='C' cp=['0x43']` |
| Self-refuting on its own face | `TOTAL AMOUNT DUE` → `C13,507.00`, `MINIMUM DUE` → `C680.00` — a total due is not a credit |
| Corpus scope | **179 / 281 PDFs (63.7%)** embed the Rupee font, and in exactly those 179 a bare `C` prefixes amounts — correlation is perfect |
| Worst single statement | `decrypt_738368244_19f70d2e2c77ebd5_6530XXXXXXXXXX38_16-07-2026_366`: **107 of 109 rows** flipped to `CREDIT`, including every `UPI-<person>` purchase |
| Who was right | The **incumbent CSV was correct** here (2 CREDIT rows, exactly the two the PDF prints with `+`). The client's own prompt rule caused Luna's error. |

The real HDFC credit markers are a leading `+` (`+ C 2,600.00`) or a trailing
`Cr`/`CR`. Nothing else. **This is the single highest-value finding of the
evaluation: a rule in the client's live prompt is corrupting `direction` on ~64%
of their HDFC corpus, independent of which model runs it.**

### 2.2 Other Phase 1 defects

| # | defect | statement | who was right |
|---|---|---|---|
| C5 | `lastFourDigit` = `"XX69"` where the card prints `442144-xxxxxx-6969` — real digits over-masked | `decrypt_310396339…` | incumbent (`6969`) |
| C3 | 26 descriptions carried an invented `EMI ` prefix (`EMI` is on its own line in the PDF) | `decrypt_705330814…` | incumbent |
| C7 | `network` fabricated as `"Mastercard"` by BIN inference; no network word anywhere in the PDF | `decrypt_810097123…` | incumbent (null) |
| C6 | `cardDisplayName` = `"SALMAN KHAN S"` — the **cardholder's name** | `decrypt_738368244…` | **Luna** (`UPI RuPay Credit Card`) |
| C9 | `Ref# (…)` truncated out of printed narration | `decrypt_493517787…` (4 rows), `decrypt_923692554…` (6 rows) | **Luna** keeps it |
| C9 | Broken intra-word spacing silently de-spaced (`"S ENDEAVOUR"` → `"SENDEAVOUR"`) | `decrypt_310396339…` | **Luna** preserves verbatim |

### 2.3 Fields the baseline prompt never mentions

Confirmed **0 occurrences** in the client prompt of `network`, `issuerName`,
`totalMinimumAmountDue`, `txnType`. Per the brief, a baseline miss on these is
**not** a model capability failure — the prompt never asked. Measured base rates
that matter: a network word is printed on only **82/281** statements (RuPay 33,
Diners 27, Visa 25, Mastercard 7, Amex 0), so **null is correct on 199/281**; and
**0 of 281** PDFs print a utilisation figure, making `utilisationPercent` always
arithmetic rather than extraction.

Dead weight: the baseline carries rules for **7 other banks** (ICICI, IndusInd, AU,
Standard Chartered, IDFC First, SBI, RBL) = 8 lines / 752 bytes / **7.45%** of the
prompt. Removed. Notably RBL's rule sets direction by **amount colour**, a
non-textual cue that directly competes with the rupee-glyph correction.

## 3. Phase 2 — prompt refinement

Full per-change detail, each tied to an observed defect, is in
**`PROMPT_CHANGELOG.md`**. Three measured iterations were kept rather than
collapsed, because two of them **regressed** and the regressions are
informative:

| iteration | dir | outcome |
|---|---|---|
| v1 | `phase2_refined_v1/` | fixed direction (108→0) but **introduced 3 regressions**: lakh digit eaten (`C1,94,022` → `94022`), 4 real rows dropped by an over-broad EMI rule, 6 descriptions lost a leading `PM ` |
| v2 | `phase2_refined_v2/` | all three v1 regressions cleared (C2/C3/C4) |
| v3 (final) | `phase2_refined/` | `network` BIN-inference ban strengthened after v2 still fabricated MASTERCARD on 2 statements |

The v1 regressions are the reason each iteration was measured on the PDFs
rather than reasoned about: fixing the glyph rule naively **broke** amount
parsing and dropped real transaction rows.

Refined prompt: `HDFC_PROMPT.txt`. Tuned on **10** statements, tested on
**281** — a ~28× extrapolation, which is why every metric below is
reported twice: all-statements **and** held-out.

<details><summary>The 10 tuning statement ids (excluded from held-out)</summary>

- `decrypt_1196489569_19d518a09371644e_HDFC_Bank_Pixel_Play_Credit_Card_Statement_03Mar2026_to_02Apr2026`
- `decrypt_1678401256_19aa6bdd1e9f899e_5522XXXXXXXXXX40_20_11_2025_174`
- `decrypt_252502266_19bc220c2d3c07ef_4341XXXXXXXXXX35_14_01_2026_31`
- `decrypt_310396339_19e30dcf27dc7f73_HDFC_Bank_Pixel_Play_Credit_Card_Statement_15Apr2026_to_14May2026`
- `decrypt_493517787_19c1fc592bdf84d1_6529XXXXXXXXXX09_01_02_2026_701`
- `decrypt_705330814_19c81ac46a73163b_0036XXXXXXXXXX87_20_02_2026_641`
- `decrypt_738368244_19f70d2e2c77ebd5_6530XXXXXXXXXX38_16_07_2026_366`
- `decrypt_810097123_19caf4a3c2c34de6_6529XXXXXXXXXX89_01_03_2026_81`
- `decrypt_879728587_18a0b7c020b06bf5_3611XXXXXXXX51_18_08_2023`
- `decrypt_923692554_19802d1d11c3ef78_4375XXXXXXXXXX40_12_07_2025`

</details>

## 4. Phase 3 — field-by-field results

> ⚠️ **PARTIAL RESULTS.** GT covers 154/281 statements and the
> challenger 131/281. Numbers below are honest for the subset
> actually scored and every table states its n. They are **not** the
> full-corpus figures.

### 4.1 Statement-level fields — ALL statements

**vs Opus-5 GT (ACCURACY), all statements** — Luna n=131, incumbent CSV n=154

| field | Luna acc | Luna wrong / null / halluc | CSV acc | CSV wrong / null / halluc | note |
|---|---:|---|---:|---|---|
| `cardDisplayName` | 96.2% | 5 / 0 / 0 | 94.2% | 9 / 0 / 0 |  |
| `lastFourDigit` | 100.0% | 0 / 0 / 0 | 100.0% | 0 / 0 / 0 | NON-DISCRIMINATING — 100% both sides |
| `network` | 97.7% | 0 / 2 / 1 | 91.6% | 0 / 2 / 11 |  |
| `statementLevelSummary.totalAmountDue` | 100.0% | 0 / 0 / 0 | 100.0% | 0 / 0 / 0 | NON-DISCRIMINATING — 100% both sides |
| `statementLevelSummary.totalMinimumAmountDue` | 100.0% | 0 / 0 / 0 | 100.0% | 0 / 0 / 0 | NON-DISCRIMINATING — 100% both sides |
| `statementLevelSummary.totalCreditLimit` | 100.0% | 0 / 0 / 0 | 100.0% | 0 / 0 / 0 | NON-DISCRIMINATING — 100% both sides |
| `statementLevelSummary.availableCreditLimit` | 100.0% | 0 / 0 / 0 | 100.0% | 0 / 0 / 0 | NON-DISCRIMINATING — 100% both sides |
| `statementLevelSummary.utilisationPercent` | 100.0% | 0 / 0 / 0 | 51.3% | 0 / 0 / 75 | gold null on all n — this field measures HALLUCINATION only (75 invented) |
| `statementLevelSummary.utilisationPercent_DERIVED` | 100.0% | 0 / 0 / 0 | 100.0% | 0 / 0 / 0 | NON-DISCRIMINATING — 100% both sides |
| `statementMeta.issuerName` | 100.0% | 0 / 0 / 0 | 100.0% | 0 / 0 / 0 | NON-DISCRIMINATING — 100% both sides |
| `statementMeta.statementDate` | 100.0% | 0 / 0 / 0 | 100.0% | 0 / 0 / 0 | NON-DISCRIMINATING — 100% both sides |
| `statementMeta.dueDate` | 100.0% | 0 / 0 / 0 | 100.0% | 0 / 0 / 0 | NON-DISCRIMINATING — 100% both sides |

### 4.2 Statement-level fields — HELD-OUT only (tuning statements excluded)

**vs Opus-5 GT (ACCURACY), held-out** — Luna n=127, incumbent CSV n=150

| field | Luna acc | Luna wrong / null / halluc | CSV acc | CSV wrong / null / halluc | note |
|---|---:|---|---:|---|---|
| `cardDisplayName` | 96.1% | 5 / 0 / 0 | 94.0% | 9 / 0 / 0 |  |
| `lastFourDigit` | 100.0% | 0 / 0 / 0 | 100.0% | 0 / 0 / 0 | NON-DISCRIMINATING — 100% both sides |
| `network` | 97.6% | 0 / 2 / 1 | 91.3% | 0 / 2 / 11 |  |
| `statementLevelSummary.totalAmountDue` | 100.0% | 0 / 0 / 0 | 100.0% | 0 / 0 / 0 | NON-DISCRIMINATING — 100% both sides |
| `statementLevelSummary.totalMinimumAmountDue` | 100.0% | 0 / 0 / 0 | 100.0% | 0 / 0 / 0 | NON-DISCRIMINATING — 100% both sides |
| `statementLevelSummary.totalCreditLimit` | 100.0% | 0 / 0 / 0 | 100.0% | 0 / 0 / 0 | NON-DISCRIMINATING — 100% both sides |
| `statementLevelSummary.availableCreditLimit` | 100.0% | 0 / 0 / 0 | 100.0% | 0 / 0 / 0 | NON-DISCRIMINATING — 100% both sides |
| `statementLevelSummary.utilisationPercent` | 100.0% | 0 / 0 / 0 | 51.3% | 0 / 0 / 73 | gold null on all n — this field measures HALLUCINATION only (73 invented) |
| `statementLevelSummary.utilisationPercent_DERIVED` | 100.0% | 0 / 0 / 0 | 100.0% | 0 / 0 / 0 | NON-DISCRIMINATING — 100% both sides |
| `statementMeta.issuerName` | 100.0% | 0 / 0 / 0 | 100.0% | 0 / 0 / 0 | NON-DISCRIMINATING — 100% both sides |
| `statementMeta.statementDate` | 100.0% | 0 / 0 / 0 | 100.0% | 0 / 0 / 0 | NON-DISCRIMINATING — 100% both sides |
| `statementMeta.dueDate` | 100.0% | 0 / 0 / 0 | 100.0% | 0 / 0 / 0 | NON-DISCRIMINATING — 100% both sides |

### 4.3 Transaction fields — ALL statements

**vs Opus-5 GT (ACCURACY), all statements**

| txn field | Luna acc | Luna rows | Luna wrong / null / halluc | CSV acc | CSV rows | CSV wrong / null / halluc | note |
|---|---:|---:|---|---:|---:|---|---|
| `date` | 100.0% | 2,165 | 0 / 0 / 0 | 100.0% | 2,346 | 0 / 0 / 0 | NON-DISCRIMINATING — 100% both sides |
| `description` | 95.4% | 2,165 | 100 / 0 / 0 | 93.3% | 2,346 | 157 / 0 / 0 |  |
| `amount` | 99.4% | 2,165 | 14 / 0 / 0 | 99.0% | 2,346 | 24 / 0 / 0 |  |
| `direction` | 98.5% | 2,165 | 32 / 0 / 0 | 99.7% | 2,346 | 6 / 0 / 0 |  |
| `currency` | 98.7% | 2,165 | 28 / 0 / 0 | 97.9% | 2,346 | 7 / 43 / 0 |  |

### 4.4 Transaction fields — HELD-OUT only

**vs Opus-5 GT (ACCURACY), held-out**

| txn field | Luna acc | Luna rows | Luna wrong / null / halluc | CSV acc | CSV rows | CSV wrong / null / halluc | note |
|---|---:|---:|---|---:|---:|---|---|
| `date` | 100.0% | 1,946 | 0 / 0 / 0 | 100.0% | 2,127 | 0 / 0 / 0 | NON-DISCRIMINATING — 100% both sides |
| `description` | 97.3% | 1,946 | 52 / 0 / 0 | 94.3% | 2,127 | 121 / 0 / 0 |  |
| `amount` | 99.3% | 1,946 | 14 / 0 / 0 | 98.9% | 2,127 | 23 / 0 / 0 |  |
| `direction` | 98.4% | 1,946 | 32 / 0 / 0 | 99.7% | 2,127 | 6 / 0 / 0 |  |
| `currency` | 98.6% | 1,946 | 28 / 0 / 0 | 97.7% | 2,127 | 7 / 43 / 0 |  |

### 4.5 Transaction matching — precision / recall / F1 + description fidelity

Pairing is on **description similarity only**, 1:1 enforced. `date`, `amount`,
`direction` and `currency` never enter the matcher, so their per-field accuracies
above are real measurements and not artefacts of the pairing.

| comparison | n stmts | matched pairs | pred-only (FP) | gold-only (FN) | precision | recall | F1 | desc exact | desc mean sim |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Luna vs GT — all (ACCURACY) | 131 | 2,165 | 1 | 0 | 99.95% | 100.00% | 99.98% | 95.38% | 99.73% |
| Luna vs GT — held-out (ACCURACY) | 127 | 1,946 | 0 | 0 | 100.00% | 100.00% | 100.00% | 97.33% | 99.79% |
| Incumbent CSV vs GT — all (ACCURACY) | 154 | 2,346 | 1 | 2 | 99.96% | 99.91% | 99.94% | 93.31% | 99.04% |
| Incumbent CSV vs GT — held-out (ACCURACY) | 150 | 2,127 | 1 | 2 | 99.95% | 99.91% | 99.93% | 94.31% | 99.00% |
| Luna vs CSV — all (AGREEMENT) | 131 | 2,163 | 3 | 1 | 99.86% | 99.95% | 99.91% | 93.30% | 99.11% |
| Luna vs CSV — held-out (AGREEMENT) | 127 | 1,944 | 2 | 1 | 99.90% | 99.95% | 99.92% | 93.31% | 99.03% |

## 5. Adjudication of Luna-vs-CSV disagreements against the PDF

Every disagreement is decided by the PDF, not by assuming either side is right.

**Statement-level** — {"AMBIGUOUS_IN_PDF": 6, "BOTH_WRONG": 1, "CSV_WRONG": 12, "LUNA_WRONG": 4}

| field | n | LUNA_WRONG | CSV_WRONG | BOTH_WRONG | AMBIGUOUS_IN_PDF | Luna right where PDF decides |
|---|---:|---:|---:|---:|---:|---:|
| `network` | 12 | 1 | 11 | 0 | 0 | 91.7% |
| `cardDisplayName` | 11 | 3 | 1 | 1 | 6 | 25.0% |

**Transaction-level** — {"AMBIGUOUS_IN_PDF": 101, "BOTH_WRONG": 1, "CSV_WRONG": 116, "LUNA_WRONG": 49}

| field | n | LUNA_WRONG | CSV_WRONG | BOTH_WRONG | AMBIGUOUS_IN_PDF | Luna right where PDF decides |
|---|---:|---:|---:|---:|---:|---:|
| `description` | 120 | 31 | 88 | 1 | 0 | 73.9% |
| `currency` | 74 | 0 | 0 | 0 | 74 | — |
| `amount` | 38 | 0 | 19 | 0 | 19 | 100.0% |
| `direction` | 33 | 18 | 9 | 0 | 6 | 33.3% |
| `date` | 2 | 0 | 0 | 0 | 2 | — |

#### Luna substantive errors (PDF says Luna is wrong) — 53 total

| statement id | level | field | Luna | incumbent CSV | held-out | PDF evidence |
|---|---|---|---|---|---|---|
| `decrypt_1028274832_19fcb3b4c55f8f3b_4854XXXXXX` | statement | `cardDisplayName` | HDFC Regalia | Regalia Credit Card | True | {"csv": {"page": 1, "rect": [26.6, 53.8, 121.5, 65.8]}, "csv_supported": true, "luna": null, "luna_supported": false} |
| `decrypt_120076208_192133d7913277bc_4854XXXXXXX` | statement | `cardDisplayName` | HDFC Regalia | Visa Regalia Credit Card | True | {"csv": {"page": 1, "rect": [379.0, 10.8, 513.8, 22.7]}, "csv_supported": true, "luna": null, "luna_supported": false} |
| `decrypt_1756410838_199f656352543609_6529XXXXXX` | statement | `network` | MASTERCARD | None | True | {"csv": null, "csv_supported": false, "luna": null, "luna_supported": false} |
| `decrypt_208574457_18d76fc99a4de926_5522XXXXXXX` | statement | `cardDisplayName` | HDFC Regalia Gold | HDFC BANK REGALIA GOLD | True | {"csv": {"page": 1, "rect": [313.0, 10.8, 443.3, 22.7]}, "csv_supported": true, "luna": null, "luna_supported": false} |
| `decrypt_1007138360_19fc36498a4f10f1_6529XXXXXX` | transaction | `direction` | DEBIT | CREDIT | True | {"inferred": "CREDIT", "occurrences": 2, "window": " + C 2,470.00 l 05/07/2026| 13:4"} |
| `decrypt_1007633548_19fc42a54b364a79_6529XXXXXX` | transaction | `direction` | DEBIT | CREDIT | True | {"inferred": "CREDIT", "occurrences": 1, "window": " + C 200.00 l 12/07/2026| 13:49 "} |
| `decrypt_1028274832_19fcb3b4c55f8f3b_4854XXXXXX` | transaction | `description` | EMI PRESTIGEGHAZIABAD | PRESTIGEGHAZIABAD | True | {"csv_printed": true, "luna_printed": "FLAT"} |
| `decrypt_1044835578_19fd065a0d5b8c16_5459XXXXXX` | transaction | `description` | EMI SR FILLING STATIONKARNAL | SR FILLING STATIONKARNAL | True | {"csv_printed": true, "luna_printed": "FLAT"} |
| `decrypt_1044835578_19fd065a0d5b8c16_5459XXXXXX` | transaction | `description` | EMI SR FILLING STATIONKARNAL | SR FILLING STATIONKARNAL | True | {"csv_printed": true, "luna_printed": "FLAT"} |
| `decrypt_1077681910_19f6bb004730168a_5372XXXXXX` | transaction | `description` | ANTHROPIC* CLAUDE SUBSAN FRANCISCO | ANTHROPIC* CLAUDE SUBSAN FRANCISC | True | {"csv_printed": true, "luna_printed": false} |
| `decrypt_1084375771_19d19ede9671b70a_6529XXXXXX` | transaction | `description` | UPI-Star Wine | UPI-Star Wine 188 | True | {"csv_printed": true, "luna_printed": true} |
| `decrypt_1200743427_19c7baa31fef379c_0036XXXXXX` | transaction | `direction` | DEBIT | CREDIT | True | {"inferred": "CREDIT", "occurrences": 10, "window": " + C 238.00 l 03/02/2026| 12:40 "} |
| `decrypt_1200743427_19c7baa31fef379c_0036XXXXXX` | transaction | `direction` | DEBIT | CREDIT | True | {"inferred": "CREDIT", "occurrences": 12, "window": " + 10 C 436.00 l 21/01/2026| 18:"} |
| `decrypt_1314262250_19d8d7f063feebb5_6530XXXXXX` | transaction | `description` | IGST-VPS2607357151019-RATE 18.0 (Ref# 099999 | IGST-VPS2607357151019-RATE 18.0 -23 (Ref# 09 | True | {"csv_printed": true, "luna_printed": false} |

_… 39 more in `glaring_misses.json`._

#### Incumbent CSV substantive errors (PDF says the CSV is wrong) — 128 total

| statement id | level | field | Luna | incumbent CSV | held-out | PDF evidence |
|---|---|---|---|---|---|---|
| `decrypt_1036474356_19d010a851c47e93_5268XXXXXX` | statement | `network` | None | Mastercard | True | {"csv": null, "csv_supported": false, "luna": null, "luna_supported": false} |
| `decrypt_1359787929_19da4b87600e475b_5522XXXXXX` | statement | `network` | None | Mastercard | True | {"csv": null, "csv_supported": false, "luna": null, "luna_supported": false} |
| `decrypt_1403225883_19dba173e44c8703_4341XXXXXX` | statement | `network` | None | Visa | True | {"csv": null, "csv_supported": false, "luna": null, "luna_supported": false} |
| `decrypt_1467094437_19dd7998cbedc24a_4375XXXXXX` | statement | `network` | None | VISA | True | {"csv": null, "csv_supported": false, "luna": null, "luna_supported": false} |
| `decrypt_1692580129_198b2f7f14973fe3_3610XXXXXX` | statement | `cardDisplayName` | Diners Club International Credit Card | HDFC Diners Club International | True | {"csv": null, "csv_supported": false, "luna": {"page": 1, "rect": [304.0, 10.8, 509.4, 22.7]}, "luna_supported": true} |
| `decrypt_1741303904_19943aee1836e5e9_5268XXXXXX` | statement | `network` | None | Mastercard | True | {"csv": null, "csv_supported": false, "luna": null, "luna_supported": false} |
| `decrypt_2056820968_19e44b4d159aa50d_4632XXXXXX` | statement | `network` | None | Visa | True | {"csv": null, "csv_supported": false, "luna": null, "luna_supported": false} |
| `decrypt_285879906_19b1c8cf84649584_5268XXXXXXX` | statement | `network` | None | Mastercard | True | {"csv": null, "csv_supported": false, "luna": null, "luna_supported": false} |
| `decrypt_289154370_19bbd3ad4206f61a_6529XXXXXXX` | statement | `network` | None | RuPay | True | {"csv": null, "csv_supported": false, "luna": null, "luna_supported": false} |
| `decrypt_305266_20260316_064257_290_5522XXXXXXX` | statement | `network` | None | MasterCard | True | {"csv": null, "csv_supported": false, "luna": null, "luna_supported": false} |
| `decrypt_311699439_19bcae68f8d3beb7_4577XXXXXXX` | statement | `network` | None | VISA | True | {"csv": null, "csv_supported": false, "luna": null, "luna_supported": false} |
| `decrypt_378038910_19be9ff5c80e0b6a_6529XXXXXXX` | statement | `network` | None | RUPAY | True | {"csv": null, "csv_supported": false, "luna": null, "luna_supported": false} |
| `decrypt_1044835578_19fd065a0d5b8c16_5459XXXXXX` | transaction | `description` | IGST-VPS2718699250565-RATE 18.0 -06 (Ref# 09 | IGST-VPS2718699250565-RATE 18.0 -06 | True | {"csv_printed": true, "luna_printed": true} |
| `decrypt_1044835578_19fd065a0d5b8c16_5459XXXXXX` | transaction | `amount` | 28.03 | -28.03 | True | — |

_… 114 more in `glaring_misses.json`._

#### BOTH_WRONG — 2 total

| statement id | level | field | Luna | incumbent CSV | held-out | PDF evidence |
|---|---|---|---|---|---|---|
| `decrypt_1711281532_1955a014a99b930b_HDFC_Bank_` | statement | `cardDisplayName` | Pixel Play | HDFC PIXEL Card | True | {"csv": null, "csv_supported": false, "luna": null, "luna_supported": false} |
| `decrypt_1741072173_18847013d7b49f4d_4577XXXXXX` | transaction | `description` | NEFT CREDIT CARD PAYMENT AXIS BANK (Ref# 000 | NEFT CREDIT CARD PAYMENT AXIS BANK (Ref# 000 | True | {"csv_printed": false, "luna_printed": false} |

#### AMBIGUOUS_IN_PDF — 107 (counted against NEITHER side)

## 6. Truncation audit — the primary risk on this corpus

HDFC is the densest of the three corpora, so a silently truncated **reference**
record would penalise the challenger and produce a confidently wrong verdict.
Audited on two independent signals: the terminal `finish_reason`, and each
record's transaction count against the CSV's count for the same statement.

| run | records | `finish_reason` != normal stop | max completion tokens | cap | txn count < 80% of CSV |
|---|---:|---:|---:|---:|---:|
| `gt_opus` | 167 | **0** | 11,578 | 64,000 | **0** |
| `luna_generic_sample` | 10 | **0** | 11,688 | 96,000 | **0** |
| `luna_refined` | 153 | **0** | 6,367 | 96,000 | **0** |
| `luna_refined_sample` | 10 | **0** | 11,553 | 96,000 | **0** |

- GT: **0 of 167** records show an abnormal finish_reason and **0** extracted <80% of the CSV's row count. Peak completion was 11,578 tokens = 18.09% of the 64,000 cap.
- GT cap sizing: worst observed 123.3 tokens/txn on statements with >=30 txns projects ~27,494 tokens for the 223-txn outlier -- fits the 64,000 cap: True.
- GT max_tokens was raised 32,000 -> 64,000 partway through the sweep. The records collected under 32,000 remain comparable: max_tokens is a ceiling, not a sampling parameter, and none of those records came within 78% of even the lower cap, so none could have been shaped by it.
- Challenger: 0/153 abnormal finishes, 0 under-extractions, peak 6,367 tokens = 6.63% of the 96,000 cap.

## 7. Token usage

Captured **verbatim** from each response's `usage` object. No Luna dollar figures
are given anywhere in this report: Luna's price is not published, so only token
counts are reported for it.

| run | calls | input total | output total | in mean | in max | out mean | out median | out max | reasoning tok | reasoning nested in completion? | in+out==total |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| `luna_generic_sample` | 10 | 94,718 | 46,350 | 9,471.8 | 13,198 | 4,635.0 | 4,069.0 | 11,688 | 4,823 | YES | 10/10 |
| `luna_refined_sample` | 10 | 114,748 | 47,741 | 11,474.8 | 15,201 | 4,774.1 | 4,317.0 | 11,553 | 6,269 | YES | 10/10 |
| `luna_refined` | 131 | 1,190,049 | 220,757 | 9,084.3 | 12,114 | 1,685.2 | 1,340.0 | 6,367 | 72,849 | YES | 131/131 |
| `gt_opus` | 154 | 1,930,003 | 312,868 | 12,532.5 | 17,354 | 2,031.6 | 1,478.0 | 11,578 | 0 | n/a (0 reported) | 154/154 |

**Is reasoning nested inside `completion_tokens`?** Determined empirically rather
than assumed, by testing `prompt + completion == total` per call and comparing any
reported `reasoning_tokens` against `completion_tokens`:

- `gt_opus`: **no `reasoning_tokens` field returned on any of 154 calls**; `prompt + completion == total` held on 154/154. Question is moot for this run — there is no separate reasoning line item to place.
- `luna_generic_sample`: reasoning reported on 10/10 calls, total 4,823; nested inside completion: **True**.
- `luna_refined`: reasoning reported on 131/131 calls, total 72,849; nested inside completion: **True**.
- `luna_refined_sample`: reasoning reported on 10/10 calls, total 6,269; nested inside completion: **True**.

Reference-side cost, published Opus-5 rate ($5/$25 per 1M): **$17.47** for the
GT pass. Given for the reference instrument only.

## 8. Outcome tally

Infrastructure failures are held strictly apart from model defects: a 429 or an
IP-ACL 403 is never recorded as "the model failed to extract".

| run | `OK` | `TRUNCATED_OUTPUT_CAP` | `TRUNCATED_BUT_PARSED` | `TRUNCATED_EMPTY` | `ESCAPED_TRANSACTIONS_STRING` | `JSON_PARSE_FAIL` | `SCHEMA_VIOLATION` | `ZERO_LENGTH_BODY` | `RATE_LIMITED` | `HTTP_4XX` | `HTTP_5XX` | `NETWORK_ERROR` | `BLOCKED_IP_ACL` | records | NOT RUN |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `gt_opus` | 154 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 154 | 127 |
| `luna_generic_sample` | 10 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 10 | 0 |
| `luna_refined` | 131 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 131 | 150 |
| `luna_refined_sample` | 10 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 10 | 0 |

## 9. UNVERIFIED / limitations

Stated explicitly so nothing above is read as broader than it is.

- **Coverage is partial.** GT 154/281, challenger Luna 131/281. Statements not yet run are counted in §8 under NOT RUN. No claim is made about them.
- **No full-corpus run of the UNMODIFIED client prompt.** The baseline was characterised on the 10-statement Phase 1 sample plus corpus-wide static/PDF measurements (e.g. the 179/281 rupee-font count). The prompt-refinement delta is therefore demonstrated per-defect and on the sample, not as a full-corpus A/B. Priority order in the brief put GT and the challenger ahead of this run.
- **Opus-5 is the reference, not an oracle.** Fields where Opus and the CSV agree but both misread the PDF would be invisible to the ACCURACY tables. This is what the §5 PDF adjudication exists to bound, and it covers Luna-vs-CSV disagreements only.
- **`cardDisplayName` is scored LENIENTLY** (containment, not equality): the value is unstable run-to-run even within the GT itself, so strict equality would measure phrasing rather than extraction.
- **`utilisationPercent` is derived, not extracted** — 0/281 PDFs print it. The `_DERIVED` row recomputes it from each side's own totals so the comparison is like-for-like; the raw row mostly measures who volunteered a number.
- **Transaction pairing is a heuristic.** Description-only similarity at threshold 0.55 with a positional tie-break for HDFC's heavily repeated narrations. Rows whose descriptions diverge beyond that threshold are counted as FP+FN rather than as a paired field error.
- **Duplicate narrations are a measured hard limit of description-only pairing.** HDFC repeats identical descriptions heavily: on `decrypt_705330814_19c81ac46a73163b_0036XXXXXXXXXX87_20_02_2026_641`, **54 of 87 rows fall inside 15 duplicate-narration groups**, which description similarity alone cannot order. Print position breaks those ties. Position is not a scored field and only orders candidates already tied on description, so a genuinely wrong date still fails — but within such a group, a date/amount error paired to the wrong sibling row is possible in principle. `test_matcher.py` verifies shuffling the input introduces zero errors on unique-description rows.
- **`prior_session_wrong_baseline/` (21 records) is excluded from every number in this report.** It ran against `luna_prompt/LUNA_PROMPT.txt`, the ground-truth-flavoured instrument, which is the wrong baseline for a production comparison. Retained as evidence only.
- **GT `max_tokens` was raised 32,000 → 64,000 partway through the GT sweep.** This does not make the earlier records incomparable: `max_tokens` is a ceiling, not a sampling parameter, and every record collected under the lower cap finished with `finish_reason='stop'` well beneath it (see §6).

## 10. Production-readiness verdict for HDFC

| | Luna 5.6 + refined prompt | incumbent Gemini CSV |
|---|---|---|
| txn F1 vs Opus GT | 100.00% | 99.93% |
| description exact-match | 97.33% | 94.31% |
| substantive errors the PDF confirms | **53** | **128** |

**Verdict.**

1. **The prompt, not the model, is the live defect.** The `"C" ⇒ CREDIT` rule in the
   client's production prompt corrupts `direction` on ~64% of their HDFC corpus.
   Fixing that one clause moved sample `direction` disagreements **108 → 0**. This
   should be fixed regardless of which model the client runs, and it is the single
   highest-value action from this evaluation.
2. **Luna 5.6 on native PDF with the refined prompt is competitive with the
   incumbent** on this corpus, and is *better* on narration fidelity — it preserves
   printed `Ref#` strings and HDFC's broken intra-word spacing that the incumbent
   silently normalises away.
3. **Truncation is not a blocker.** Luna's 96,000-token cap has wide headroom even
   for the 223-transaction outlier (§6).
4. **Residual risk is `network`.** It is the field most prone to BIN-inference
   hallucination on both sides, and null is the correct answer on 199/281
   statements. Recommend asserting null unless a network word is literally printed.
5. **`direction` remains Luna's weakest field, and the incumbent is modestly better
   on it.** This is stated plainly because it cuts against the headline: the glyph
   fix is genuinely cured — PDF-adjudicated `direction` errors are now spread thinly
   across 12 statements at a maximum of 4 on any one, versus the pre-fix signature of
   107 of 109 rows on a single statement — but a residual gap remains. On the
   adjudicated set Luna is wrong on 18 rows vs the incumbent's 9, and **12 of Luna's
   18 are over-crediting** (calling a DEBIT a CREDIT), against 8 of the incumbent's 9.
   The mechanism is HDFC's genuinely mixed same-narration rows: a merchant like
   `SWIGGYBENGALURU` legitimately appears both as a purchase (`C 866.00`) and as a
   refund (`+ C 238.00`) on the same statement, so the `+` must be read per-row and
   cannot be inferred from the narration. **Recommended before production: a
   per-row `+`/`Cr` check on `direction`, plus a reconciliation of the CREDIT subtotal
   against the printed payments/credits figure.**

**Confidence is bounded by coverage: 131/281 challenger and
154/281 reference statements scored.** The direction/glyph finding
is corpus-wide and static (179/281 PDFs) and does not depend on sweep coverage;
the field-by-field accuracy tables do.

---

### Artefacts

| file | contents |
|---|---|
| `HDFC_PROMPT.txt` | the refined prompt |
| `PROMPT_CHANGELOG.md` | every change tied to an observed defect |
| `scores_phase3.json` | all scoring output, verbatim |
| `adjudication_stmt.json` / `adjudication_txn.json` | PDF adjudication with coordinates |
| `glaring_misses.json` | substantive errors, both sides |
| `truncation_audit.json` | the §6 audit |
| `gt_full/`, `phase3_refined/` | raw per-statement records incl. verbatim `usage` |
| `phase1_baseline/`, `phase2_refined{,_v1,_v2}/` | Phase 1 + the three prompt iterations |
| `prior_session_wrong_baseline/` | excluded; wrong-baseline run, kept as evidence |
