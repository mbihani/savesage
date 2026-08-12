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
