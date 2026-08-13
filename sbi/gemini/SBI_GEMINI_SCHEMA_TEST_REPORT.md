# SBI under the client's 26-leaf Gemini schema — 3-arm measured report

**Corpus.** 12 PDFs in `~/Downloads/output/SBI/PDF`. The brief said 15; the folder holds
**12** — re-verified. All 12 appear in the client GT CSV.

**Model / call shape.** `databricks-gpt-5-6-luna`, profile `fevm-stable`, one user message,
no system message, OpenAI `file` block + `data:application/pdf;base64,…`,
`max_tokens=96000`, `reasoning_effort="medium"`, `response_format` = the converted client
schema with `strict:true`, no `model` key in the body. Filename on the wire is the neutral
`statement.pdf`, never `os.path.basename()`.

**Arms** (one schema, one model, one effort — the prompt is the only variable):

| arm | prompt | sha256 (12) |
|---|---|---|
| **A** | NEW refined `sbi/SBI_PROMPT.txt` | `6526f158e92b…` |
| **B** | PREVIOUS refined prompt, `gemini/SBI_PROMPT_PREV.txt` | `21d7c16174ba…` |
| **C** | client's unmodified generic prompt, `gemini/GEMINI_GENERIC_PROMPT.txt` | `7886583cba60…` |

**Run health.** 36/36 calls `OK`, `finish_reason=stop` on every call, **zero 429s, zero
IP-ACL blocks, zero truncations**. No record was resumed from a non-terminal state.

---

## ⚠️ What the "oracle" actually is

The comparator is the `data` blob in `remaining pdfs ground truth/sbi.csv`. Those rows
carry **`modelName: gemini-3-flash-preview` / `databricks-gemini-3-flash` and
`detectionSource: GEMINI`**. It is the **client's incumbent model output**, not
human-verified truth. Everything below is *agreement with the incumbent contract*, and
cells where the incumbent is contradicted by the PDF are called out as GT defects.
**No number here should be read as "accuracy against truth."**

---

## 1. The 5 "weak" fields — adjudication verdicts

Evidence: `probe_5fields.py`, `probe_rewards.py`, `adjudicate_5fields.py`, artefacts in
`probe/`. **Three of the five were already correct; only one was a genuine prompt defect.**

### `network` → **null was already right. This is a GT/scoring defect, not a prompt defect.**

Every VISA/MasterCard/Rupay/Amex occurrence across all 12 PDFs is boilerplate:

| boilerplate source | page | statements |
|---|---|---|
| dispute paragraph "…as per the Network (VISA, MasterCard, Rupay, Amex) Guidelines" | 2 | 12/12 |
| international-fee table "a minimum of $175 for VISA and $148 … for Mastercard" | 4–6 | 12/12 |
| "VISA Credit Card Pay" payment-option blurb | 6–7 | 11/12 |
| "NEFT, Visa Money Transfer, MasterCard MoneySend, IMPS, BBPS" channel list | 6–7 | 10/12 |

**Zero network tokens occur anywhere on page 1**, where the masked card number is printed
(card number bbox x≈317–398, y≈75–85 on all 12; nearest network token is a different page).
This is the ICICI multi-network-disclaimer trap exactly.

**IMAGE_ONLY was checked, not assumed.** The page-1 header art was rendered at 3× and
inspected visually (`probe/imgs/top_*.png`): it carries the co-brand product logo
(`CASHBACK SBI CARD`, `TATA NEUCARD Tata Neu Infinity SBI Card`) and the SBI Card logo —
**no network mark**. The only card image is a fraud-warning illustration whose card art
was zoomed 8× (`probe/imgs/cardart_zoom.png`) and shows only a faint "SBI card". So there
is **no image-only ceiling** here: there is simply no network to read.

| verdict | n | statements |
|---|---|---|
| `MATCH_NOT_PRINTED_NULL_CORRECT` | 11 | all but 221159806 |
| `GT_DEFECT` | 1 | **221159806** — incumbent says `RuPay`; the only RuPay occurrence in the entire document is the dispute boilerplate |

Corpus-wide the incumbent populates `network` on only **37/315** rows, with case-variant
values (`VISA` 23, `Visa` 8, `RUPAY` 2, `RuPay` 2, `Rupay` 1, `Mastercard` 1) — the field
is unstable in the comparator itself. **No rule was added**: any rule that produced a
network here would have to fabricate from boilerplate or from the card number, and prior
work on this project already confirmed BIN-inferred networks as fabrications.
**Ceiling on this sample is 11/12; the missing cell is unreachable without fabricating.**

