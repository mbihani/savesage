# ICICI credit-card statements — Luna 5.6 native-PDF evaluation

**Workspace** `fevm-stable` · **Challenger** `databricks-gpt-5-6-luna` (native PDF, single call,
`reasoning_effort=medium`, `max_tokens=96000`) · **Reference** `databricks-claude-opus-5`
(native PDF, `thinking:adaptive`, `output_config.effort=medium`, `max_tokens=32000`) ·
**Incumbent** the client's `icici.csv` (`detectionSource=GEMINI`, `modelName=gemini-3-flash-preview`).

> **Numbers in the Phase-3 tables are generated** by `score_phase3.py` → `report_tables.md`,
> never hand-typed. `report_tables.md` is the machine-generated companion to this document and
> is the authority if the two ever disagree.

---

## 0. Corrections to the brief (my measurements differ)

| Brief said | I measured | Consequence |
|---|---|---|
| PDFs: "~305 files" | **304** `.pdf` files (plus a `.DS_Store`) | corpus size is 304 |
| join matches "302 of 315 CSV rows" | **304 of 315** | the brief's join drops 2 real statements: two CSV `link`s carry `%20` where the file on disk has a literal space. URL-decoding the basename recovers both. `urllib.parse.unquote` is load-bearing. |
| — | 4 PDFs use a `decrypt_gmail:<n>:<hex>_` id prefix | the Axis harness's `_ID_RE` (`^decrypt_(?:encrypt_)?(\d+)_`) **raises** on these. Keeping that regex would have silently cost 4 statements. |
| — | 11 CSV rows have no PDF on disk | reported, never silently dropped (list in `scores_phase3.json`) |

**Scoreable set** = the 304-statement PDF∩CSV intersection, further intersected with whatever
the Opus GT arm completed (see §6 for exact completion counts).

---

## 1. The instrument (what was actually sent)

| | |
|---|---|
| **Phase-1 baseline** | the **client's own production prompt**, `SYSTEM PROMPT.txt` |
| file | 10,111 bytes, sha256 `c618380769746a4abe988cb12cb947d979bac81424deb371286183a3454ca362` |
| **sent** | the **inner string literal only** — the file is Python source (`SYSTEM_PROMPT = """…"""`). **10,088 bytes / 10,041 chars**, sha256 `9dc59e63b6957bf24ca3fdb9f7dee9389f5650dc030c7abfae7d3277ae025bac` |
| **Phase-3 challenger** | `ICICI_PROMPT.txt` — 11,885 bytes / 11,842 chars, sha256 `2ba790951037a779a84043bdd2cf3a930514898be9b62a5d32c3eedbe74350f6` |
| **schema** | `luna_prompt/LUNA_SCHEMA.json`, **unchanged**, verified byte-identical to `gt298_lib.GT_SCHEMA`; identical across all 3 banks |
| **GT prompt** | `gt298_lib.GT_PROMPT`, unchanged, sha256 `a14219f1…` — the shared cross-bank reference instrument |

`luna_prompt/LUNA_PROMPT.txt` was **not** used as the baseline, per instruction. It is
ground-truth-flavoured and hard-codes `issuerName … "Axis Bank"`, making it invalid as an ICICI
production baseline.

**Four of the 16 priority fields get NO guidance in the baseline prompt** (verified by grep on
the sent string): `network` 0 occurrences, `issuerName` 0, `totalMinimumAmountDue` 0, `txnType` 0.
Baseline misses on these are **prompt-coverage gaps, not model capability failures**, and are
labelled as such throughout.

---

## 2. Phase 1 — 10-statement baseline, client's own prompt

**Sample**: deterministic, no RNG — one statement per distinct card product (products ordered by
corpus frequency, then name; within a product, max estimated transaction rows, then max bytes,
then filename) for 8 slots, then the corpus-wide max-transaction and max-bytes PDFs for slots
9–10. Recorded in `phase1_sample.json`. This deliberately spans **10 different card products**
so the refinement is not tuned to one layout.

