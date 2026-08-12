# Orphan-rule audit + field-guidance inventory

Source of the generic prompt and schema: `/Users/mayanck.bihani/Downloads/gemini-3-flash--prompt-shcema.txt`,
sha256 `aa286633fa774131b2ae1c30020235829d7948249408875005931fef83459459`
(prompt = lines 1–61, schema type map = line 64). Extracted by `ast.literal_eval`, not regex.

Baseline HDFC prompt compared against: `origin/main` = `d1319c0`, blob `7dba4a19` for
`hdfc/HDFC_PROMPT.txt`. **This INCLUDES PR #6** ("Fix HDFC transaction column extraction
rules", merged 2026-08-11T07:48:36Z). Note the local checkout's `main` was stale at
`b2cf196` and its `docs/refresh-bank-eval-reports` branch carries a *different*
`HDFC_PROMPT.txt` (blob `41f8f788`) that does **not** contain PR #6; that branch was not
used as the base.

---

## Part 1 — Orphan rules: guidance for fields the adopted schema cannot emit

The converted Gemini schema has `additionalProperties: false` at every object level and
`strict: true`, so a field it does not name is **unrepresentable in the response**. Any
prompt rule governing such a field is dead weight at best.

### 1a. Orphans in the CLIENT'S GENERIC PROMPT

| # | Field | Rule (source lines) | In schema? | Recommendation |
|---|---|---|---|---|
| O1 | `financeChargesThisCycle` | `INFERENCE_RULES (ALLOWLIST)`, lines 23–25: "The following fields MUST be inferred or derived when not explicitly stated: financeChargesThisCycle" + the three-way inference ladder (stated → use it; explicitly none → 0; undeterminable → null) | **No** | **Drop from the HDFC prompt as dead weight** — but only in a change that *also* drops it from the schema-side contract. See note below. |
| O2 | `rewards->bonusPointsThisCycle` | `BONUS_POINTS_RULE (STRICT)`, line 28: populate ONLY on an explicit distinct "bonus points" field; otherwise null and never inferred/derived/split/reclassified | **No** | **Keep the rule's *negative* half, drop the *positive* half.** See below — this one is not purely dead. |
| O3 | every non-transaction amount | Line 9: "For any Amount except for transactions->\"amount\" ... if it is a credit Transaction then add - before that Amount." | Partially orphaned | **Drop as unreachable.** |

**O1 detail.** `financeChargesThisCycle` is the *only* member of the generic prompt's
`INFERENCE_RULES` allowlist. Under the adopted schema the allowlist is therefore empty,
which also makes `MISSING_DATA_RULE`'s clause "EXCEPT for fields listed in
INFERENCE_RULES" vacuous. Recommendation: drop O1's rule text, and simplify
`MISSING_DATA_RULE` to the unconditional "not present ⇒ null". I have **not** made this
edit — dropping it is a scope decision for you, and the current HDFC prompt carries the
same rule at lines 160–165.

**O2 detail — why this one is NOT purely dead weight.** The field cannot be emitted, so
the "populate it when…" half is unreachable. But the rule's *prohibition* half does real
work on a schema-adjacent field: it forbids bonus points being "inferred, derived, split,
or reclassified from `pointsEarnedThisCycle`, `pointsRedeemedThisCycle`, adjustments,
transfers, or transaction-level reward points" — and `pointsEarnedThisCycle` **is** in the
schema. Measured relevance: file `10378` prints a table headed `Marriott Bonvoy Points
Summary` whose only numeric column is literally `BONUS POINTS` (values `-8 pts`, `8 pts`,
`Total 0 pts`) and prints **no** closing balance. With the positive half removed and no
prohibition retained, those bonus figures have nowhere legitimate to go and the nearest
schema field is `pointsEarnedThisCycle`. So the recommendation is to retain a one-line
prohibition and drop only the unreachable populate-instruction.

**O3 detail.** With `financeChargesThisCycle` gone, the only non-transaction amounts left
in the schema are the four `statementLevelSummary` figures, none of which is "a credit
transaction". The rule has no reachable target and should go. (It survives verbatim at
`HDFC_PROMPT.txt:30`.) One caveat worth stating: `totalAmountDue` **can** legitimately be
negative on this corpus — files `1741303904` and `809843802` print a negative total
amount due (credit balance). That negativity comes from the printed figure itself, not
from applying O3, so dropping O3 does not endanger it.