### `pointsExpiringNext60Days` → **null correct 12/12. NON-DISCRIMINATING.**

No 60-day figure is printed anywhere in any of the 12 PDFs (two independent probes:
whitespace-collapsed token search and a `E\s*X\s*P\s*I\s*R` loose regex on raw page text).
12/12 both-null across all three arms and the incumbent. **This field has no oracle on
this sample and must not be reported as an accuracy figure.**

### `pointsExpiringNext30Days` → **1 real, fixable cell.**

Only **221159806** prints anything: a `Points Expiry Details` cell whose printed value is
the word **`NONE`**. The incumbent records `0`. That is a defensible reading — the label
is printed and says nothing is expiring. The other two `EXPIR` hits corpus-wide are
distractors: a T&C sentence ("NeuCoins … WILL BE FORFEITED", 1707857175) and an EMI table
column header ("Loan Expiry Date", 905768587). Rule added, scoped to fire only where the
label appears. **Confirmed live: arm A now emits `0` on 221159806.**

### `closingPoints` → **THE ONE GENUINE PROMPT DEFECT. Fixed. 3/12 → 10/12.**

The prompt asserted that SBI prints two reward tables and that the four-cell
`Previous Balance | Earned | Redeemed/Expired/Forfeited | Closing Balance` strip is "the
ONLY source". **Measured: that strip exists on 1 of 12 statements.** The real layout
inventory (geometric label binding, `probe_rewards.py`):