**Outcome: 10/10 `OK`.** No truncation, no schema violation, no parse failure, no 429.

| statement | product | Luna rows | CSV rows | txn F1 | mean desc sim |
|---|---|---:|---:|---:|---:|
| 557652636 | Expressions | 150 | 150 | 1.000 | 1.000 |
| 1010092654 | Coral | 140 | 140 | 1.000 | 1.000 |
| 2054837190 | Amazon | 68 | 68 | 1.000 | 0.994 |
| 516479745 | HPCL | 61 | 61 | 1.000 | 1.000 |
| 820648402 | Sapphiro | 42 | 42 | 1.000 | 1.000 |
| 979848021 | Platinum | 40 | 41 | 0.988 | 1.000 |
| 1711342048 | MMT | 19 | 19 | 1.000 | 1.000 |
| 1066621585 | AdaniOne | 18 | 18 | 1.000 | 1.000 |
| 524015761 | MMT_NEW | 8 | 8 | 1.000 | 1.000 |
| 670758880 | Amazon | 6 | 6 | 1.000 | 1.000 |

**Transaction extraction was essentially solved at baseline** — 552 of 553 rows recovered, exact
row count on 9/10 statements. The defects are concentrated in **metadata and card structure**.

### 2.1 The headline Phase-1 finding: the co-brand trap did NOT reproduce

The brief predicted `issuerName` would be the largest defect (wrong on 18/94 on Axis, where the
prompt likewise had no `issuerName` rule). **On ICICI it was wrong 0 times out of 10** — Luna
returned `"ICICI Bank"` on all ten, across ten *distinct* co-brands (Amazon, Coral, Sapphiro,
Platinum, MMT, MMT_NEW, AdaniOne, HPCL, Expressions). The incumbent also scored 10/10.

I am reporting this as a **non-reproduction, not a fix**. The likely reason is layout, not model
skill: every ICICI statement carries `ICICI Bank` in the letterhead, the footer
(`For ICICI Bank Limited`), the CIN, the contact URL and the regulatory text, so the issuing bank
is heavily over-determined in a way a co-brand name cannot displace.

> ⚠️ `issuerName` is **NON-DISCRIMINATING on this corpus** (303/304 incumbent rows are
> `"ICICI Bank"`). Its ~100% score is *not evidence* the co-brand risk is handled — it is
> evidence the field is nearly constant. Do not generalise it to a multi-issuer corpus.

### 2.2 Baseline defects, by frequency

| # | defect | count | who is wrong |
|---|---|---:|---|
| 1 | `rawStatementId` = the header `Invoice No:` value | **10/10** | Luna. Not a statement id. |
| 2 | `isPrimaryCard` = null on every multi-card statement | 11 cards / 7 stmts | Luna (defensibly — ICICI never prints "primary"/"add-on") |
| 3 | `programType` = `Cashback`/`Reward Points` vs incumbent's wallet names | 8 | **the incumbent** — see §5 |
| 4 | `network` populated with no evidence | 4 (CSV) + 1 (Luna) | mostly the incumbent |
| 5 | foreign-currency rows tagged `USD` while carrying the **rupee** amount | 3 rows | Luna |
| 6 | row/column drift dropped a row and shifted an amount | 1 stmt | Luna — most serious |
| 7 | a whole card missed (`820648402`: 2 of 3 cards) | 1 stmt | Luna |
| 8 | `amount` sign: incumbent negates credits | 12 rows | **the incumbent** — violates the client's own rule |

Full evidence, with statement ids, both values and PDF page/snippet, is in
`PROMPT_CHANGELOG.md` and `phase1_findings_client.json`.

---

## 3. Lever (a) — VALIDATING the client's existing ICICI rule

The baseline carried exactly one ICICI clause, an **untested client assertion**:

> *do not compute closingPoints using total points earned and redeemed; if not available, set to
> null. Points transferred = points redeemed.*

**Verdict: both halves are CORRECT — and the rule is more necessary than the client knew.**

1. **"Do not compute closingPoints"** — measured over all 304 PDFs: `Total Points earned` appears
   in 122/304 and `Points earned on iShop` in 122/304; **no PDF prints a closing/available points
   balance under any label.** So the only way to produce `closingPoints` is the forbidden
   derivation. Luna obeyed (null on 10/10). **The incumbent does not**: it emits a non-null
   `closingPoints` on **56/304**, and in the sampled cases it exactly equals
   `pointsEarnedThisCycle` (`1010092654` 495=495, `553419366` 134=134, `518298999` 340=340,
   `1315105175` 858=858). The client's rule is right and their own parser breaks it on ~18% of
   the corpus.
2. **"Points transferred = points redeemed"** — correct but narrow: `Points Transferred` occurs in
   only **8/304**, always as `Points Earned | Points Transferred to PAYBACK | PAYBACK Account
   Number` (verified `380476562`, `586112625`, `601053102`).

### 3.1 A corpus-wide fabrication trap the *generic* rules create — highest-value finding

The baseline's generic rewards rule lists **`Closing Balance`** as a label to populate
`closingPoints` from. On ICICI:

> **`Closing Balance` appears in 277 of 304 PDFs (91%) with the byte-identical value `26,958.20`.**

It is line 18 of a **pre-printed illustrative Minimum-Amount-Due worked example** (the numbered
`SL. No | Transaction` table headed *"On statement dated Nov 08, 2025, following Minimum Amount
Due is calculated"*) that ships unchanged on every statement and belongs to no cardholder.
Confirmed on `1010092654` p11, `1025056219` p8, `1025079069` p8.

So the client's live prompt contains a standing instruction to emit a **fabricated ₹26,958.20** on
91% of this corpus. The refined prompt fences the entire boilerplate table off explicitly — for
`closingPoints`, for every `statementLevelSummary` field, and as a transaction source.

**Honest scope of this finding: the trap is real and live, but nothing has yet fallen into it.**
I searched every numeric leaf of every extraction from all four sources (Luna baseline, Luna
refined, Opus GT, incumbent CSV) for the value `26,958.20`: **0 emissions, in any field, by any
source.** So this is a *latent* prompt defect, not an observed one. It is reported because (a) the
instruction genuinely names `Closing Balance` as a `closingPoints` source, (b) the string is
present in 277/304 inputs, and (c) a prompt whose correctness depends on the model ignoring one of
its own explicit rules is fragile — it can regress on a model or temperature change without any
input changing. The fence costs nothing; I am not claiming it fixed a measured error.

**Corroboration for lever (a) from an independent model.** On the statements scored so far, Luna
(refined) and the Opus-5 GT **independently** return `closingPoints = null` on **66/66**, while the
incumbent emits a non-null value on 7 of the same 66. Two models built on different prompts
agreeing on null, against an incumbent that derives a figure, is strong evidence the client's "do
not compute closingPoints" assertion describes the corpus correctly.

## 4. Lever (b) — STRIPPING other banks' rules

15 of the baseline's 136 lines are rules for banks absent from this corpus (HDFC ×6 blocks, SBI,
IndusInd, AU, Standard Chartered, IDFC, RBL, plus NeuCoins/Marriott/eDGE vocabulary). All removed.
One rule inside an HDFC block — *`+`/`Cr`/`C`/`CREDIT` ⇒ direction CREDIT* — was **kept and
de-scoped to apply generally**, because ICICI prints exactly that `CR` suffix.

Notably the RBL rule sets direction from **amount colour** (red/black=DEBIT, green=CREDIT). On a
corpus where direction is marked textually, that is not merely dead weight but a live hazard.

