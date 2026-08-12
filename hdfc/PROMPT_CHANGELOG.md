# HDFC prompt refinement — changelog

## Baseline used

| | |
|---|---|
| **File** | `/Users/mayanck.bihani/Savesage/SYSTEM PROMPT.txt` |
| File size on disk | 10,111 bytes, sha256 `c618380769746a4abe988cb12cb947d979bac81424deb371286183a3454ca362` |
| **Bytes actually SENT to the model** | **10,088 bytes** (10,041 chars) |
| sha256 of what was sent | `9dc59e63b6957bf24ca3fdb9f7dee9389f5650dc030c7abfae7d3277ae025bac` |
| How the wrapper was stripped | AST parse (`ast.literal_eval` of the `SYSTEM_PROMPT` assignment), not regex/slicing — see `hdfc_lib.baseline_prompt()` |
| Wrapper overhead removed | 23 bytes (`SYSTEM_PROMPT = """` + `"""`) |

This is the **client's own production prompt**. It is *not* `luna_prompt/LUNA_PROMPT.txt`.

> **Correction to prior work in this directory.** An earlier session ran Phase 1/2 using
> `luna_prompt/LUNA_PROMPT.txt` (8,230 chars, sha `e8e90c6c…`) as the baseline — the
> ground-truth-flavoured instrument, which is the wrong instrument for a production
> baseline. That output is retained unmodified under `prior_session_wrong_baseline/`
> as evidence and was **not** reused. Every number in `HDFC_REPORT.md` comes from runs
> against the client prompt above.

Schema: `luna_prompt/LUNA_SCHEMA.json`, **unchanged**, verified byte-identical to
`gt298_lib.GT_SCHEMA` (32 leaves). Held constant across all three banks. The baseline
prompt says "strictly matching the provided schema" but embeds no schema — it arrives
only via the `response_format` json_schema parameter.

## Refined prompt

`HDFC_PROMPT.txt` — 17,413 bytes, sha256 `6cc57f692859c4453d918ed3a91cc02e5c4cc67eb08dd3dca17161b9b4524512`.

Net **+7,325 bytes** vs baseline. On the Axis corpus stripping other banks made the
prompt shorter *and* better; here the strip saved only 752 bytes (7.45%) while the
HDFC-specific defects found required substantially more text, so this prompt is
**longer**. That is a measured outcome, not an assumption.

Refinement went through three measured iterations. Each is kept:
`phase2_refined_v1/` (regressed), `phase2_refined_v2/`, `phase2_refined/` (final).

---

## Lever (a) — VALIDATING the HDFC clauses the client prompt already asserts

These were untested client assertions. Checked against all 281 real PDFs:

| Existing HDFC clause | Marker present in corpus | Verdict |
|---|---|---|
| `pointsEarnedThisCycle` ← "Feature + Bonus Reward Points Earned" | 135/281 | **VALID**, kept |
| `pointsEarnedThisCycle` ← "NeuCoins Earned" | 37/281 | **VALID**, kept |
| `pointsRedeemedThisCycle` ← "Disbursed" (priority 1) | 187/281 | **VALID**, kept |
| `pointsRedeemedThisCycle` ← "Cash Back Summary" (priority 2) | 33/281 | **VALID**, kept |
| `closingPoints` ← "Reward Points" (not directly available) | 187/281 | **VALID**, kept |
| Cardholder-name row must not be prepended to descriptions | name-row form in 4/281 | **VALID but rare**; kept and strengthened (see C6) |
| `"+"` / `"Cr"` / `"C"` / `"CREDIT"` ⇒ direction CREDIT | — | **PARTLY WRONG — see C1. The `"C"` half is a factual error.** |

### The one clause that is WRONG for this corpus

**`- If in the transaction amount have a "+" or "Cr" or "C" or "CREDIT" symbols, set direction to "CREDIT".`**

`"C"` is **not** a credit marker on HDFC statements. It is the **rupee sign**.

Evidence (`decrypt_738368244_19f70d2e2c77ebd5_6530XXXXXXXXXX38_16-07-2026_366.pdf`):

- The font embedded in the PDF is literally named **`ITFRupee` / `ITFRupee,Bold`**, and
  the glyph extracted as `C` is code point `0x43` rendered in that font. PyMuPDF span
  dump, page 1: `font=ITFRupee,Bold size=15.0 raw='C' cp=['0x43']`.
- The statement's own headline figures carry the same prefix:
  `TOTAL AMOUNT DUE` → `C13,507.00`, `MINIMUM DUE` → `C680.00`. A total amount due is
  self-evidently not a credit transaction, so `C` cannot mean credit.
- Consequence measured: Luna marked **107 of 109** rows on that statement `CREDIT`,
  including every `UPI-<person>` purchase. The incumbent CSV marked exactly 2 rows
  CREDIT — the two rows the PDF actually prints with a `+` prefix
  (`+ C 2,600.00`, `+ C 49,259.00`, both `CREDIT CARD PAYMENTNet Banking`). **The
  incumbent is right here and the client's own prompt rule caused Luna's error.**
- Corpus scope: **179 of 281 PDFs (63.7%)** embed a Rupee font, and in exactly those
  same 179 a bare `C` prefixes an amount. The correlation is perfect.

This is the single highest-value finding of the HDFC evaluation: a rule in the client's
production prompt is actively corrupting `direction` on ~64% of their HDFC corpus.

---

## Lever (b) — STRIPPING other banks' rules

Baseline carries rules for 7 banks that are not HDFC. All removed:

| Removed | Baseline occurrences |
|---|---|
| ICICI BANK (closingPoints / points transferred) | 1 line |
| INDUSIND (totalAmountDue vs Total Outstanding) | 1 line |
| AU Bank (statementDate from period sentence) | 1 line |
| Standard Chartered (Card Number ≠ Account Number) | 2 lines |
| IDFC FIRST SELECT (card number source) | 2 lines |
| SBI cards (closingPoints / cashback) | 3 lines |
| RBL (direction by amount COLOUR) | 4 lines |
| Non-HDFC reward programs ("EDGE Reward Points", "Marriott Bonvoy Points", "Membership Rewards") | in programType list |

Measured dead weight: **8 lines / 752 bytes / 7.45%** of the baseline. Verified zero
occurrences of all 7 bank names remain in `HDFC_PROMPT.txt`.

Note the RBL rule was worth removing on more than length grounds: it instructs direction
by **amount colour**, which is exactly the kind of non-textual cue that competes with the
rupee-glyph correction in C1.

---

## Changes, each tied to an observed defect

### C1 — Rupee-glyph section added; `"C" ⇒ CREDIT` deleted
**Defect:** 107 of 109 transactions on `decrypt_738368244…` had `direction` flipped to
CREDIT. **Statement id:** `decrypt_738368244_19f70d2e2c77ebd5_6530XXXXXXXXXX38_16-07-2026_366`.
**Evidence:** font `ITFRupee`, cp `0x43`; `TOTAL AMOUNT DUE = C13,507.00`.
**Change:** new leading section "THE RUPEE GLYPH"; `"C"` removed from the CREDIT marker
list; credit markers restricted to a leading `+` or a trailing `Cr`/`CR`; description-based
fallback given; an explicit sanity check ("if you are about to mark most rows CREDIT, you
have misread the glyph").
**Result:** `direction` disagreements on the sample **108 → 0**.

### C2 — Indian digit grouping after the glyph *(added in v2, fixing a v1 regression)*
**Defect introduced by C1:** on `decrypt_705330814_19c81ac46a73163b_0036XXXXXXXXXX87_20-02-2026`,
`totalAmountDue` came back **94022** where the PDF prints **`C1,94,022.00`** — the rule
made the model consume `C1` as the marker.
**Evidence:** PDF text `TOTAL AMOUNT DUE\nC1,94,022.00`; `194022` correct, and the CSV had it right.
**Change:** "the glyph is EXACTLY ONE character"; worked lakh-grouping examples
(`C1,94,022.00 → 194022.00`, explicitly "NOT 94022").
**Result:** defect cleared in v2; `totalAmountDue` diffs back to 0.

### C3 — EMI badge rule bounded *(added in v2, fixing a v1 regression)*
**Defect (baseline):** 26 descriptions on `decrypt_705330814…` carried an invented `EMI `
prefix — `"EMI PHARMEASY INMUMBAI"` where the PDF prints `EMI` on its **own line** above
`PHARMEASY INMUMBAI`. **Luna wrong, incumbent right.**
**Defect introduced by the v1 fix:** on `decrypt_252502266_19bc220c2d3c07ef_4341XXXXXXXXXX35_14-01-2026`
Luna **dropped 4 real rows** (`1% on all DCC Transaction (Ref# …)` ×2 and their `IGST-VPS…`
tax rows) — all four verified printed in the PDF. Row count 51 → 47.
**Change:** rule now removes "ONE leading badge word" and "NEVER removes a row"; applies
only to a bare `EMI` on its own line; names the DCC-fee and IGST rows as keepers; adds a
row-count reconciliation instruction.
**Result:** row count restored to 51/51; EMI-prefix defect stays fixed (0 in v2/v3).

### C4 — Clock-time stripping confined to the `date` field *(added in v2, fixing a v1 regression)*
**Defect introduced in v1:** on `decrypt_252502266…`, 6 descriptions lost their leading
`PM ` — `"PM *ViFun Live Co LiHongKong"` became `"*ViFun Live Co LiHongKong"`. The PDF
prints `PM *ViFun…`; `PM` is part of the merchant name.
**Change:** the DD/MM/YYYY rule now states that dropping the clock time applies to `date`
only and must never edit `description`, naming this merchant as the example.
**Result:** cleared in v2.

### C5 — `lastFourDigit`: real digits vs masked positions
**Defect:** `decrypt_310396339_19e30dcf27dc7f73_HDFC Bank Pixel Play…` returned **`"XX69"`**
where the card prints `442144-xxxxxx-6969` — the final four are **real digits**. Luna
over-applied the baseline's "replace all masked characters with X" rule. **Luna wrong,
incumbent right (`6969`).**
**Evidence:** `Card No.\n442144-xxxxxx-6969`; literal `6969` present in the PDF text.
**Change:** "where the source shows a REAL DIGIT, output that digit"; both HDFC mask forms
given side by side (`652989XXXXXX1376 → "1376"`, `442144-xxxxxx-6969 → "6969"`, explicitly
"NOT XX69"). Corpus: X-mask form in 156/281, dash-mask form in 10/281 — both real.
**Result:** cleared.

### C6 — `cardDisplayName` must never be the cardholder
**Defect (incumbent, not Luna):** CSV returned **`"SALMAN KHAN S"`** for
`decrypt_738368244…` — the cardholder's name, violating the baseline prompt's own
explicit rule. Luna returned `"UPI RuPay Credit Card"`, which the PDF prints as a page
header. **CSV wrong.**
**Change:** rule extended — the cardholder name is "printed prominently in the address
block and sometimes as a standalone row above the first transaction; it is NEVER the card
display name"; if the product is named only in a page header, use that wording.

### C7 — `network` anti-hallucination *(strengthened twice)*
**Defect:** the baseline prompt never mentions `network` (0 occurrences, confirmed).
Luna fabricated `"Mastercard"` on `decrypt_810097123_19caf4a3c2c34de6_6529XXXXXXXXXX89_01-03-2026`
where **no** network word (`visa|master|rupay|diners|amex`) appears anywhere in the PDF;
CSV correctly returned null. A v2 re-run still fabricated MASTERCARD on that statement
*and* on `decrypt_493517787_19c1fc592bdf84d1…`, both pure BIN inference.
**Corpus measurement:** a network word is printed on only 82/281 statements
(RuPay 33, Diners 27, Visa 25, Mastercard 7, Amex 0) — **null is correct on 199/281**.
**Change (v3):** BIN inference named and forbidden explicitly, including the leading-digit
heuristic ("4=Visa, 5=Mastercard, 6=RuPay/Diners is FORBIDDEN reasoning here"); a
quote-it-or-null test; and the base rate stated so null reads as the common right answer.

### C8 — fields the baseline never named
Confirmed 0 occurrences in the baseline of `network`, `issuerName`,
`totalMinimumAmountDue`, `txnType`. Added explicit guidance for each:
`issuerName` = "HDFC Bank" even under a co-brand; `totalMinimumAmountDue` ← "MINIMUM DUE"
/ "Minimum Amount Due" (present 281/281) and never derived; `totalCreditLimit` ←
"TOTAL CREDIT LIMIT (Including Cash)" not the cash limit; `availableCreditLimit` ←
"AVAILABLE CREDIT LIMIT" not "AVAILABLE CASH LIMIT" (281/281); a full HDFC-specific
`txnType` mapping table.
**Per the brief:** a baseline miss on these four is **not** a model capability failure —
the prompt never asked.

### C9 — smaller measured additions
- `rewards.openingPoints` ← "Opening Balance" (present 212/281).
- "Adjusted/Lapsed" explicitly excluded from `pointsRedeemedThisCycle` — it sits adjacent
  to `Disbursed` in the same strip and is not redemption.
- "Feature + Bonus Reward Points Earned" is ONE earned figure, not a separate bonus line
  (it must not be copied into `bonusPointsThisCycle` or split).
- `statementPeriodStart/End` ← the "Billing Period" range (HDFC's label; the baseline
  described an Axis-style "Statement Period" cell).
- `rawStatementId` — null is correct; do not repurpose the Alternate Account Number or
  CKYC ID (both present on these statements and both tempting).
- `utilisationPercent` — stated as always a computation: **0 of 281** PDFs print a
  utilisation figure.
- Ref# in parentheses is printed narration and must be kept — the incumbent truncates it
  (`decrypt_493517787…`, 4 rows; `decrypt_923692554…`, 6 rows), Luna keeps it. **Luna right.**
- Broken intra-word spacing preserved verbatim — `"S ENDEAVOUR"` is what
  `decrypt_310396339…` prints; CSV de-spaces it to `"SENDEAVOUR"`. **Luna right.**

---

## Anti-overfit note

The prompt was tuned on 10 statements and is tested on the full scoreable set — a ~28×
extrapolation. `HDFC_REPORT.md` therefore reports every metric twice: over all statements
and over the **held-out** set (all minus those 10). The 10 tuning ids are recorded in
`corpus_profile.json` → `sample` and listed in the report.

---

## 2026-08-11 — HDFC transaction-column corrections

Impact has not been measured because no inference or re-sweep was run. Every target
below is a **PREDICTION / UNVERIFIED** pending an authorised re-sweep.

- **FX billed rupee leg:** HDFC's two-line foreign-leg/billed-INR layout now requires
  the billed rupee amount with currency `INR`, never the foreign leg or a mixed pairing.
  Targets all 45 measured currency-error cells (43 USD→INR, 2 JPY→INR) and 14 of 16
  measured amount-error cells. **PREDICTION / UNVERIFIED.**
- **Description column isolation:** one positional five-column rule excludes clock time,
  standalone EMI badge, reward points, amount markers/rupee glyph, foreign leg and the
  decorative glyph from `description`, while preserving the existing bounded EMI rule.
  Targets 79 measured description cells: EMI badge 29, FX leg 21, reward points 10,
  clock time 9, credit marker 7 and rupee `C` 3. **PREDICTION / UNVERIFIED.**
- **Trailing `-NN` discrimination:** column position determines whether `-NN` remains
  narration or is excluded as reward points. Targets 26 measured description cells:
  16 narration suffixes wrongly stripped and 10 reward-point values wrongly retained.
  **PREDICTION / UNVERIFIED.**
- **Marker-first direction:** the `+` immediately before the amount (or trailing `Cr`/`CR`)
  is authoritative; signed reward points and narration wording are not credit markers.
  Targets approximately 18–23 of 39 measured Luna-wrong direction cells.
  **PREDICTION / UNVERIFIED.**

Deliberately unchanged: the working rupee-`C` glyph section; `GT_SCHEMA`; reference,
scorer and JSON artifacts; 49 GT/reference defects; 21-digit Ref# recognition; and the
36 closed-space cells better handled by scorer normalisation.

---

## 2026-08-12 — adopt the client's ORIGINAL Gemini 3.0 Flash schema; port audit

### What changed and what did not

The response schema for HDFC was switched from `GT_SCHEMA` (32 leaves) to the client's
own Gemini 3.0 Flash schema, converted to strict JSON Schema (**26 leaves**) — see
`hdfc/gemini/GEMINI_SCHEMA.json`, sha256 `35ea9019c051…`, derived mechanically from line
64 of `gemini-3-flash--prompt-shcema.txt` (source sha256 `aa286633fa77…`) by
`hdfc/gemini/convert_schema.py`. The Gemini leaf set is a strict **subset** of
`GT_SCHEMA`'s; no field exists in the Gemini schema that `GT_SCHEMA` lacks.

`HDFC_PROMPT.txt`: `08f3bbd388ee…` → `7ddbce14c6ae…` (17,413 → 20,506 bytes).
Base: `origin/main` = `d1319c0`, **which includes PR #6**. (The header table at the top
of this file still records `6cc57f69…`, the *pre*-PR #6 content hash; PR #6 appended a
section without refreshing that table. Noted so the two hashes are not mistaken for a
discrepancy.)