| page-1 block header | shape | statements |
|---|---|---|
| `SHOP & SMILE SUMMARY` | 4-cell balance strip + `Points Expiry Details` | 221159806 |
| `CARD CASHBACK SUMMARY FOR THIS STATEMENT` / `CASHBACK SUMMARY FOR THIS STATEMENT` | one figure under `CASHBACK Amount ( ` )` | 1036185244, 1511624796, 369606524, 515948911, 1118980175, 905768587 |
| `Reward Point Summary` | one figure under `Points Earned` | 1120623464, 1152718739 |
| `REWARD SUMMARY` | one figure under `Reward Points` | 393366914 |
| `NeuCoins Summary` | one figure under `NeuCoins` | 1707857175 |
| `REWARD SUMMARY` | `Current Stmt Period \| Till Last Cycle \| Earned Till Date` | 1390952698 |

On 10 of 12 statements **no closing balance is printed at all**, so the old prompt's
"null unless a balance is shown" was self-consistent — and 100% out of contract with an
incumbent that populates `closingPoints` on **314/315 rows corpus-wide**.

Root cause is a **dropped clause**, not a missing rule (see §2). Fixed; measured 3/12 → 10/12.
The 2 remaining misses are both **GT defects**, and against the PDF arm A is right on 12/12:

| statement | arm A | incumbent | verdict |
|---|---|---|---|
| **1152718739** | `12` (printed `Points Earned`) | `1879` | **GT_DEFECT** — `1879` is the `From the card issue date` LIFETIME column, which the client's *own* prompt forbids |
| **515948911** | `-1467` (printed sign) | `1467` | **GT sign flip** — the PDF prints `-1467` |

Also fixed a live model defect: **1390952698** previously emitted `closingPoints = 53724`
= `openingPoints(53724) + earned(0)`, a derivation both prompts forbid. Arm A now emits
`0`, the printed `Current Stmt Period` cell, matching the incumbent.

### `pointsRedeemedThisCycle` → **rule deliberately NOT added. Adding it would be net-negative.**

The incumbent sources this from the **`CARD CASHBACK CREDIT` transaction row**:

| statement | incumbent | printed as | our arms |
|---|---|---|---|
| 1511624796 | `782` | `CARD CASHBACK CREDIT 782.00 C` (txn row) | null |
| 515948911 | `4191` | `CARD CASHBACK CREDIT 4,191.00 C` (txn row) | null |
| 1118980175 | `-1544.5` | `CARD CASHBACK CREDIT 1,544.50 C` (txn row) | null |
| **1036185244** | **null** | `CARD CASHBACK CREDIT 390.00 C` — *identical row type* | null ✅ |
| **369606524** | **null** | `CARD CASHBACK CREDIT 424.00 C` — *identical row type* | null ✅ |

**The comparator is self-inconsistent**: it uses that row on 3 statements and ignores it
on 2 identical ones. A "cashback-credit row → pointsRedeemedThisCycle" rule would win 3
cells and **break 2 that are currently correct** (net +1) while violating "never roll up
rewards from transactions". Not applied. Instead the contradiction inside our own prompt
was resolved explicitly in the safe direction (changelog Edit 4).
The 2 remaining `0`-vs-null cells (1120623464, 393366914) are the incumbent defaulting to
`0` with no printed redeemed cell — not reachable without inventing zeros.

---

## 2. PORT_IN / DO_NOT_PORT

`gap_audit.py` counts guidance per leaf in our prompt vs the **client body, lines 1–61
only**. **Line 64 is excluded** — it is the `SCHEMA` type-map string, not guidance. This
is the HDFC trap: there, four fields looked like gaps but all four client "hits" were on
line 64 and the real gap count was zero.

### PORT_IN — the count-based list is **EMPTY**, but a clause-level read found one decisive omission

No leaf has `client_body > 0` while `ours == 0`. **But mention-counting is a coarse
instrument, and it missed the single largest defect in this whole exercise.**

`rewards.closingPoints` had **9 mentions in ours vs 7 in the client's** — it looked
*better* covered. The defect was a clause dropped *inside a sentence we had kept*:

> **Client (lines 57–59):** "For SBI cards if any closing points and cashback is not
> mentioned in the statement, 'closing points' should not be taken total cashback earned
> or total reward points earned from card issue date. **it should be how much cashback
> earned on current statement**."
>
> **Ours (before):** "…must not be taken from total cashback earned or total reward points
> earned from card issue date; **it should reflect only the current statement cycle**."

Ours kept the *prohibition* and paraphrased away the *positive instruction naming the
source*. Combined with our own added "set closingPoints = null ONLY if no numeric rewards
**balance** is explicitly shown", the model correctly concluded "no balance → null".

**PORT_IN (1 item):** restore the positive clause, bound to the measured layouts.
**Predicted impact: `closingPoints` 3/12 → ~10/12. MEASURED: 3/12 → 10/12.**

### DO_NOT_PORT

| client rule | why not |
|---|---|
| `CR, C, + as CREDIT and DR, D, - as DEBIT` | **Harmless on SBI — verified, not assumed.** No ITFRupee font in any of the 12 files, and **zero bare `C` tokens outside the direction column on page 1**; the rupee sign is a backtick (`Amount ( ` )`). On SBI `C` genuinely is the credit marker. (On HDFC this clause is catastrophic because ITFRupee maps the rupee sign to ASCII `C`.) Not ported only because our prompt already carries a stricter, marker-authoritative version that also handles `T` and `M`. |
| `Points Earned Till Date` in the `closingPoints` label list | An other-bank label. On SBI it maps to the lifetime `From the card issue date` / `Earned Till Date` column — which the client's own SBI sentence forbids, and which is exactly the incumbent's error on 1152718739. |
| ICICI / HDFC / Standard Chartered / IDFC / Marriott Bonvoy / eDGE clauses | Other-bank rules; no SBI statement matches them. |
| `Transaction Date … must not exceed the Statement Date, nor fall more than two months prior` | A deletion hazard already litigated on this project: SBI's leading-band and late-posted rows legitimately fall outside the window. Our prompt's version may only *correct* a date, never delete a row. |

### Orphan rules removed (fields the 26-leaf schema cannot emit)

`financeChargesThisCycle`, `utilisationPercent`, `bonusPointsThisCycle`,
`bigPicture.cardCreditLimit/cardAvailableCreditLimit`, `rawStatementId`,
`statementPeriodStart/End`. `gap_audit.py` now reports **zero orphan lines**.

**Two live rules were RELOCATED, not lost with their sections** (the HDFC
`BONUS_POINTS_RULE` lesson, where a naive section delete would have silently dropped a
live `pointsEarnedThisCycle` rule):
1. the transaction-date sanity check *needs* the printed statement period → the
   `"for Statement Period: <start> to <end>"` sentence is retained as an explicitly
   **non-output internal reference**;
2. `MISSING_DATA_RULE` referenced "fields listed in INFERENCE_RULES" and would have
   dangled → rewritten to the now-unconditional "nothing in this schema is ever inferred".

---

## 3. Per-field, 3-arm results

`gtPop` = statements where the incumbent is non-null. `sub` = **substantive** agreement
(both-null cells excluded), because a field that is correctly null 12/12 scores 100% and
carries zero information.

### Scalar leaves (n = 12 statements)