### 1b. Orphans ALREADY IN the current `HDFC_PROMPT.txt` (pre-existing, wider than 1a)

Adopting the 26-leaf schema orphans more of the HDFC prompt than it orphans of the
generic prompt, because the HDFC prompt was written against the 32-leaf `GT_SCHEMA`.

| # | Field | HDFC_PROMPT lines | In Gemini schema? |
|---|---|---|---|
| O4 | `financeChargesThisCycle` | 160–165 | No |
| O5 | `utilisationPercent` | 166–170 | No — **and not in `GT_SCHEMA` either** |
| O6 | `rewards.bonusPointsThisCycle` | 176–184 | No |
| O7 | `statementMeta.statementPeriodStart` / `statementPeriodEnd` | 253–255 | No |
| O8 | `statementMeta.rawStatementId` | 256–259 | No |
| O9 | `cardCreditLimit` / `cardLevelTotalAmountDue` | 168–169 (inside O5) | No |

**O5 is worth separating out.** The brief states our `GT_SCHEMA` contains
`utilisationPercent` and the Gemini schema's omission of it is a difference to report.
**That is not the case.** `utilisationPercent` is absent from *both* schemas:
`gt298_lib.py:24–26` documents that it "is NOT requested from the model … and is computed
in code by `add_utilisation298.py`", and `gt298_lib.py:494` holds the code that computes
it. So there is no Gemini-vs-GT difference here at all, and the brief's instruction "do
NOT add it" is satisfied automatically. The `HDFC_PROMPT.txt:166–170` block that asks the
model to *compute* utilisation has therefore been orphaned since before this change.

**Recommendation for O4–O9:** drop all six blocks *if and only if* you commit to the
26-leaf schema for HDFC going forward. I have deliberately **not** dropped them in this
PR, for two reasons: (i) the brief's instruction is to recommend rather than act, and
(ii) they are harmless under `strict` decoding — the model cannot emit the fields
regardless — so leaving them costs prompt tokens and reader confusion but not accuracy.
The one I would drop first regardless is O5, which is stale against both schemas.

### 1c. Explicitly NOT recommended: adding any field to the schema

For every orphan above, the alternative repair is to add the field to the schema. **I
recommend against it for all of them.** The purpose of adopting the client's schema is
comparability with their Gemini baseline; a schema with fields their baseline never had
produces numbers that cannot be set beside theirs. If any of these fields are wanted in
production, that is a separate change to the *production* schema, measured separately.

---

## Part 2 — Complete field-guidance inventory of the generic prompt

Every field for which the generic prompt provides description or guidance, and whether
the current `HDFC_PROMPT.txt` already covers it.