**Did stripping make the prompt shorter? No — and I am not claiming it did.** On Axis, removing
boilerplate made the prompt shorter *and* better; **that did not reproduce here.** Stripping
removed ~1.0k chars, the defect-driven ICICI rules added ~2.8k, for a **net +1,801 chars
(+17.9%)**. The two levers moved in opposite directions and are reported separately.

---

## 5. Adjudication — the CSV is the incumbent, not truth

Every Luna-vs-CSV disagreement on the 16 priority fields is resolved **against the PDF** with
PyMuPDF page/snippet evidence, classified `LUNA_WRONG` / `CSV_WRONG` / `BOTH_WRONG` /
`AMBIGUOUS_IN_PDF`. See `adjudication.json`; counts in `report_tables.md`.

Three adjudication rules, established from the PDFs and applied uniformly:

1. **`network`.** The only network mention on an ICICI statement is the fuel-surcharge disclaimer
   *"For RuPay/American Express/ Visa/Mastercard Credit Cards…"*, which names **all four**
   networks and identifies none. A non-null `network` is unsupported unless the token occurs
   outside that sentence.
2. **`utilisationPercent`.** **0 of 304 PDFs contain the string "utilis"/"utiliz".** An
   as-extracted value cannot be an extraction at all; the incumbent's figure (155/304) is
   *derived*. Luna's null is the correct extraction under the client's own `MISSING_DATA_RULE`,
   so charging it as a miss would penalise Luna for declining to fabricate.
3. **Contract violations.** Where the client's own prompt pins the answer — `amount` always
   positive, `programType` is a type not a wallet name, dates `DD/MM/YYYY` — a value breaking it
   is wrong even where the PDF is silent. Recorded as `CONTRACT_VIOLATION` with the rule cited.

### 5.1 `network` scored against the PDF (no reference is trustworthy here)

**The Opus GT itself fabricates this field.** It emits a non-null `network` 3 times and PDF
adjudication shows **all 3 are unsupported** outside the disclaimer (`1025079069` RUPAY,
`1065138420` VISA, `114630258` VISA). `network` is therefore **excluded from GT-referenced
scoring** and scored against the PDF for all three sources (`network_vs_pdf.py`):

| source | cards | fabricated `network` | fabrication rate |
|---|---:|---:|---:|
| **Luna (refined)** | 43 | **0** | **0.00%** |
| Opus 5 GT | 43 | 3 | 6.98% |
| incumbent CSV | 37 | 8 | **21.62%** |

*(counts over the statements complete at the time of writing; regenerate for finals.)*

Worst single case `820648402`: the incumbent assigned `American Express`, `Mastercard`, `RuPay` to
the statement's three cards — it walked the disclaimer list in order. Luna returned null for all.

### 5.2 Disagreements where Luna is right and the incumbent is wrong

* **`amount` sign, 12 rows (`1066621585`)** — `BBPS Payment received … 50,000.00 CR`: Luna
  `+50000`/`CREDIT`, incumbent `-50000`/`CREDIT`. The client prompt: *"transactions->amount is
  ALWAYS a positive number. Never negate the amount field regardless of the transaction
  direction."* `CSV_WRONG`.
* **`programType`, 8 statements** — incumbent returned `Amazon Pay balance`, `MakeMyTrip My Cash`,
  `Adani One`, `My Cash`. The client prompt: *"DO NOT copy payment methods or wallet names as
  programType."* `CSV_WRONG`.
* **`dueDate` (`524015761`)** — Luna `21/07/2026`, incumbent `July 21, 2026`; `DD/MM/YYYY` is
  required. `CSV_WRONG`.
* **`utilisationPercent`** — incumbent emits a derived figure; not printed anywhere. `CSV_WRONG`.

I did **not** change Luna's behaviour to match the incumbent on any of these. Doing so would
weaken a correct behaviour to improve an agreement metric.

### 5.2a A total incumbent failure: statement `238910814`