| leaf | gtPop | A | B | C | A-sub | flags |
|---|---|---|---|---|---|---|
| statementMeta.issuerName | 12 | 12/12 | 12/12 | 12/12 | 12 | UNEARNED, NON-DISCRIM |
| statementMeta.statementDate | 12 | 12/12 | 12/12 | 12/12 | 12 | NON-DISCRIM |
| statementMeta.dueDate | 12 | 12/12 | 12/12 | 12/12 | 12 | NON-DISCRIM |
| statementLevelSummary.totalAmountDue | 12 | 12/12 | 12/12 | 12/12 | 12 | NON-DISCRIM |
| statementLevelSummary.totalMinimumAmountDue | 12 | 12/12 | 12/12 | 12/12 | 12 | NON-DISCRIM |
| statementLevelSummary.totalCreditLimit | 12 | 12/12 | 12/12 | 12/12 | 12 | NON-DISCRIM |
| statementLevelSummary.availableCreditLimit | 12 | 12/12 | 12/12 | 12/12 | 12 | NON-DISCRIM |
| cards[].cardMeta.cardDisplayName | 12 | 7/12 | 7/12 | 7/12 | 7 | NON-DISCRIM — see note |
| cards[].cardMeta.productFamily | 10 | 2/12 | 2/12 | 2/12 | 2 | NON-DISCRIM — incoherent oracle |
| cards[].cardMeta.lastFourDigit | 12 | 12/12 | 12/12 | 12/12 | 12 | NON-DISCRIM |
| cards[].cardMeta.network | 1 | 11/12 | 11/12 | 11/12 | **0** | NON-DISCRIM — the 1 miss is a GT defect |
| cards[].cardMeta.isPrimaryCard | 12 | **12/12** | **12/12** | **2/12** | 12 | UNEARNED (all `true`) |
| rewards.programType | 12 | **11/12** | **11/12** | 9/12 | 11 | |
| rewards.openingPoints | 4 | **10/12** | 9/12 | 10/12 | 2 | |
| rewards.pointsEarnedThisCycle | 12 | **11/12** | **11/12** | 10/12 | 11 | |
| rewards.pointsRedeemedThisCycle | 6 | 7/12 | 7/12 | 7/12 | 1 | incumbent self-inconsistent |
| **rewards.closingPoints** | 12 | **10/12** | 3/12 | 3/12 | **10** | **the headline result** |
| rewards.pointsExpiringNext30Days | 1 | 12/12 | 12/12 | 12/12 | 1 | NON-DISCRIM |
| rewards.pointsExpiringNext60Days | **0** | 12/12 | 12/12 | 12/12 | 0 | **NO ORACLE**, UNEARNED, NON-DISCRIM |

**`cardDisplayName` 7/12 is a floor, not a ceiling.** On 4 statements the incumbent puts
the **cardholder's name** there (`BHAVANA KS`, `SHUBHAM DESHMUKH`, `BIPIN PATEL`,
`ARIHANT KATARIYA`) while our arms return the product name, which our prompt explicitly
requires. Those 4 are GT defects; adjusted, all three arms are ~11/12.

### Transaction leaves (matched rows)

| leaf | rows | gtPop | A | B | C |
|---|---|---|---|---|---|
| date | 189 | 189 | 188/189 = 99.5% | 190/190 = 100% | 191/191 = 100% |
| description | 189 | 189 | 189/189 = 100% | 190/190 = 100% | 191/191 = 100% |
| amount | 189 | 189 | **189/189 = 100%** | 100% | 100% |
| direction | 189 | 189 | **189/189 = 100%** | 100% | 100% |
| txnType | 189 | **55** | 55/189 = 29.1% | 55/190 = 28.9% | 57/191 = 29.8% |
| rewardPointsOnThisTransaction | 189 | **0** | 100% | 100% | 100% (**NO ORACLE**) |
| currency | 189 | 189 | **189/189 = 100%** | **100%** | 184/191 = 96.3% |

**`txnType` needs the substantive split — the raw percentage is misleading.** The
incumbent leaves `txnType` null on **134 of 189** rows, so raw agreement is dominated by
null-vs-null. On the 55 cells the incumbent *does* populate:
**A = 55/55 (100%), B = 55/55 (100%), C = 55/55 (100%)**. All three arms are perfect where
there is an oracle; the raw figure only measures how willing each arm is to emit a type
where the incumbent declined to.

### Leaves with NO oracle on this sample — reported as populated-only, never as accuracy