**The port yielded far less than expected, and that is the honest headline.** The current
HDFC prompt is already a near-superset of the generic prompt's HDFC-applicable content —
PR #6 and prior refinement absorbed it. Of the generic prompt's field guidance, 20 of 24
covered leaves were already present, 2 were rejected on measured evidence, and 4 leaves
are unguided by *both* prompts. Full inventory: `hdfc/gemini/ORPHAN_AND_GUIDANCE_AUDIT.md`.

### Changes made

- **C1 — `transactions.txnType`, wording fix forced by the schema change.**
  Was: "exactly ONE value from **the schema's** closed list". The adopted schema types
  `txnType` as a bare `string|null` and pins no enum, so that phrase became factually
  false and pointed the model at a list that no longer exists in the contract. Now reads
  "from THIS closed list", plus an explicit note that the instruction is the only thing
  enforcing the vocabulary and that null is preferred over an invented label.
  *Ported-from-generic:* **no** — the generic prompt gives no `txnType` guidance at all.
  *Classification:* correctness repair caused by adopting the client's schema.
- **C2 — `transactions.rewardPointsOnThisTransaction` + `rewards.closingPoints`:
  the Marriott Bonvoy Points column.**
  Two additions, from one measurement. The generic prompt (line 19) lists "Marriott
  Bonvoy Points" among the labels that should populate `closingPoints`. On HDFC that is
  **wrong**: on file `10378` the string `Marriott Bonvoy Points` is a *transaction table
  column header* (`DATE & TIME | TRANSACTION DESCRIPTION | Marriott Bonvoy Points |
  AMOUNT | PI`). Porting the generic clause as written would route a per-transaction
  column into a statement-level field — exactly the transaction→rewards rollup that
  `REWARDS_RULES` forbids. So instead: (a) the reward-points rule now names that column
  as transaction-level, and (b) the `closingPoints` rule now states that a
  `Marriott Bonvoy Points Summary` table is not a closing balance, citing its real shape
  on that file (only numeric column headed `BONUS POINTS`; rows `-8 pts`, `8 pts`,
  `Total 0 pts`; no headline balance printed ⇒ `closingPoints` is null).
  *Ported-from-generic:* **inverted** — the generic clause was the trigger, and the ported
  form is its correction. *Why it applies to HDFC:* the label occurs in this corpus.