The incumbent's `data` blob for this statement is the **two-character string `{}`** — an empty
object. Every top-level column is blank too (`totalAmount`, `minimumAmount`, `dueDate`,
`totalCardLimit`, `availableLimit` all empty), with `detectionSource=GEMINI` and
`modelName=gemini-3-flash-preview` recorded as if the parse had succeeded. So the incumbent
produced **nothing at all** for a perfectly ordinary Coral statement, and produced it silently.

Luna extracted all seven statement-level fields correctly; each is confirmed printed in the PDF:

| field | Luna | incumbent | printed in PDF as |
|---|---|---|---|
| `totalAmountDue` | 48474.42 | *(nothing)* | `` `48,474.42 `` |
| `availableCreditLimit` | 51525.58 | *(nothing)* | `` `51,525.58 `` |
| `totalCreditLimit` | 100000 | *(nothing)* | `` `1,00,000.00 `` |
| `totalMinimumAmountDue` | 2670 | *(nothing)* | `` `2,670 `` |
| `issuerName` | ICICI Bank | *(nothing)* | letterhead/footer |
| `statementDate` | 16/11/2025 | *(nothing)* | `November 16, 2025` |
| `dueDate` | 04/12/2025 | *(nothing)* | `December 4, 2025` |

A silent empty-object parse is the most dangerous incumbent failure mode found: it is not a wrong
value that a validation rule could catch, it is an absent statement that looks processed.

**Frequency, measured corpus-wide so it is not overstated: exactly 1 of 304** incumbent rows has an
empty `data` blob. No incumbent row has zero cards or zero transactions. So this is a rare
catastrophic failure rather than a systemic one — but it is a *silent* one, and it is the failure a
downstream consumer is least likely to notice.

### 5.2b Adjudicator hardening — I had to fix my own probe before trusting it

My first PDF adjudicator was **biased toward accusing Luna**: it tested `value in pdf_text`, so
every gap in the *probe* became a fabrication verdict. Hand-checking the accusations against raw
page text found six distinct probe defects, all of which produced false `LUNA_WRONG`s:

| probe defect | example | effect |
|---|---|---|
| PDF hard line breaks | card name extracts as `Amazon Pay ICICI Bank\nCredit Card` | correct value reported absent |
| Indian digit grouping | `100000` prints `1,00,000.00`, never `100,000.00` | printed credit limit called a fabrication |
| unpadded long-form dates | `04/12/2025` prints `December 4, 2025` | correct dueDate called a fabrication |
| substring inside a word | `VISA` matches merchant city `VISAKHAPATNAM` | incumbent's fabricated network looked "supported" |
| **derived, never printed** | `currency: INR` (PDF shows only `₹`/backtick) | Luna's correct INR charged as fabrication **13×** on one statement |
| **image-only values** | product name has **0 text hits on 123/298 PDFs** | correct image-derived product name charged as fabrication |

**`LUNA_WRONG` fell from 41 to 11** once these were fixed, and every survivor is genuine. The
image-only finding is the most consequential: on 123 of 298 product-labelled PDFs the product token
(Coral/Sapphiro/Rubyx…) appears **nowhere in the text layer** — the statement prints only the
generic "ICICI Bank Credit Card" — yet Luna *and* the Opus GT both return the correct product,
matching the filename. Both are reading the **card-art image**. A text-based probe cannot
adjudicate `cardDisplayName` at all, so it is now reported as undecidable rather than guessed.

This is reported because the *pre-fix* numbers would have overstated Luna's error rate ~4×.

### 5.3 A scorer defect I found and fixed (would have libelled the incumbent)

The canonical `score.date_norm` tries only 6 formats and **echoes the raw string** on no match.
The incumbent's top-level date columns are long-form English (`"October 18, 2022"`), matching
none — so **22 statement-level dates that are the same day as the GT scored as `wrong_value`**,
making the incumbent look ~84% accurate on `statementDate`/`dueDate` when it was right. Fixed by
widening the format list (`score_lib.date_norm`), verified that a genuinely different day still
scores wrong. **All 22 disappeared.** Reported because the pre-fix numbers were wrong in the
incumbent's *disfavour*.