| leaf | incumbent | A populated | B | C |
|---|---|---|---|---|
| rewards.pointsExpiringNext60Days | null 12/12 | 0 | 0 | 0 |
| transactions[].rewardPointsOnThisTransaction | null 189/189 | 0 | 0 | 0 |

2 of 26 leaves have no oracle here (HDFC had 11 of 26). A further 12 leaves are
**NON-DISCRIMINATING** (identical across all three arms) and 2 are **UNEARNED**
(`issuerName` all `SBI Card`; `isPrimaryCard` all `true`).

---

## 4. ⚠️ REGRESSION GATE

### One flagged regression: `transactions[].date`, A 188/189 vs B 190/190 — **ONE cell, and it is sampling noise**

The cell is **905768587**, row `IGST DB @ 18.00%` (₹190.74): arm A emitted `date = null`,
the incumbent has `03 Jun 26`. This is the undated tax-continuation row.

**Tested with repeats rather than assumed.** Three fresh arm-A calls on 905768587:

| repeat | IGST row date |
|---|---|
| 1 | `03/06/2026` ✅ |
| 2 | `03/06/2026` ✅ |
| 3 | `null` ❌ |

**2 of 3 correct → non-deterministic on an inherently ambiguous row, not a reproducible
effect of the prompt edit. No revert recommended.**

### Not flagged by the gate but reported anyway: row fidelity

| arm | incumbent rows | arm rows | matched | row fidelity |
|---|---|---|---|---|
| A | 193 | 191 | 189 | 97.9% |
| B | 193 | 192 | 190 | 98.4% |
| C | 193 | **193** | 191 | **99.0%** |

`description` is scored on **matched** rows, and rows are matched *by* description — so a
mistranscribed description cannot match and is invisible to that score. Row fidelity is
reported alongside it so the 100% figures are not read as complete.

Two distinct causes, both tested with repeats:

1. **The 71-row statement 1707857175.** A = 69 rows, B = 70, C = **71** (correct).
   Arm A repeats: 70, 69, 70. **Both refined arms reproducibly drop 1–2 rows on the
   longest statement while the client's shorter prompt gets all 71.** This is
   **pre-existing (B is affected too), not caused by this edit**, and A's central
   tendency (~70) is within variance of B. **Recommendation: investigate as follow-up —
   our longer prompt appears to cost completeness on long tables. Do not revert the
   `closingPoints` fix for it.**
2. **`Cashfree*FLIPKART INTE` on 369606524.** The PDF prints `INTE`; arm A truncated to
   `IN` in 3/3 repeats. But **arm B is only 1/3 correct on the same row and arm C also
   truncates** — so this is a model-level transcription weakness across all arms, **not a
   regression from this edit**.

### A measurement defect found and fixed mid-analysis (not a model finding)

The first version of `analyse.py` matched transaction rows **greedily**. SBI repeats the
same description at the same amount on many dates (1707857175 has a long run of
`UPI-REDEFINED PRIVATE L` at 20.00); when one arm drops a row the whole run shifts by one,
manufacturing a cascade of fake date mismatches. It reported **arm A at 93.8% on `date`
and a false `description` regression**. Replaced with order-preserving LCS alignment keyed
on `(amount, description)` — the date is deliberately excluded from the key, since it is
under measurement. The 93.8% figure was an artefact and never a real result.

Similarly, an earlier probe pass searched for numbers in whitespace-collapsed text.
Collapsing destroys numeric token boundaries (`12 720 1879` → `127201879`), so a
word-bounded search for `1879` reported NOT PRINTED for a figure that **is** printed — a
**false accusation of the ground truth**, caught and corrected. Numbers are now matched
against the word-token list; collapsed text is used only for alphabetic labels, where
mid-word line-wrap is the real hazard.

---

## 5. Tokens

No dollar figures: Luna's rate is unpublished and must not be interpolated from a sibling
model.

| arm | calls | prompt | completion | reasoning | total | cached |
|---|---|---|---|---|---|---|
| A | 12 | 241,134 | 16,460 | 4,590 | 257,594 | 20,224 (8.39%) |
| B | 12 | 229,926 | 18,445 | 6,471 | 248,371 | 0 (0.0%) |
| C | 12 | 200,274 | 17,376 | 5,702 | 217,650 | 0 (0.0%) |

**Verified per call, not assumed:** `prompt + completion == total` on **all 36 calls**, and
`reasoning_tokens <= completion_tokens` on **all 36 calls** — i.e. reasoning is **inside**
completion, the OpenAI convention. Getting this backwards would be a ~30% error.