- **C3 — `transactions.direction`, enforcement note.** The adopted schema drops
  `GT_SCHEMA`'s `["DEBIT","CREDIT",null]` enum, so the two-value vocabulary and its
  uppercase spelling are now prompt-enforced only. One note added saying so. The
  direction *logic* is untouched. *Ported-from-generic:* **no.**

### Rules deliberately NOT ported

- **`C` ⇒ CREDIT (generic line 8) — BANNED, and measured.** Independently reproduced on
  these 15 PDFs: `ITFRupee` in **13/15** files; **274** of **288** transaction rows carry
  an `ITFRupee` `C` before the amount; the true split from the `+`/green markers is
  **40 CREDIT / 248 DEBIT**; a bare-`C` rule would call all 274 CREDIT, **238 wrongly**,
  including **107 of 109** rows on `738368244`; and `TOTAL AMOUNT DUE` itself carries the
  same `C` (`C13,507.00`) on all 13 layout-A files. The `+` and green signals agree on
  **288/288** rows. The existing marker-first rule was kept **verbatim and not weakened**.
  (The brief's split of 41/247 did not reproduce — I measure 40/248, a one-row difference
  I could not reconcile; the probe agrees with an independent text-layer row count on
  every file and finds **zero** `Cr`/`CR`-suffix rows, so no credit marker is missed.)
- **`lastFourDigit` mask preservation (generic lines 33–36) — REJECTED for HDFC.**
  All 15 statements print the mask in the **middle** and the last four as **real digits**
  (`526873XXXXXX9821`, `442144-xxxxxx-1048`, `00361147XXXX4148`, `653029XXXXXX0012`). A
  mask-preserving instruction has no correct work to do here and risks `XX21`-style
  output — the defect ICICI suffered, which needed the *opposite* repair. Neither bank's
  resolution was copied; the decision rests on the 15 measured printed forms. The existing
  HDFC rule already resolves this correctly and is unchanged.
- **All other banks' clauses — NOT ported**, per the standing HDFC-only scoping rule:
  ICICI `closingPoints`/Adani One (41–44), Standard Chartered card ≠ account number
  (49–51), IDFC FIRST SELECT card-number source (54–56), SBI current-cycle closing points
  (57–59).
- **`eDGE REWARD POINTS`** (in the generic `closingPoints` label list) — absent from all
  15 files; it is Axis Bank's programme, not HDFC's. Not ported.