---

## 6. Phase 3 — full run

See **`report_tables.md`** for the generated tables:
scope actually measured · outcome tally · token accounting · field-by-field
(Luna-vs-GT accuracy, CSV-vs-GT incumbent accuracy, Luna-vs-CSV agreement) ·
**all-statements AND held-out** · transaction P/R/F1 + description fidelity ·
non-discriminating-field flags · adjudication counts.

### 6.0 `transactions[].description` — the one field where Luna is materially imperfect

Description is Luna's weakest priority field and the only one worth decomposing, because a single
raw accuracy number badly misrepresents it. Over the statements scored so far
(2,839 matched rows vs the Opus GT), 241 rows differ. Decomposed:

| class | rows | who is right | severity |
|---|---:|---|---|
| **spacing only** — identical once whitespace is ignored | **163** | GT (Luna re-spaced) | cosmetic-but-contractual |
| **dropped trailing country code** — GT `…PARASHRA IN`, Luna `…PARASHRA` | **66** | **GT** — verified printed in the PDF | low |
| genuinely different characters | **12** | mixed, see below | low count, higher severity |

So of 2,839 rows, **229 of the 241 defects (95%) are pure text-fidelity slips** — Luna closing the
PDF's intra-cell line-wrap gaps (`Google P lay` → `Google Play`) or dropping a trailing ` IN`.
Neither changes a date, an amount, or a direction. They violate the client prompt's *"copy
descriptions EXACTLY"* rule and would break exact-string joins downstream, but they do not corrupt
money.

The dropped-` IN` defect is **highly concentrated**: 49 of 66 rows are on a single statement
(`232344130`), plus 13 on `310385621`, 3 on `283344944`, 1 on `203051285` — i.e. 4 statements
carry all of it, so it reads as a per-statement mode rather than a uniform tendency.

**The incumbent BEATS Luna decisively on this field, and it is the incumbent's clearest win.**
Measured on the same GT over comparable row counts: the incumbent has **4 description defects in
2,717 rows (99.85%)** against Luna's **241 in 2,839 (91.5%)** — and none of the incumbent's 4 are
spacing slips. The Gemini incumbent reproduces the PDF's awkward intra-cell spacing faithfully;
Luna "tidies" it. If a downstream process joins on description strings, that is a real regression
and the single strongest argument for keeping a validation layer on the Luna output.

Of the 12 genuine character differences, the two most serious were adjudicated individually against
the PDF and **both are Luna errors, with the GT matching the print exactly**:
* `426486404` — Luna returned `UPI-389476433876-SK PAN H OUSE IN`. The PDF attaches reference
  `389476433876` to **`MANGO ST ATIONERY`**; the SK PAN HOUSE row's real reference is
  `291859843978` (what the GT returned). Luna moved a reference number between two rows — the same
  row/column drift family as the `979848021` amount shift, and the most concerning defect class
  found because a transaction identifier ended up on the wrong transaction.
* `516977279` — the statement prints `MYNTRA DESIGNS PRIVATE L Bangalore IN` twice and
  `Myntra BANGALORE IN` three times. Luna emitted the first 3× and the second 2×, inverting the
  multiplicities; the GT matched the printed counts exactly.

### 6.1 Token accounting — determined, not assumed

**Luna: reasoning sits INSIDE `completion_tokens`** (OpenAI convention). Verified per call, not
assumed: `prompt_tokens + completion_tokens == total_tokens` on **every** call, with
`completion_tokens_details.reasoning_tokens` reported separately and **non-zero on 10/10**
Phase-1 calls (232–707, sum 4,674). This confirms the prior 298/298 Axis finding holds on ICICI.

**Opus 5 reports no reasoning field at all** and `prompt+completion==total` on every call —
consistent with the brief's note of 0 reasoning tokens at every effort level. **The GT is
therefore NOT a high-reasoning pass** and must not be described as one.