| Schema leaf | Generic prompt guidance (lines) | Already in HDFC_PROMPT? | Action |
|---|---|---|---|
| *(all fields)* | always output all; null when missing (5); MISSING_DATA_RULE (27–28); not-a-credit-card ⇒ all null (45) | Yes (6, 172–174, 244) | none |
| `transactions.direction` | **"CR, C, + as CREDIT and DR, D, - as DEBIT" (8)** | Yes — a **stronger, marker-first** rule (73–93) | **REJECTED — see Part 3** |
| `transactions.amount` | positivity implied; sign rule for other amounts (9) | Yes (30–31) | none |
| `transactions[]` | extract every transaction exactly as shown (10); no infer/fabricate/aggregate (11) | Yes (32, 64–67) | none |
| `transactions.rewardPointsOnThisTransaction` | informational only, never used outside the txn object (12); "Cashback Credit" caveat (21) | Yes (68–72, 144–145) | **augmented — C2** |
| `transactions.date` | logically bounded: ≤ statementDate, ≥ 2 months prior (60–61) | Yes (240) | none |
| `transactions.description` | "exactly as shown" only (10) | Yes, far more specific (33–63) | none |
| `transactions.txnType` | **none at all** — no vocabulary is named | Yes, 11-value list (106–122) | **C1 — wording fix forced by schema** |
| `transactions.currency` | none | Yes (94–105) | none |
| `statementMeta.dueDate` | non-date text preserved verbatim (30–32) | Yes (212–213) | none |
| `statementMeta.statementDate` | referenced only as a bound (60–61) | partially (245–253) | none |
| `statementMeta.issuerName` | none | Yes (187–190) | none |
| `statementLevelSummary.availableCreditLimit` | explicit label only; never compute by subtraction (37–40) | Yes (232–234) | none |
| `statementLevelSummary.totalAmountDue` / `totalMinimumAmountDue` / `totalCreditLimit` | none | Yes (191–197) | none |
| `cards.cardMeta.lastFourDigit` | **preserve masking exactly** (33–36); card ≠ account number, SCB (49–51); IDFC source (54–56); must match the points-bearing card (52–53) | Yes — the **opposite** resolution (221–231) | **REJECTED — see Part 4** |
| `cards.cardMeta.cardDisplayName` | none | Yes (214–220) | none |
| `cards.cardMeta.network` | none | Yes (198–209) | none |
| `cards.cardMeta.productFamily` | **none** | **No** | none — flagged, see Part 5 |
| `cards.cardMeta.isPrimaryCard` | **none** | **No** | none — flagged, see Part 5 |
| `rewards.programType` | identify first (18); points over cashback (48); cashback mapping (21) | Yes (129–134, 241) | none |
| `rewards.closingPoints` | explicit numeric balance + label list (19–20); negative allowed (20); ICICI (41–44); HDFC bonus-only (46–47); SBI (57–59) | Yes (135–140, 155–158) | **augmented — C2** |
| `rewards.pointsEarnedThisCycle` / `pointsRedeemedThisCycle` | cashback mapping (21); never rolled up from transactions (15–17) | Yes (125–128, 148–154) | none |
| `rewards.openingPoints` | none | Yes (146–147) | none |
| `rewards.pointsExpiringNext30Days` / `Next60Days` | **none** | **No** | none — flagged, see Part 5 |

### Bank-specific clauses in the generic prompt — NONE ported

Per the standing scoping requirement that `HDFC_PROMPT.txt` contain only HDFC-justified
rules:

| Clause | Lines | Bank | Ported? |
|---|---|---|---|
| `closingPoints` not computed from earned/redeemed; "Earnings transferred to Adani One" is redemption | 41–44 | ICICI | **No** |
| Credit Card Number ≠ Credit Card Account Number | 49–51 | Standard Chartered | **No** |
| Card number from "Statement Summary", not the transactions section | 54–56 | IDFC FIRST SELECT | **No** |
| Closing points = current cycle only, not since card issue | 57–59 | SBI | **No** |
| Bonus-points-only ⇒ do not aggregate into closing points | 46–47 | **HDFC** | **Already present** at `HDFC_PROMPT.txt:155–158` — pre-existing, not newly ported |

The one HDFC-specific clause the brief asked to be ported was already in the prompt
before this change. Recorded as pre-existing rather than claimed as new work.

---

## Part 3 — The `C` ⇒ CREDIT clause: measured, and REJECTED

Generic prompt line 8 reads *"Classify debit/credit using CR, C, + as CREDIT and DR, D, -
as DEBIT."* The bare-`C` half was **not** ported. Independently measured on these 15 PDFs
by `verify_itfrupee.py` (raw output in `itfrupee_verification.json`):

| Measurement | Result |
|---|---|
| PDFs embedding a font named `ITFRupee` | **13 of 15** (both "Pixel Play" files: 0) |
| Transaction rows, geometric extraction | **288** |
| Same count from an independent text-layer date-anchor count | **288** (delta 0 on every file) |
| Rows whose amount is preceded by an `ITFRupee` `C` | **274** |
| TRUE split from `+`/green markers | **40 CREDIT / 248 DEBIT** |
| Rows where the `+` signal and the green signal disagree | **0 of 288** |
| Rows a bare-`C` ⇒ CREDIT rule would call CREDIT | **274** |
| …of which **WRONG** | **238** |
| Worst single file (`738368244`) | **107 of 109** rows flipped |
| `TOTAL AMOUNT DUE` headline carrying the same `C` | **13 of 13** layout-A files, e.g. `C13,507.00` |