- **The HDFC bonus-points clause the brief asked for** ("does not explicitly mention
  Closing Points and only shows Bonus Points ⇒ do not aggregate") was **already present**
  at lines 155–158 before this change. Recorded as **pre-existing**, not new work.

### Orphan rules — reported, NOT acted on

Nine prompt rules govern fields the 26-leaf schema cannot emit: `financeChargesThisCycle`
(the generic prompt's entire `INFERENCE_RULES` allowlist), `rewards.bonusPointsThisCycle`,
generic line 9's sign rule, and — pre-existing in this prompt — `utilisationPercent`,
`statementPeriodStart`/`End`, `rawStatementId`, `cardCreditLimit`/`cardAvailableCreditLimit`.
No field was added to the schema (that would break comparability with the client's Gemini
baseline) and no orphan block was deleted (harmless under `strict` decoding, and the call
is yours). Recommendations per orphan are in `ORPHAN_AND_GUIDANCE_AUDIT.md` Part 1.

Correction to the brief on one point: **`utilisationPercent` is absent from `GT_SCHEMA`
too**, not just from the Gemini schema — `gt298_lib.py:24–26` documents that it is never
requested from the model and is computed in code (`gt298_lib.py:494`). There is no
Gemini-vs-GT difference on that field.

### Fields with NO guidance in either prompt

`cards.cardMeta.productFamily`, `cards.cardMeta.isPrimaryCard`,
`rewards.pointsExpiringNext30Days`, `rewards.pointsExpiringNext60Days`. Nothing was
ported (the generic prompt is silent) and no rule was invented — writing new guidance and
measuring it on the same 15 statements would be tuning toward the metric. Measured capture
is reported instead.

---

# Change — final-shape refactor + schema enums (HDFC only)

Date 2026-08-12. Committed directly to `main` on explicit human authorisation (no PR).

| | before | after |
|---|---|---|
| `HDFC_PROMPT.txt` | 278 lines / 20,506 B | 377 lines / 27,727 B |
| | sha256 `6cc57f69…` | sha256 `2d9bc1fa5fdb54704cf9eee1ab7cc579efcd3f8b63e7271af160f15dfc097ee4` |
| `gemini/GEMINI_SCHEMA.json` | 5,161 B, 26 leaves | 7,462 B, **26 leaves** |
| | | sha256 `f8aae8495dd42267dcab4cb7fca04ba0d140eb6f85cfcdc5941269c23a4d676f` |

This entry **supersedes the two "reported, NOT acted on" stances above** (orphan rules,
and the four zero-guidance fields). Both were deliberate holds pending a decision; the
decision was given, so both are now acted on.

## Part E — schema: enums + descriptions. NO field added, removed or renamed.

`enum` added to two leaves; `description` added to four. **Leaf count stays 26** and is
now asserted in code by the new `gemini/assert_schema.py`, which fails on any drift.

- `transactions[].direction` → `["DEBIT","CREDIT",null]`
- `transactions[].txnType` → the 11-value closed list **read verbatim out of
  `HDFC_PROMPT.txt`**, not invented: `PURCHASE, PAYMENT, REFUND, REVERSAL, CASHBACK, FEE,
  TAX, INTEREST, EMI, CASH_ADVANCE, UPI`, plus `null`. The prior run happened to emit only
  8 of these; the enum is the prompt's full closed list, because the 3 unused values
  (REFUND, REVERSAL, CASH_ADVANCE) are legal answers that simply did not occur in 15 files.
  Narrowing the enum to the 8 observed values would be fitting the schema to one sample.

### The strict-mode nullability risk, and how it was resolved

`response_format` is sent with `strict: true`, and every one of these leaves is NULLABLE
(the client type-map says `string|null`). An enum that omits `null` on a nullable leaf can
either 400 the call outright or — worse, because it is silent — make a correct `null`
unrepresentable and force the model to emit a wrong non-null value.

Nullability in this converted schema is expressed as a **TYPE ARRAY**
(`"type": ["string","null"]`), not `anyOf`. So the enums were built to match that form,
with `null` as an explicit enum member:

```json
"direction": { "type": ["string","null"], "enum": ["DEBIT","CREDIT",null] }
```

`assert_schema.py` enforces the biconditional `null in type  <=>  null in enum`, so this
cannot silently regress. **Smoke-tested on ONE PDF before any sweep** (statement
814964372, the smallest file, Pixel Play layout): HTTP **200**, `finish_reason=stop`,
valid JSON parsed, 2 transactions. The enum shape is accepted. No revert needed.

Residual limitation, stated rather than papered over: the smoke test proves the schema is
ACCEPTED and that non-null enum members round-trip. It does not prove a `null` is
*emittable* for `direction`/`txnType` at runtime, because that statement needed no null.
`direction` should never be null anyway (every printed row has one, and the prompt now
says so); for `txnType` the full run is the check.

`convert_schema.py` was deliberately NOT re-run: it regenerates `GEMINI_SCHEMA.json` from
the client source and would silently discard these enums and descriptions. It is
provenance, not a build step. `assert_schema.py` is the check that replaces it.

## Part A — deleted rules governing fields the schema cannot emit

Verified absent from the schema, and now absent from the prompt (re-grepped: **0**
remaining mentions of all six names):

- the whole `INFERENCE_RULES (ALLOWLIST):` section. Both subjects
  (`financeChargesThisCycle`, `utilisationPercent`) are unemittable, and the section's
  first line commanded "MUST be inferred" for a field `additionalProperties:false`
  forbids — an active instruction/schema conflict, not dead text.
- the `bonusPointsThisCycle` field rule inside `BONUS_POINTS_RULE (STRICT):`.
- the `statementPeriodStart`/`End` clause and the `rawStatementId` clause.

**The live rule inside BONUS_POINTS_RULE was NOT deleted.** Its last clause — "On HDFC the
combined label 'Feature + Bonus Reward Points Earned' is a SINGLE earned figure … It
populates pointsEarnedThisCycle. Do NOT split" — governs `pointsEarnedThisCycle`, which IS
in the schema, and is one of only four HDFC reward labels this prompt has that the client's
lacks. It was **relocated verbatim into REWARDS_RULES** and is asserted present by
`/tmp/verify_protected.py`-style string check during review.

Two consequential edits the deletions forced, both reported rather than done quietly:

1. `MISSING_DATA_RULE` said "EXCEPT for fields listed in INFERENCE_RULES, which must be
   inferred". With INFERENCE_RULES gone that is a dangling reference, so the rule is now
   unconditional: nothing in this schema is a computed field.
2. The DD/MM/YYYY sanity check bounded transaction dates by
   `statementPeriodStart`/`statementPeriodEnd`. Those field names are deleted, but the
   day/month-swap mechanism they drive contributes to date 288/288, so the check was
   **re-pointed at the statement's printed "Billing Period" range** instead of at two
   output fields. The mechanism is preserved; only the referent changed. The
   DD/MM/YYYY formatting instruction itself is byte-identical.

## Part B — rules for the four fields that had ZERO guidance anywhere

All four were previously held back on the grounds that inventing guidance and measuring it
on the same 15 statements is tuning toward the metric. That objection still stands and is
worth restating: **none of these four fields has a correctness oracle**, so every number
reported for them is POPULATED_ONLY, never accuracy. What the new rules buy is
determinism and a fabrication standard, not a measurable score.

Each rule is grounded in a probe of the 15 PDFs, not in guesswork:

- **`pointsExpiringNext30Days` / `pointsExpiringNext60Days`** — HDFC **does** print these.
  8 of 15 statements print an expiry pair; 7 (cashback, NeuCoins, Marriott Bonvoy) print
  none. Crucially, the prior run already matched that split **15/15** with zero guidance:
  it emitted `0` on exactly the 8 that print `0`, and `null` on exactly the 7 that print
  nothing. So the rule's job is to CODIFY a field that is already right, not to change it.
  It documents both printed layouts — the classic run-together
  `"POINTS EXPIRING IN 30 DAYS 0 IN 60 DAYS 0"` and the Pixel Play
  headings-row/values-row form needing positional binding (6th and 7th values) — and it
  states that a printed 0 is a real value, not a missing one.
- **`productFamily`** — every one of the 15 files does carry a product name in the text
  layer, so this is groundable. The prior run was inconsistent in GRANULARITY on the same
  product family (`Swiggy` vs `Swiggy HDFC Bank Credit Card`; `UPI RuPay` vs
  `UPI RuPay Credit Card`; and `null` on 838900283 despite the name being printed). The
  rule fixes a single deterministic form — product name minus issuer minus the words
  "Credit Card" — with seven worked examples, and forbids sourcing it from a promo block,
  a narration, the filename, or the BIN.
- **`isPrimaryCard`** — probed all 15 files for `Primary`, `Add-on`, `Add On`,
  `Additional`, `Supplementary`, `Secondary`, `Card Type`, `Cardholder Type` as card
  designations. **Not one statement prints any of them.** The only literal "PRIMARY" in the
  corpus is inside a postal address (a street named for a "PRIMARY SCHL" on 567125239) —
  exactly the kind of token a lazy rule would latch onto, so the rule names and excludes it.
  The rule therefore follows the evidence-first pattern that made `network` 16/16 with zero
  fabrications: **null unless a designation is printed.** It also blocks the four
  tempting inferences (only card on the statement, addressed to the holder, listed first,
  two cards ⇒ primary+add-on) and records that HDFC issues Pixel Play on TWO networks for
  the SAME holder (495459059: one Visa, one RuPay), so a 2-card statement is not a
  primary/add-on pair.

  **This is a semantic judgement call with no oracle, and it changes behaviour**: the
  prior run emitted `true` on 7 cards and `null` on 9; under this rule the expected result
  is `null` on all 16. By the same standard that scores a fabricated `network` as worse
  than `null`, `true` on a statement that prints no designation is a fabrication. If the
  client's ground truth instead expects `true` for the sole card on a statement, this rule
  is wrong and should be inverted — that is a contract question, not a measurement, and it
  is flagged rather than assumed.

## Part C — three live defects, one cell each

**C1 `direction`.** Coordinate-verified on statement 1723515293 page 2: the row
`Reinstating_Diff_1%_Swiggy_Cbk_Rev` at y=308.37 has amount spans in dark `0x333333` and
**no `+` span at all** ⇒ DEBIT; the model returned CREDIT. PR #6's "marker-first" wording
failed because it invited a comparison between marker and narration instead of forbidding
the narration outright.

The probe also surfaced that this defect class is **SYMMETRIC**, which the brief's C1
framing did not cover and which a one-sided fix would have made worse. The same 15-file
run has **two errors in the opposite direction** on statement 567125239: rows
`UPICC-687912822278-17-07-2025` (₹704.00, y=760.37) and `UPICC-519872895172-17-07-2025`
(₹124.00, y=774.54) both carry a `+` span at x0=526.84 in green `0x05c747` — they ARE
CREDIT — yet the model returned DEBIT, because narration semantics ("a UPI row is a
spend", reinforced by the prompt's own distribution note) overrode a **present** marker.

So the rule was made absolute in BOTH directions rather than only in the no-marker
direction: a two-step mechanical test on the printed amount, "run it, emit, and STOP",
with marker-absent ⇒ DEBIT and marker-present ⇒ CREDIT each stated as unoverridable. Had
C1 been implemented one-sidedly it would have pushed harder toward DEBIT and risked
turning 2 errors into more.

The worked examples are the real rows, chosen because they are self-refuting: the DEBIT
`Reinstating_Diff_1%_Swiggy_Cbk_Rev` sits a few rows above the CREDIT
`Reinstating_Diff_10%_Swiggy_Cashbac` (`+`, green `0x05c747`, y=336.72) **on the same
statement**. Two rows in one narration family, opposite directions, distinguished only by
the marker — which is precisely the point, and which also means the new rule must not flip
those two legitimate CREDITs. That is checked explicitly in the regression gate.

The prompt's pre-existing distribution note ("almost every row is a UPI DEBIT") is
retained but demoted in the same edit: it is now explicitly a check on the rupee-glyph
reading, "never a reason to change a row", and "not evidence about any individual row".
It is implicated in the two 567125239 errors, so leaving it unqualified would have
undercut the fix.

**C2 `rewardPointsOnThisTransaction`.** Statement 838900283, page 1, y≈643–648. The row's
spans are date (x0=169.51), narration (x0=250.74, text
`IGST-VPS2713733665720-RATE 18.0 -19 (Ref#`), then the amount at x0=531.91–536.95. There
is **no span between narration and amount** — no reward-points column value on that row —
so the field is `null`; the model emitted `-19`, lifted from inside the narration string.
This was the one field where the client's prompt beat ours (288/288 vs 287/288). The fix
is a column-positional guard: the value is a standalone number to the RIGHT of the
narration and LEFT of the amount, and characters inside the narration never populate it,
"however number-like they look". The `-RATE 18.0 -NN` form is named as the trap.

**C3 `pointsEarnedThisCycle`.** Statement 629227338 prints
`Cash Back Summary … Total C 877.70` on page 2, and the model returned
`pointsRedeemedThisCycle = 877.70` but `pointsEarnedThisCycle = null`. The gap was a
missing link, not a bad rule: the prompt asserted "for cashback cards: cashback earned =
pointsEarnedThisCycle" but never said WHERE cashback earned is printed, and listed the
Cash Back Summary only as a REDEMPTION source. The statement's own printed note resolves
it — "'Cashback Summary' will have cashback earned for the previous statement cycle" and
"will be auto redeemed against the balance for this statement cycle" — so the Total is
legitimately BOTH figures. The rule now says so explicitly, in both field entries, and
states that populating one is never a reason to null the other, which is what protects
`pointsRedeemedThisCycle` (currently 14/15 populated vs the client's 8/15). The likely
trigger for the miss is also named: on 629227338 an
`Eligible for EMI / TRANSACTIONS / TOTAL AMOUNT / CONVERT TO EMI` panel is printed
directly above the Cash Back Summary, and the rule now tells the model not to let that
panel cause it to skip the table.

## Part F — the dangling schema reference

Prompt line 4 said "strictly matching the provided schema" while no schema appears in the
prompt text. It now states that the shape arrives with the request as an API
`response_format` of type `json_schema` with `strict: true`, and that unlisted field names
are rejected.

**The full 26-leaf schema was deliberately NOT pasted into the prompt.** It would add
~6 KB to every one of the 15 calls (~+37% on a 27.7 KB prompt) for no measured benefit:
the shape is already enforced by the decoder, so the model cannot deviate from it whether
or not the text is present. A one-line pointer removes the dangling reference at ~250
bytes. If a later measurement shows the model reasoning better with the shape visible,
that is a cheap experiment to run — but it should be run, not assumed.

## DO-NOT-TOUCH sections — verified byte-identical

Extracted from `HEAD` and from the working tree by start/end marker and compared verbatim:
rupee-glyph/ITFRupee section (1,376 B), FX rupee-leg rule (394 B), column-bleed/positional
narration rules (1,868 B), txnType closed-list wording (1,136 B), DD/MM/YYYY formatting
instruction (520 B) — **all IDENTICAL**. Also grepped for banned cross-bank imports (no
country-code rule, no ICICI `NNNNXXXXXXXXNNNN` mask wording, no SBI `TRANSACTIONS FOR` or
`Amount ( \` )` wording): none present. HDFC-only scope held; `icici/` and `sbi/` untouched.

### One stale sentence left in place, on purpose

Inside the protected txnType block the prompt still says the closed list "is enforced by
THIS INSTRUCTION ONLY — the response schema types txnType as a free string and will NOT
reject an out-of-list value". Part E makes that **false**: the schema now pins an enum.
The sentence sits inside a DO-NOT-TOUCH block, and the brief says to stop and report
rather than edit one, so it was left alone and is reported here instead. It is stale, not
harmful — it understates enforcement, so it can only push the model toward compliance,
never away from it. Recommended as a one-line follow-up, not a blocker.

The equivalent sentence in the `direction` block WAS corrected, because C1 puts that block
in scope; it now states the enum pins two values plus null, and adds that `direction` is
never legitimately null since every printed row has one.

---

# Change — post-cross-review corrections (HDFC only)

Date 2026-08-12. Committed directly to `main` on explicit human authorisation. Follows an
independent codex cross-review of `e672591` / `169c5be` that returned PASS-WITH-CONCERNS,
no BLOCKING. The review independently reproduced the 26-leaf traversal, the
enum/nullability handling, direction 288/288 against the `+`/green PDF oracle, and the
widened reward metric across all three arms (new 288/288, prev 287/288, client 288/288),
confirming that metric is applied symmetrically and does not flatter the refined arm by
construction. It also independently confirmed the description regression is genuine rather
than a probe artifact, and agreed with not reverting.

## Fix 1 — the false txnType enforcement sentence

The txnType block still claimed the list "is enforced by THIS INSTRUCTION ONLY — the
response schema types txnType as a free string and will NOT reject an out-of-list value".
Part E made that **false**. The prior entry recorded it as stale-but-harmless and left it
alone because it sat inside the DO-NOT-TOUCH set; that sentence has now been put in scope
explicitly, and the reasoning for correcting it is accepted: a factually false statement
about enforcement is not defensible merely because the enum prevents invalid output.

It now states that the list is enforced by the response schema **as well as** by the
instruction, that the schema pins a strict enum of the 11 values plus null, and that the
set should be treated as genuinely closed.

Verified byte-identical, so the protected closed-list content itself did not move:

| sub-block | bytes | status |
|---|---|---|
| the closed VALUE LIST (`PURCHASE … UPI`) | 192 | **IDENTICAL** |
| all mapping bullets + the trailing `Note:` | 778 | **IDENTICAL** |

All 11 values remain present in the prompt list, matching the schema enum exactly. Only
the enforcement sentence changed.

The equivalent sentence in the `direction` block was corrected in the previous change and
**was re-checked here: it is accurate post-enum.** It says the schema pins direction to an
enum of exactly two values plus null, and the schema's enum is
`["DEBIT","CREDIT",null]`.

## Fix 2 — isPrimaryCard: reverting an evidence-first rule that was the wrong analogy

The previous entry flagged this as a semantic judgement call with no oracle and said that
if the client's truth expects `true` for the sole card, the rule was wrong and should be
inverted. It was wrong, and it is inverted here.

Measured on these 15 statements:

| | isPrimaryCard |
|---|---|
| enum-run 1 (evidence-first null rule) | **null × 16** |
| the arm before that | true × 7 / null × 9 |
| Opus-5 GT, same 15 statements | **true × 15 / false × 1** |

The error was the analogy, not the probe. `network` is a **print-transcription** field, so
"null unless the word is printed" is right for it. `isPrimaryCard` is a **semantic** field
about account structure, and no HDFC statement in this set prints a primary/add-on
designation at all — so under a print-only rule the ONLY reachable output is null, which
is how a field went from 7 populated to 0. That is materially worse, not safer.

Re-probed all 15 PDFs before rewriting, counting distinct printed card numbers:

- **14 statements list exactly ONE card account.**
- **1 statement lists TWO** (495459059: `442144-XXXXXX-1048` and `652989-XXXXXX-4493` —
  the Pixel Play product issued on a Visa BIN and a RuPay BIN to the same cardholder).
- **ZERO statements print any** `Add-on` / `Add On` / `Additional` / `Supplementary` /
  `Secondary` / `Primary Card` / `Card Type` / `Cardholder Type` designation. The only
  literal "PRIMARY" in the corpus remains the postal-address false friend on 567125239 (a
  street naming a "PRIMARY SCHL"), which the rule names and excludes.

The PDFs therefore do not contradict the new rule, so it was implemented rather than
escalated:

- exactly ONE card account → `true`, unless that card is explicitly labelled add-on /
  supplementary / secondary → `false`
- MORE THAN ONE card account → the printed per-card designation if there is one, else
  `null`; never guessed from listing order

Expected on this set: `true × 14`, `null × 2`. Note this deliberately does **not** match
GT's `true × 15 / false × 1`: GT resolves the two-card 495459059 by treating the
first-listed card as primary and the second as add-on, which is precisely the
listing-order inference the rule forbids. The residual 2-cell difference is that
disagreement, not a failure to apply the rule.

The schema `description` for this leaf was updated in the same commit, because the old
description asserted the print-only rule and would have contradicted the prompt. Leaf
count re-asserted at **26**.

**Caveat, stated because it bounds every number above:** `isPrimaryCard` has **no
correctness oracle** in the analyser — it is POPULATED_ONLY, and there is no PDF oracle
for it either. It is also **not one of the client's 16 priority fields**. The Opus-5 GT is
a strong reference but is itself a model output, not ground truth. So this change is
**convention-alignment, not demonstrated correctness**, and no accuracy claim is made for
it.

## Fix 3 — two corrections to how the previous entry described its own work

Both raised by the cross-review and both accepted:

**(a) The 'Feature + Bonus' live rule was relocated SEMANTICALLY, not verbatim.** The
previous entry said "relocated verbatim". The surviving clause preserves the load-bearing
content — that the combined label is a SINGLE earned figure which populates
`pointsEarnedThisCycle` and must not be split — but the sentence fragment instructing the
model not to "also copy it into bonusPointsThisCycle" was dropped, because
`bonusPointsThisCycle` is unemittable under the 26-leaf schema and reintroducing its name
would have reintroduced an orphan reference. Dropping it is correct; describing the
relocation as "verbatim" was not.

**(b) The DD/MM/YYYY section is NOT byte-identical.** Its central formatting instructions
are unchanged and were verified byte-identical (520 bytes), but two surrounding pieces were
contextually edited to drop unemittable fields: the section header lost
`statementPeriodStart, statementPeriodEnd` from its field list, and the billing-period
sanity check was re-pointed from those two output fields to the statement's **printed**
"Billing Period" range. The mechanism is preserved; the referent changed. The previous
entry's byte-identical claim covered the formatting instruction only, and should have said
so explicitly.

**(c) The stash label undercounts its untracked set.** `stash@{0}` says "18 modified + 14
untracked"; the true contents are **18 modified + 76 untracked** (+2,370/−747). The label
counted the top-level untracked ENTRIES shown by `git status --porcelain`, which collapses
wholly-untracked directories (`hdfc/logs/`, `sbi/var/`, `.claude/`) into one line each;
those expand to 76 files. Nothing was lost — the stash always held all 76 — but a stash
message cannot be rewritten in place, so the accurate inventory (including that
`stash@{1}` is the prior superseded ICICI report rewrite, 1 file, +437/−408, 0 untracked)
is recorded in `hdfc/STASH_NOTE.md`.