**Luna's price is unpublished** → token counts only. No dollar figure, and none interpolated from
a sibling model. Opus 5 cost is computed at its published rate ($5/M in, $25/M out).

### 6.2 Anti-overfit: held-out numbers

The prompt was tuned on 10 statements and tested on ~304 — a ~30× extrapolation. Every Phase-3
metric is reported **both** over all statements **and** excluding the 10 tuning statements.
6 of the 8 added rules are null-forcing or evidence-requiring, which lowers fabrication risk
rather than pattern-matching the sample; the layout rules were verified against the printed PDF,
not against the incumbent's answers. If the held-out figure is materially worse than the
all-statements figure, that is stated in `report_tables.md` and here.

---

## 7. UNVERIFIED / not done

* **Generic-prompt full run (304) was NOT executed.** Priority order in the brief was
  refined-Luna full > Opus-GT full > generic-Luna full, and three workers share one
  output-TPM budget. **Consequence: the refinement's lift is measured on the 10 tuning
  statements only** (`baseline_vs_refined_on_10_tuning` in `scores_phase3.json`) — which is
  exactly the set the refinement was fitted to, so it is an **upper bound on lift, not an
  unbiased estimate.** The full-corpus lift of the refined prompt over the client's baseline is
  **UNVERIFIED**.
* **The GT is a single Opus-5 sample**, no self-consistency re-run. GT-referenced "accuracy" is
  agreement with one Opus pass, and §5.1 shows the GT is demonstrably wrong on `network`.
* **`cardDisplayName` is scored leniently** (substring match) because it is unstable run-to-run
  even inside the GT. Its score is not a strict-equality score.
* **`txnType` is not adjudicated.** It has 0 occurrences in the baseline prompt, the incumbent
  emits null for most rows, and it is a secondary field. The ~110 Luna-vs-CSV differences are a
  coverage gap, deliberately not tuned against a mostly-null reference.
* **Rewards fields beyond `closingPoints`** were validated only insofar as lever (a) required.
* Two `NETWORK_ERROR` calls (`URLError: Broken pipe`, 10 attempts each) are recorded as
  **infrastructure**, never as model failures, and were re-issued on resume.
* The `lastFourDigit` masking defect (§8) was found **after** the tested prompt was frozen and is
  **not** fixed in the scored numbers.

---

## 8. Production-readiness verdict for ICICI

**Raw-PDF → Luna 5.6 is deployable for ICICI, and on this corpus it is already better than the
incumbent parser** — with one metadata fix required first and one caveat about what the strong
scores do and do not prove.

**What is production-ready now.** Transaction extraction, the part that actually matters for a
statement parser and the part hardest to fix downstream. At *baseline* — before any refinement —
Luna recovered 552 of 553 transaction rows across ten different card layouts with an exact row
count on 9/10 statements and near-perfect description fidelity, including 150- and 140-row
statements. Statement-level money fields (`totalAmountDue`, `totalCreditLimit`,
`availableCreditLimit`, `totalMinimumAmountDue`) and the statement/due dates were clean.

**Where Luna beats the incumbent, and why it is not a rounding difference.** The incumbent
fabricates. It invents `network` on 21.6% of cards from a disclaimer that lists all four networks;
it derives `utilisationPercent` and `closingPoints` (56/304) that no PDF prints; it negates credit
amounts against the client's explicit instruction; it copies wallet names into `programType`
against another explicit instruction. Luna's characteristic failure is the opposite — a
conservative null, or a layout misread — which is materially safer for a financial product: a
null is visibly missing, a fabricated card network or a sign-flipped ₹50,000 payment is not.
Luna's `network` fabrication rate is **0%** against the incumbent's 21.6%.