Mechanism, confirmed at span level: the rupee sign is a **separate span** whose font is
`ITFRupee` and whose text is the single character `C` — the font maps the rupee glyph onto
code point 0x43. So `C13,507.00` means ₹13,507.00. The `TOTAL AMOUNT DUE` figure carries
that same `C` on every layout-A file, which by itself falsifies "C means credit": a total
amount due is never a credit.

HDFC's real credit markers, both present and in perfect agreement on 288/288 rows:
a `+` span before the amount, and green (`0x05c747` layout A, `0x07bf7d` Pixel Play).

**Reconciliation with the brief.** The brief's 13/15 ITFRupee, 274 marker-bearing rows,
"107 of 109 on one statement", and the `C13,507.00` total-amount-due example all
reproduce **exactly**. The brief's split of *41 CREDIT / 247 DEBIT* does not: I measure
**40 / 248** over the same 288 rows — a **one-row** difference I could not reconcile. My
probe is self-consistent (288 == 288 against an independent counter, zero `+`/green
disagreements, and **zero** `Cr`/`CR`-suffix rows anywhere in the corpus, so no credit
marker is being missed). The direction of the finding is unaffected: 238 of 274
`C`-bearing rows are DEBIT.

The current HDFC prompt's marker-first rule (lines 73–93) was **kept verbatim and not
weakened**. It already states that a leading `C` is the rupee sign and carries no
direction information, that a signed reward-points value is not the credit marker, and
that narration wording never overrides the printed marker. Nothing about direction was
ported from the generic prompt.

One incidental measurement: the `Cr`/`CR` **suffix** marker, which the HDFC prompt lists
as a legal credit marker alongside `+`, occurs **zero** times in all 15 files. It is
therefore not load-bearing on this corpus. I left it in place — it is harmless and may
matter on other HDFC statements — but no measurement here supports or refutes it.

---

## Part 4 — `lastFourDigit`: mask-preserving rule REJECTED for HDFC, with evidence

Generic prompt lines 33–36 require masking be preserved exactly
(`XXXXXXX56`/`xxxxxxx56` → `"xx56"`, `******56` → `"**56"`).

Measured on all 15 files (`probe/evidence_fields.py`) — every statement prints the card
number with the **middle** masked and the **last four as real digits**:

| Statement | Printed form | Last four |
|---|---|---|
| `1036474356` | `526873XXXXXX9821` | `9821` real |
| `10378` | `00361147XXXX4148` | `4148` real |
| `1403225883` | `434155XXXXXX9503` | `9503` real |
| `495459059` (Pixel Play) | `442144-xxxxxx-1048` | `1048` real |
| `814964372` (Pixel Play) | `442144-xxxxxx-6463` | `6463` real |
| `567125239` | `653029XXXXXX0012` | `0012` real (leading zero) |
| … | *(same pattern in all 15)* | **15/15 real** |

**Decision: do not port.** The mask never falls in the last four positions on HDFC, so a
"preserve the masking" instruction has no correct work to do here — while carrying a live
risk of the model seeing the X-run and emitting `XX21`-style output. That is precisely the
defect the brief reports ICICI suffered (`XX02` where real digits existed), and ICICI
needed the *opposite* repair. Neither bank's resolution was copied: the decision here
rests on the 15 measured printed forms above.

The current HDFC rule (lines 221–231) is already the correct resolution and is kept
unchanged — it says output the real digit where the source shows one, substitute `X` only
for a position that is itself masked, and gives `442144-xxxxxx-6969 → "6969"` (NOT
`"XX69"`) as a worked example. It also already handles the leading-zero case (`0576` →
`"0576"`), which file `567125239` (`…0012`) exercises for real.

---

## Part 5 — Schema fields with NO guidance in EITHER prompt

Four leaves are unguided by the generic prompt *and* by the current HDFC prompt:

- `cards.cardMeta.productFamily`
- `cards.cardMeta.isPrimaryCard`
- `rewards.pointsExpiringNext30Days`
- `rewards.pointsExpiringNext60Days`

Nothing was **ported** for these — the brief's task is to port *from* the generic prompt,
and the generic prompt says nothing about them. I also did not invent rules for them:
writing new guidance and then measuring it on the same 15 statements would be tuning
toward a metric, and any gain would be unattributable. Their measured capture is reported
in the per-field analysis instead, so the decision whether they need guidance can be made
on evidence in a later, separately-measured change.