Two honest caveats:
- **Arm A's 8.39% cached figure is an artefact of this session's repeat testing**, not a
  property of the arm: the repeat calls re-sent an identical prompt prefix. The clean
  single-pass arms both show **0% implicit cache hits**, so the ~4.9% historical SBI cache
  rate is **not reproduced here**.
- `prompt_tokens` clusters tightly around 20,000 for arms A/B and ~16,700 for arm C,
  including a call at exactly `20000`. The value looks **quantised** for the PDF
  attachment rather than a true token count. Reported as observed.

Arm A costs ~4.8% more prompt tokens than B and ~20% more than C (a longer prompt), and
**~11% fewer completion tokens and ~29% less reasoning than B** — the explicit layout
enumeration appears to reduce deliberation. Unverified as a causal claim.

---

## 6. Schema

`GEMINI_SCHEMA.json` = the client's line-64 type map converted by `convert_schema.py`
(AST-parsed, never regex), then tightened by `patch_schema.py`. **26 leaves, unchanged —
nothing added or removed.** Nullability comes out as a **type array**
(`"type": ["string","null"]`), not `anyOf`.

Added, and nothing else:
- `enum` on `transactions[].direction` → `["DEBIT","CREDIT",null]`
- `enum` on `transactions[].txnType` → the 11-value vocabulary **+ `null`**, mirroring the
  prompt verbatim. `CASH_ADVANCE` is included although this sample never emits it —
  narrowing the enum to a 12-file sample would bake the sample into the contract.
- `description` on the **4 leaves with zero guidance from both prompts**:
  `productFamily`, `txnType`, `pointsExpiringNext30Days`, `pointsExpiringNext60Days`.

**Enum safety under `strict:true`.** Both enumerated leaves are nullable, so **`null` is a
member of both enums**. Omitting it would make a correct null unrepresentable — either a
hard 400 on every call or a forced wrong non-null, which is worse than the problem being
fixed. **Smoke-tested on exactly one PDF (221159806) before the 12-file run: `OK`,
`finish_reason=stop`, no 400.** Enums are safely expressible here; no revert needed.

`assert_schema.py` enforces leaf count == 26, no path/type drift, only `enum`/`description`
ever added, and the biconditional **(null in type) ⇔ (null in enum)**. It exits nonzero on
violation, and was **negative-tested in both directions**: enum-omits-null → exit 1;
field-removed → exit 1.

---

## 7. UNVERIFIED / limits of this measurement

- **Every figure here is 12 statements.** Nothing is extrapolated to the 300-statement SBI
  corpus. A single cell is 8.3 percentage points.
- **The comparator is Gemini-3-Flash output, not human truth.** "Agreement" ≠ accuracy.
  At least **7 incumbent cells are contradicted by the PDF** (network on 221159806;
  closingPoints on 1152718739 and 515948911; cardDisplayName on 4 statements), so the
  true ceiling is above every number in §3.
- **`closingPoints` 10/12 is measured; the corpus-wide effect is not.** The incumbent
  populates this field on 314/315 rows, so the same defect plausibly affected most of the
  300-statement corpus — but that is **UNVERIFIED** and would need a full re-run.
- **`pointsExpiringNext30Days` rests on one cell.** Corpus-wide the incumbent populates it
  on 79/315 and `…60Days` on 41/315, while no PDF in this sample prints a numeric expiry.
  Whether those 79/41 are other layouts or fabrications is **UNVERIFIED**.
- **`productFamily` has an incoherent oracle** (`CASHBACK` vs `CASHBACK SBI CARD` vs
  `Platinum` vs null for equivalent statements). Its 2/12 is not a meaningful score, and
  the new schema description is **untested against any stable contract**.
- **The `M` direction marker change is documentation, not repair** — arm A and the prior
  output already produced `DEBIT` on those rows via the fall-through rule. Zero measured
  gain. `FP`/`EN` markers were reported by the brief but **do not occur** in these 12.
- **Row-completeness on long statements is a real open issue** (§4, cause 1) and is
  **worse in both refined arms than in the client's prompt**. Not fixed here.
- `pointsRedeemedThisCycle` stays at 7/12 across all arms. **This edit produced no gain
  there, and that was the deliberate choice** — the available rule was net-negative.
- The three-arm design isolates the prompt, but **each arm is a single sample per
  statement**. Where a delta was small it was repeat-tested (§4); deltas not repeat-tested
  should be assumed to carry run-to-run variance of ~1–2 cells.
