# SBI Card — Luna 5.6 native-PDF evaluation

**Status: PARTIAL — Phase 1 and Phase 2 are complete; Phase 3 was still running when
this report was written.** All Phase-3 tables are generated from the persisted
artifacts by `finalize.sh`; the counts in `REPORT_TABLES.md` are authoritative and
carry their own `n`. Re-run `./finalize.sh` to refresh every number.

Workspace `fevm-stable`. Challenger `databricks-gpt-5-6-luna`, reference
`databricks-claude-opus-5`, incumbent = the `sbi.csv` `data` blob
(`detectionSource=GEMINI` on 315/315 rows; `modelName` ∈ {`gemini-3-flash-preview` 178,
`databricks-gemini-3-flash` 137`}).

---

## 0. Corrections to the brief — trust these measurements over the numbers I was given

| brief said | measured | evidence |
|---|---|---|
| PDFs: "~301 files" | **300** `.pdf` files (+1 `.DS_Store`, which is not a PDF) | `ls`, `sbi_lib.discover_pdfs()` |
| CSV: "315 data rows" | **315** ✓ | `csv.DictReader` |
| Join: "matches 300 of 315" | **300** ✓ — and every PDF joins; the 15 unmatched are CSV-only | `probe_join.py` |
| "SBI is the transaction-dense corpus… CSV `data` averages ~10.4KB vs ~3.7KB for HDFC" | **WRONG, and inverted.** SBI mean blob **4,067 B**, HDFC **5,300 B**, ICICI **4,336 B**. Transactions/statement: SBI mean **12.0** / median 6 / max 141; HDFC **16.2** / 10 / 223; ICICI **12.8** / 8 / 150. **SBI is the LEAST dense of the three.** No reading of the file reproduces 10.4KB (raw CSV line incl. doubled quotes = 4,880 B) | `density_crossbank.json` |
| "treat TRUNCATION as your primary risk" | **Not a risk on SBI.** 0 truncations in any arm. Worst Luna output 7,627 tokens against a 96,000 cap (7.9%); worst Opus output 6,418 against 64,000 (10.0%) | `tokens.json` |
| Two `decrypt_gmail:…` files | **Real, and they join.** They break `gt298_lib`'s `^decrypt_(\d+)_` id regex, so reusing its `discover_pdfs()` would have raised and dropped them | `sbi_lib.statement_id()` |

**Consequence of the density correction:** the brief's central strategic premise for
SBI — that a leaner prompt was needed to buy output-token headroom against truncation —
does not hold. I still stripped the other banks' rules (they are dead weight), but the
refined prompt is deliberately **longer** (+48.3%), because the measured defects were
layout-comprehension failures, not cap failures. See `PROMPT_CHANGELOG.md` §(b).

---

## 1. Instruments

| | |
|---|---|
| Baseline prompt | `/Users/mayanck.bihani/Savesage/SYSTEM PROMPT.txt`, sha256 `c618380…` (10,111 B on disk). **Sent: the inner string only — 10,041 chars / 10,088 bytes**, sha256 `9dc59e63b6957bf2…`. The file is Python source (`SYSTEM_PROMPT = """…"""`); `sbi_lib.strip_python_wrapper()` strips the wrapper and fails loud if the shape changes. |
| Refined prompt | `SBI_PROMPT.txt`, 14,887 chars / 14,932 bytes, sha256 `da15a77509cca13e…` |
| Schema | `luna_prompt/LUNA_SCHEMA.json`, **UNCHANGED**, verified byte-identical to `gt298_lib.GT_SCHEMA` (33 leaves) and identical across all three banks and all arms |
| GT instrument | `gt298_lib.GT_PROMPT` (8,243 chars) + same schema, **UNCHANGED** — the shared cross-bank reference |
| Luna request | OpenAI `file` block, `data:application/pdf;base64,…`, `reasoning_effort="medium"`, `max_tokens=96000`, `response_format` json_schema strict |
| Opus request | Anthropic `document` block, `thinking:{type:adaptive}`, `output_config:{effort:medium}`, **`max_tokens` raised 32000 → 64000** so a truncated GT could not silently penalise the challenger |
| Concurrency | **1** throughout (ceiling 2), three workers sharing one workspace output-TPM budget |

`luna_prompt/LUNA_PROMPT.txt` was **not** used as the baseline. An earlier Phase-1 pass
in this session mistakenly did use it; that run was discarded and Phase 1 re-run against
the client prompt. The mis-prompted run is kept at `run_luna_generic/` and feeds no
reported number.

**Honest note on the GT:** Opus 5 returned **0 reasoning tokens on 100% of records**
(`reasoning` absent from its usage block entirely). This is not a high-reasoning pass.

---

## 2. Phase 1 — baseline, 10 statements, client prompt

Sample chosen by a fixed deterministic diversity rule (no RNG), biased to structural
extremes — see `select_sample.py` / `phase1_sample.json`. It spans 1→141 transactions,
6/7/8/9-page layouts, a non-INR row, a no-rewards control, and 10 distinct card
products.

**Outcome: 10/10 `OK`, `finish_reason=stop` on all 10, 0 truncations, 0 429s, 0 schema
violations, 0 parse failures.** Input 182,730 tok, output 32,975 tok (mean 3,298).

Defects, every one located in the PDF with PyMuPDF page/coordinate evidence
(`phase2_measure.py` → `phase2_measured.json`):

| count | pattern | attribution |
|---:|---|---|
| 8 | dropped a printed row dated **before** `statementPeriodStart` | **the baseline prompt's own date-window rule**, applied as a filter |
| 3 | dropped a printed row in the **leading band** above `TRANSACTIONS FOR` | layout blindness |
| 1 | dropped a printed row, unclassified | — |
| 2 | `PAYMENT RECEIVED` marked `DEBIT` | **the baseline prompt is wrong**: it says "Payments TO the bank → DEBIT" |
| 1 | amount `15,050.00` read as **15.05** | thousands separator read as a decimal point |
| 4 | `closingPoints` taken from the **cycle strip** (correct) | logged as evidence the SBI rule holds |

12 of 3,769 corpus rows is small, but the *cause* is systematic and corpus-wide: **76 of
304 statements (25.0%) have at least one transaction dated before `periodStart`** — 124
rows in total. All 124 precede `periodStart`; **0** exceed `periodEnd` and **0** exceed
the statement date.

### The date-window rule — a recommendation, not a unilateral change

The brief flagged (from Axis) that "transaction dates must not exceed the Statement
Date" cost a legitimate row. **On SBI that clause is harmless — 0 rows violate the upper
bound.** The damage comes from the *lower* bound and the "swap day/month and re-validate"
sanity check, which Luna applied as a delete. I narrowed the rule to correcting a date
and forbade deleting a row; I did not remove the concept. The user chose to keep this
rule as-is on Axis, so this is presented as a recommendation — reverting is one line, at
a measured cost of ~124 rows.

### Validating the existing SBI closingPoints rule — it is CORRECT

The client prompt's SBI clause was an untested assertion. It is right and load-bearing.
SBI prints **two** reward tables holding different numbers: a 4-cell current-cycle strip
(`Previous Balance | Earned | Redeemed/Expired/Forfeited | Closing Balance`) and a
`SAVINGS AND BENEFITS SECTION` with `For this statement | For this year | From the card
issue date`. On stmt **1707857175** those three columns read **1072 | 5077 | 6590**;
Luna correctly took 1072. Taking the lifetime column would be a **6.1x** error. Kept
verbatim and reinforced with the layout mechanics that make it followable.

**One genuine `BOTH_WRONG` — stmt 515948911.** PDF prints `-1467 | -1467 | 44136` and a
`CASHBACK` block, no cycle strip. Luna returned `null`; the incumbent returned `1467`
with the **sign dropped**. The printed figure is negative. Reported as AMBIGUOUS rather
than scored as a clean win for either side.

---

## 3. Phase 3 — see `REPORT_TABLES.md` for all generated tables

`REPORT_TABLES.md` (regenerated by `./finalize.sh`) carries, each over **all**
statements and again over the **held-out** set (all minus the 10 tuning statements):

- `luna_vs_gt` — **ACCURACY** vs the Opus-5 native-PDF reference
- `csv_vs_gt` — the **incumbent's own accuracy** against the same reference
- `luna_vs_csv` — **AGREEMENT** with the incumbent (explicitly *not* correctness)
- per field: n, correct %, `wrong_value`, `null_when_populated`,
  **`hallucinated_when_GT_null` counted separately**, `both_null` (excluded from the
  denominator — a field null on both sides is not evidence either way)
- transaction precision / recall / F1 + byte-exact description fidelity
- the refinement lift (refined vs client-baseline prompt on the same statements)
- token tables and the outcome tally

### Transaction recall: truncation-caused vs genuine misses

The brief asked for these to be separated. **Across 350 completed calls in all three arms
there are 0 truncations** (`finish_reason=stop` on 100%; no `TRUNCATED_*` outcome in any
record). Therefore **every recall miss in this corpus is a GENUINE miss** — truncation
cannot be a confound, and no miss should be excused as a cap artifact.

### Verified refinement lift (partial — the refined arm had not reached all 10 tuning
statements)

Direct before/after on statements where both the client-baseline and refined arms have a
record:

- **stmt 734857498 — `PAYMENT RECEIVED` direction FIXED**: baseline `DEBIT` → refined
  `CREDIT`, matching the printed `C` marker and the incumbent. This is the change with
  the widest reach (the incumbent labels 485/485 such rows `CREDIT`).
- transaction counts held identical to the baseline on every jointly-completed statement
  (65/65, 56/56, 90/90, 12/12) — the added rules did not cost recall anywhere.
- The two statements that exhibited the dropped-row defect (1707857175, 837436340) sort
  late and had not yet been re-run, so **the fix for changes 2 and 3 is UNVERIFIED at
  the time of writing**; `REPORT_TABLES.md` carries the final numbers.

### Fields flagged as non-discriminating or trivially solved

A high score on these is **not** earned skill:

| field | why |
|---|---|
| `transactions[].currency` | incumbent: `INR` on 3,729/3,769 rows (98.9%); 9 non-INR rows total. Effectively a constant. |
| `statementMeta.issuerName` | single issuer: `SBI Card` / `SBI CARD` / `SBI card` on 314/315. Scoring it measures casing normalisation. |
| `cards[].cardMeta.network` | **0 of 300 PDFs print a network label anywhere in the page-1 header band** (verified: 296/300 never print one on page 1 at all; the other 4 only in T&C prose). `null` is the only correct answer, so the field measures *restraint*, not extraction. |
| `statementLevelSummary.utilisationPercent` | **0 of 300 PDFs contain `utilis`/`utiliz`.** Arithmetic, not extraction. |
| `cards[].cardMeta.cardDisplayName` | judgement-laden; scored **leniently** (substring containment either way, `kind=LENIENT` recorded). |
| `cards[].cardMeta.lastFourDigit` | SBI masks to the last **two** digits (`XXXX XXXX XXXX XX25`), so a 2-digit answer is correct; a 4-digit reference agreeing on the last 2 is credited `kind=MASK_DEPTH`. |
| multi-card logic | **untested: 315/315 statements are single-card.** |

### `utilisationPercent` is structurally asymmetric — do not read the as-extracted row

The shared schema sets `additionalProperties:false` and does not list the key, so Luna
and the GT are **forbidden** to emit it (0 records do). The incumbent ran under no such
constraint and emits it on **180/315** rows — and 151 of those 180 equal its own
`totalAmountDue/totalCreditLimit×100`, i.e. it is computing, not reading. As-extracted
therefore charges the incumbent ~180 `hallucinated_when_null` cells purely for a schema
it was never bound by. **Only the as-derived variant is comparable** (same formula
applied to all three sources); both are reported.

### Known GT-instrument defects — they cap `luna_vs_gt` and are excluded from "misses"

Verified against the PDFs. These are GT errors, **not** challenger errors:

| GT defect | effect |
|---|---|
| `dueDate` = `NO PAYMENT REQUIRED` → GT returns `null` | SBI really prints that string on credit-balance statements. The **GT prompt has no non-date-dueDate rule; the client prompt does.** Luna and the incumbent are both RIGHT and both score `hallucinated_when_null`. Equal count on both sides, so it does not bias Luna vs CSV — but it depresses both accuracy figures. |
| `rewards.programType` → GT returns a section **header** (`SHOP & SMILE SUMMARY`, `REWARD SUMMARY`) | verified: those strings are headers above the points strip, not programme names. Secondary field, not one of the 16. |
| `transactions[].date` → GT returns `null` on continuation rows | SBI omits the date on a row continuing the previous one (`IGST DB @ 18.00%` under `INTEREST ON EMI`). Luna carries the date down; neither misreads a printed glyph. |

---

## 4. Scoring integrity

**The transaction matcher is non-circular, and this is tested, not asserted.**
`match_txns_by_description` admits pairs on **description similarity only**
(threshold 0.60), with a globally-descending greedy assignment that is provably 1:1 and
order-insensitive. `date`, `amount`, `direction` and `currency` are **never** used to
admit a pair — they are the fields being scored. A date tie-break orders *equal*-similarity
candidates only (SBI repeats narrations verbatim — three identical `UPI-Blinkit` rows —
so without it the scorer charges an arbitrary permutation as date defects).
`test_matcher_sbi.py` proves all of it, including that a pair whose date, amount **and**
direction are all wrong still matches and still scores wrong on all three.

**Scorer bug found and fixed — it would have inverted the headline result.** The
canonical `score.date_norm` does not parse SBI's dominant transaction-date format, the
2-digit-year `DD Mon YY`; it returns the string unchanged, so `'22 Jun 26' != '22/06/2026'`.
**2,733 of 3,769 (72.5%)** incumbent transaction dates are in that form, while Luna and
Opus emit `DD/MM/YYYY` on 100% of rows. Unpatched, the scorer would have charged the
incumbent a fabricated 72.5% date-defect rate, handed Luna an unearned win, and
corrupted the matcher's tie-break. `score_lib_sbi.date_norm` wraps the canonical
function rather than editing it, so the other two banks keep the identical yardstick.

**Adjudication.** Every Luna-vs-incumbent disagreement is settled against the PDF, not
against either party: statement-level money by **geometric label binding** (the SBI
ACCOUNT SUMMARY grid emits labels and values in *different* orders, so reading order is
not evidence), transaction rows against the geometrically reconstructed printed row
including the right-hand C/D column. Verdicts: `LUNA_WRONG` / `CSV_WRONG` /
`BOTH_WRONG` / `AMBIGUOUS_IN_PDF` → `adjudication_refined.json`.

Two adjudicator bugs were found and fixed during validation, both of which would have
misattributed blame: (1) `S.direction()` returns lowercase, so comparing against
`"CREDIT"` never matched and turned every direction disagreement into a bogus
`BOTH_WRONG`; (2) two SBI rows whose narrations differ only in a trailing
foreign-currency amount (`PAYOO-HIGHLANDS … 39,000.00 VND` vs `… 1,68,000.00 VND`) were
charged as amount errors when the disagreement is a matcher row-assignment ambiguity —
now `AMBIGUOUS_IN_PDF` when both disputed values are printed on candidate rows.

**Every accusation was hand-verified against the raw page text before its count was
trusted** — a naive `if value not in pdf_text: MODEL_WRONG` probe is biased toward
accusing whichever side is more correct, because each gap in the *probe* becomes a
fabrication verdict. All 13 `*_WRONG` verdicts in the partial adjudication were
re-checked with a hardened probe (word-bounded regex, western **and** Indian
lakh/crore digit grouping, `%.2f` forms). Results:

- **Both network false-positive traps were present in this corpus and both were already
  excluded** by the geometric header-band test: `Visa` appears on page 1 of stmt
  **319897605** as a **merchant name** (`CRED   Visa Direct   IN`, a transaction row at
  y=507), and elsewhere only in T&C prose on pages 2–8. Confirmed corpus-wide:
  **0 header-band network hits across all 300 PDFs.**
- **The `transactions[].amount` sign verdicts are judged against the CLIENT'S OWN
  CONTRACT, not against "is it printed"** — both the baseline and refined prompts state
  *"transactions->amount is ALWAYS a positive number. Never negate the amount field
  regardless of the transaction direction."* The incumbent emits **5 negative amounts
  across 2 statements**; the magnitude is printed correctly, so the sign is a contract
  violation rather than a misread glyph. Scored `CSV_WRONG` on that basis, which is the
  correct reading, and the distinction is stated here rather than buried.
- Where the probe cannot see the evidence the verdict is `AMBIGUOUS_IN_PDF`, never a
  fabrication charge — e.g. the 2 unmarked `TRANSFER TO …` direction rows.

**`network` adjudication is evidence-based.** Every network-word occurrence in the
corpus was enumerated before judging: the recurring lines are the dispute-policy
paragraph `(VISA, MasterCard, Rupay, Amex) Guidelines`, `VISA Credit Card Pay`,
`Mastercard MoneySend`, a fee table (`minimum of $175 for VISA`), and
`Emergency Card Replacement (When Abroad)`. **0 of 300** PDFs print a network label in
the page-1 header band where the card number and product name live, so on SBI *any*
non-null `network` is a fabrication. Both sides do it; the incumbent ~3x more often.

---

## 5. Robustness and rate limits

- Per-statement **atomic** persistence + idempotent resume. Verified under a real
  crash: both primary arms were killed mid-run at GT 94/300 and refined 65/300;
  **0 corrupt records**, and both resumed exactly where they stopped with zero repeated
  work. Arms were relaunched **fully detached** (`launch_detached.py`, `os.setsid()` —
  macOS has no `setsid`) so a harness-cancelled foreground command can no longer take
  them down via the shared process group.
- **0 429s across every arm.** Concurrency 1 was sufficient; `rate_limited=0` and
  `attempts>1` only on the retried infrastructure failures.
- **1 infrastructure failure**: GT stmt `325774041`, `URLError: [Errno 32] Broken pipe`
  after 10 attempts → classified `NETWORK_ERROR` / `infrastructure`, **never** as "the
  model failed to extract". Non-terminal, so a resume pass retries it.
- OAuth re-minted proactively every 20 min and reactively on 401/403, via the pinned
  `/usr/local/bin/databricks`. No IP-ACL 403s occurred.
- **Prompt-provenance guard added after a real incident.** I edited `SBI_PROMPT.txt`
  while the refined arm was in flight (adding a `TRANSFER TO …` rule); `run_arm.py`
  re-reads the file per call, so that would have silently mixed two prompts inside one
  arm. Reverted, verified all records carry one hash matching disk, and `run_arm.py` now
  **refuses to start** if any existing record's `prompt_sha256` differs from the prompt
  on disk (tested both directions on a scratch copy). The deferred rule is documented in
  `PROMPT_CHANGELOG.md` as a recommendation and is **UNVERIFIED** — no run used it.
- Inputs are read-only and **verified unchanged** at the end: 300/300 PDF mtimes
  identical, CSV mtime+size identical, prompt sha256 matches the brief, schema unchanged
  (`verify_inputs.py`).

---

## 6. UNVERIFIED / not done

- **Phase 3 was incomplete at the time of writing.** The `n` in every table states what
  it covers. `./finalize.sh` regenerates everything.
- **The client-baseline arm over the full corpus is the least complete.** Per the brief's
  priority order (refined-Luna > Opus GT > baseline-Luna) it was chained to start only
  when a slot freed. Until it completes, the refinement-lift table falls back to the
  10 tuning statements, which is **the overfit-favourable comparison** — read it as an
  upper bound, not a held-out result.
- **The `luna_prompt/LUNA_PROMPT.txt` arm was not run** on the full corpus. It is not
  the baseline and its 10-statement mis-prompted run is excluded from all metrics.
- **The deferred `TRANSFER TO …` direction rule is unmeasured** (see above).
- **Multi-card behaviour is untested** — 315/315 statements are single-card.
- **`txnType`, `rewards.*` and the other secondary fields** are scored and present in
  `scores_*.json`, but are not part of the 16 priority fields and are not adjudicated
  against the PDF; `rewards.programType` in particular is polluted by the GT defect above.
- **Luna's price is unpublished.** Token counts only — no dollar figure, no
  interpolation from a sibling model. Opus 5 cost is computed at its published
  $5/$25 per 1M rate.
- **Description fidelity is measured against the reference's string, not the PDF glyph
  run.** Byte-exactness vs GT/CSV is reported; a systematic mismatch where *both* models
  normalise SBI's fixed-width column padding identically would not be caught.

---

## 7. Production-readiness verdict for SBI

**Raw-PDF → Luna 5.6 is deployable for SBI, conditional on the refined prompt.**

Supporting evidence:
- **Reliability is a non-issue.** Across every arm: 0 truncations, 0 429s at
  concurrency 1, 0 schema violations, 0 parse failures, 0 escaped-transactions-string
  outcomes. The single failure in the whole exercise was one broken pipe on the GT arm.
- **Truncation and cost blow-up do not materialise at SBI's density**, and the brief's
  density premise was inverted (§0). Worst observed Luna output is **7,627 of 96,000**
  tokens (7.9%); mean output ≈1,100–3,300 tokens depending on prompt. Input is dominated
  by the ~19k-token PDF payload, and the refined prompt adds only ~1,200 tokens (~+6%
  input). Headroom is ~12x on the worst statement. **`max_tokens=96000` is far larger
  than needed; ~24,000 would cover the worst case with 3x margin** and would cap
  tail-latency risk.
- **The residual errors are prompt-shaped, not capability-shaped.** Every Phase-1
  defect traced to a missing or actively wrong prompt rule — most importantly the
  baseline's "Payments TO the bank → DEBIT", which is backwards for a credit-card
  statement and corrupted the single highest-frequency credit row in the corpus
  (the incumbent labels 485/485 `PAYMENT RECEIVED` rows `CREDIT`).
- **The incumbent is not a safe reference.** It hallucinates `network` ~3x more often
  than Luna, emits a computed `utilisationPercent` as if extracted, inverts the sign on
  at least one reward figure, and leaks the **cardholder's personal name** into
  `cardDisplayName` on **7 of 315 statements** (2.2%) — `BIPIN PATEL` (221159806),
  `ARIHANT KATARIYA` (905768587), `SHUBHAM DESHMUKH` (515948911), `SANGITA RANI`
  (63610251), `KUNALSING THAKUR` (1253765972), `BHAVANA KS` (1511624796), `SUNIL`
  (864770940) — while carrying the correct product name in `productFamily` on 6 of the
  7. Luna and the GT both return the product name on these. That is a **PII leak into a
  product-name field**, not merely a wrong string. Adjudicated disagreements run heavily
  `CSV_WRONG`.

Residual risks to manage:
1. **The date-window rule is the biggest single lever and it is still a recommendation.**
   If the original wording is restored, expect ~124 rows across ~76 statements to be
   silently deleted — a *recall* failure that produces clean-looking, schema-valid,
   quietly incomplete output. This is the failure mode most likely to reach production
   undetected, because nothing errors.
2. **`network` should be forced to `null` for SBI in code**, not left to the prompt.
   0/300 statements print it; every non-null value observed was a fabrication.
3. **Deploy-time guardrail worth adding:** compare the emitted transaction count against
   a cheap geometric row count from the PDF (`sbi_pdf_evidence.txn_rows`, PyMuPDF, no
   model call). It caught all 12 dropped rows here and would catch a silent recall
   regression in production.
4. **The 30x extrapolation stands.** The prompt was tuned on 10 statements and tested on
   ~300; 4 of the 13 changes are prophylactic with no observed defect. The held-out
   numbers in `REPORT_TABLES.md` are the ones to trust.
5. **Untested surfaces:** multi-card statements (0 in corpus) and any layout outside the
   16 signatures seen here.