**Where the incumbent is genuinely better, and it is not a nitpick.** Description fidelity.
The incumbent misses 4 of 2,717 descriptions (99.85%); Luna misses 241 of 2,839 (91.5%). 95% of
Luna's misses are fidelity-only — it closes the PDF's intra-cell line-wrap gaps
(`Google P lay` → `Google Play`) or drops a trailing ` IN` country code — so no money is corrupted,
but the client prompt explicitly requires descriptions copied EXACTLY, and any downstream join on
description strings would regress. This is the strongest reason not to drop a validation layer, and
the one dimension on which a swap to Luna is a step backwards.

**The one blocking fix.** `lastFourDigit` returns mask characters where real digits are printed
(`4315XXXXXXXX5002` → `XX02`, 4 cards). This is caused by the **client's own** prompt rule, whose
worked example assumes the mask covers the final four characters; on ICICI the mask sits in the
middle and the last four are real. `lastFourDigit` is a card-identity field used for joining, so
this must be fixed before rollout. It is a one-line prompt change (§ *Defect found during the
full Phase-3 run* in `PROMPT_CHANGELOG.md`) — not a model limitation.

**Residual risks.**
1. **Row/column drift — the one that actually worries me.** The three-column
   `description | Reward Points | Amount` layout produces cross-row contamination: on `979848021`
   a row was dropped and its ₹2,290 landed on the neighbouring Vijay Sales row (true value 123.76);
   on `426486404` Luna moved UPI reference `389476433876` off `MANGO ST ATIONERY` and onto
   `SK PAN H OUSE`. The refined prompt fixed the first instance, but the family recurred at full
   scale, which tells me prompting suppresses it rather than eliminating it. It is the only defect
   class found that puts a **wrong amount or a wrong identifier on a real transaction**, and it is
   silent. Mitigate in code, not in the prompt: reconcile extracted row sums against the printed
   statement total, and assert each row's reference number appears on that row's own printed line.
2. **Foreign-currency rows**: the `Intl.# amount` column caused a rupee amount tagged `USD`.
   Fixed in the refined prompt (3→0), but low-frequency, so under-sampled and worth a
   currency-vs-amount sanity assertion.
3. **Boilerplate contamination**: `Closing Balance 26,958.20` sits in 277/304 PDFs and the
   client's live prompt still names `Closing Balance` as a `closingPoints` source. Fix the
   production prompt regardless of which model is used.
4. **Metadata judgement fields** (`isPrimaryCard`, `cardDisplayName`, `rawStatementId`) have no
   ground truth in the document. Pin them by convention in the prompt, or accept null.
5. The lift of the refined prompt over the client's baseline is measured **only on the 10 tuning
   statements** (§7) — an upper bound.

**Does ICICI's co-brand diversity make this harder than a single-product corpus? Measurably, no —
and that is the surprise.** The corpus is heavily co-branded (133/304 Amazon, 45 Coral,
26 Sapphiro, 16 Platinum, 15 AdaniOne, and ~29 distinct product labels), and the brief expected
the co-brand trap — the largest metadata defect on Axis, wrong on 18/94 — to be the dominant
failure. **It did not reproduce: `issuerName` was correct on 10/10 across ten distinct
co-brands.** ICICI's template over-determines the issuer (letterhead, footer *For ICICI Bank
Limited*, CIN, URL, regulatory text), so the co-brand name cannot displace it.

The real difficulty on ICICI is **not** product diversity but **template uniformity of the wrong
kind**: every product shares one layout whose *boilerplate* is adversarial — a four-network
disclaimer that invites `network` fabrication, and a specimen MAD table with a fixed
`Closing Balance`. Those traps are **product-independent**, so they hit all 304 statements
equally rather than scaling with the number of co-brands. Practical consequence: **do not budget
per-product prompt rules for ICICI.** One template-level rule set covers the corpus; the
co-brand count is close to irrelevant. The per-product variation that does exist is confined to
low-stakes label fields (`cardDisplayName`, `productFamily`), not to money or transactions.
