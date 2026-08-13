# SBI prompt changelog — every change tied to a measured defect

## 2026-08-13 — row completeness on long tables: statement 1707857175, the 28 Apr cluster

**Defect.** Statement **1707857175** prints **71** transaction rows. The refined prompt
emitted 70 (sometimes 69). All 11 other sample statements were unaffected, so this is a
long-table defect, not a general one.

The row count was established from the PDF itself, not from any model output
(`gemini/pdf_rowtruth.py`, span geometry: a row is a line whose rightmost span is a lone
`C`/`D`/`T`/`M` inside the page's own learned marker band, carrying an Indian-grouped
amount). That probe independently reproduces the arms' counts on **11 of 12** statements,
which is what licenses trusting it on the twelfth. It says 71; the client's generic prompt
(arm C) was right and our arms were dropping a genuine printed row.

**The 28 Apr 2026 cluster, with printed y-positions.** Four rows carry that date, and the
decisive fact is that an *exact duplicate pair straddles the page break*:

| idx | page | y | date | amount | mk | description |
|-----|------|---------|------------|---------|---|-------------------------------|
| 29 | 1 | 818.71 | 28/04/2026 | 40.00 | D | `UPI-SHASHANK VISHNUPANTNSE` |
| 30 | 1 | 830.52 | 28/04/2026 | 20.00 | D | `UPI-REDEFINED PRIVATE L` |
| 31 | 2 | 130.09 | 28/04/2026 | 20.00 | D | `UPI-REDEFINED PRIVATE L` |
| 32 | 2 | 141.89 | 28/04/2026 | 1750.00 | D | `UPI-SHAUKEEN ENTERPRISE` |

idx30 is the **last** row of page 1 and idx31 is the **first** row of page 2, byte-identical
to it. `UPI-REDEFINED PRIVATE L` at exactly 20.00 recurs dozens of times in this one
statement, so the corpus is saturated with this shape.

**The failure was not a clean drop.** Diffing against the PDF (not against arm C) with
normalised dates and `Counter` multiplicity shows the pre-fix prompt failing three
different ways across repeats, all localised to idx29–31:

* drop one of the identical 20.00 rows (→ 70);
* drop the 40.00 *and* one 20.00 (→ 69);
* emit a **row that the PDF never prints** — `28/04/2026  20.00  UPI-SHASHANK
  VISHNUPANTNSE`, i.e. the description of idx29 bound to the amount of idx30/31 — while
  dropping the real 40.00. This one **still counts 71**.

That last mode matters: **row count hides the defect.** The pre-fix prompt hit n=71 on
8 of 12 samples but was row-*exact* on only **4 of 12**. The mechanism is row-boundary
misalignment in a dense same-date cluster at a page break, not de-duplication alone.

**Isolation experiment** (`gemini/ablate_rowcount.py`, 24 calls on this one statement,
3–12 repeats per variant, each variant differing from the current prompt by exactly one
excision or addition). Competing hypotheses were tested and **both are ruled out**:

| variant | Δ prompt | n per repeat | row-exact |
|---------|----------|--------------|-----------|
| base (current prompt) | — | 71,70,70,71,71,71,69,71,71,70,71,71 | **4/12** |
| `nodate` (minus statement-period / date sanity-check rules) | −731 ch | 71,70,71 | no better |
| `noband` (minus leading-band + `TRANSACTIONS FOR` rules) | −536 ch | 70,71,71 | no better |
| `norewards` (minus the whole REWARDS_RULES block) | **−7433 ch (−36%)** | 70,71,70 | no better |
| `fix` (base + the new rule) | +770 ch | 71×12 | **11/12** |

* **Hypothesis (b), a specific rule suppressing the row: NOT SUPPORTED.** No single rule
  excision restores the row. (Consistent with arm C carrying its own, stricter, date-bound
  rule — "must not exceed the Statement Date" — while still emitting all 71.)
* **Hypothesis (a), prompt length / attention dilution: NOT SUPPORTED.** Deleting 36% of
  the prompt — a block that cannot legitimately affect whether a transaction row is
  emitted — left the defect exactly where it was. **No prompt shortening was performed**,
  so none of the measured rewards wins were put at risk.
* **Hypothesis (c), output truncation: RULED OUT WITH DATA.** `finish_reason == "stop"`
  and `prompt_tokens + completion_tokens == total_tokens` on every sample;
  completion was ~3.7–4.9k tokens against `max_tokens` 96000.

Row-exactness, pre-fix 4/12 vs post-fix 11/12: **Fisher exact one-sided p = 0.0047**
(p = 0.0006 pooling the 4 earlier pre-fix samples). On row count alone, 8/12 vs 12/12
gives p = 0.047 — a reminder that the weaker metric understates the change.

**The change.** One positive, SBI-scoped bullet added to `TRANSACTION RULES`, immediately
after the existing `COMPLETENESS IS MANDATORY` bullet: consecutive rows identical in date,
amount and description are separate genuine payments and must each be emitted; this holds
across a page break; each amount stays bound to the description printed on its own line;
the emitted count must equal the printed count. Nothing was removed, no schema field
changed (26 leaves, `gemini/assert_schema.py` passes), and no HDFC/ICICI wording,
glyph rule or mask rule was imported.

**Honesty bounds.** Measured on **12 statements only**; not extrapolated to the ~300-statement
SBI corpus. `fix` was row-exact 11/12, not 12/12 — one repeat still produced the hybrid
row, so this reduces the defect rate, it does not prove elimination.

## 2026-08-13 — correct rewards flows mis-slotted as `closingPoints`

Re-adjudication of the 12-statement Gemini sample found that the shipped prompt treated
current-statement accruals as closing balances. Only **221159806** prints a genuine
balance strip: page 1 `SHOP & SMILE SUMMARY` at `(x=175.0,y=362.2)`, with `Previous
Balance` `(27.6,391.7)=18068` `(36.8,405.1)`, `Earned` `(107.9,391.7)=0`
`(112.6,405.1)`, `Redeemed/Expired/Forfeited` `(160.3,387.0)=0` `(179.3,405.1)`, and
`Closing Balance` `(229.6,391.7)=18068` `(238.5,405.1)`. The other 11 page-1 rewards
blocks print flows, so their PDF-correct `closingPoints` is null.

The prompt now binds the later `SAVINGS AND BENEFITS SECTION` geometrically: row labels
at `x≈37`, column headers at `y≈83`, and only the `x≈250` cell under `For this statement`
is current-cycle. It expressly rejects `x≈369` (`For this year`) and `x≈497` (`From the
card issue date`). This prevents **1152718739** `Reward Points` `(x=37.4,y=121.7)` from
mis-binding `12 | 720 | 1879` `(x=244.7/370.4/497.6,y=121.7)`, and **221159806** from
misusing lifetime `18068` `(x=495.3,y=121.4)` as cycle-earned. For product variation,
**905768587** uses `Offer Cashback / Petrol Surcharge Waiver / Card Cashback` at
`x≈37,y=97.8/108.8/120.6`; only `Card Cashback=453` `(x=251.2,y=120.6)` maps to the
Cashback program accrual. `Petrol Surcharge Waiver` maps nowhere in rewards.

Special case **1390952698**: page 1 `REWARD SUMMARY` `(188.1,363.9)` prints `Current
Stmt Period=0` `(80.1,403.7)`, `Till Last Cycle=53724` `(211.9,403.7)`, and `Earned Till
Date=12380` `(356.4,403.7)`. None is labelled closing balance; therefore
`closingPoints=null`, `pointsEarnedThisCycle=0`, and `openingPoints=53724`.

No network or redemption rule, schema field, or runner behavior was changed.

## Baseline used

| | |
|---|---|
| **File** | `/Users/mayanck.bihani/Savesage/SYSTEM PROMPT.txt` (the client's own production prompt) |
| **File size on disk** | 10,111 bytes, sha256 `c618380769746a4abe988cb12cb947d979bac81424deb371286183a3454ca362` (matches the brief) |
| **What was actually sent** | the **inner string only** — 10,041 chars / **10,088 bytes** UTF-8, sha256 `9dc59e63b6957bf2…` |
| **Why they differ** | the file is Python source: it opens `SYSTEM_PROMPT = """` and closes `"""`. Stripping the 20-char prefix + 3-char suffix + trailing newline accounts for all 23 bytes. `sbi_lib.strip_python_wrapper()` fails loud rather than silently shipping the wrapper. |
| **Schema** | `luna_prompt/LUNA_SCHEMA.json`, **UNCHANGED**, verified byte-identical to `gt298_lib.GT_SCHEMA` (33 leaves). Identical across all 3 banks and all arms. |

`luna_prompt/LUNA_PROMPT.txt` was **not** used as the baseline. It is
ground-truth-flavoured ("this output will be used to score other models") and shares
the GT's prompt instrument, so it is the wrong instrument for a production baseline.

> **Correction to an earlier pass in this session.** A first Phase-1 run did use
> `LUNA_PROMPT.txt` by mistake. It was discarded and Phase 1 was re-run from scratch
> against the client prompt; every number below comes from the re-run
> (`run_p1_client/`). The mis-prompted run is retained at `run_luna_generic/` and is
> **not** used for any reported metric.

## Phase-1 baseline result (10 statements, client prompt, Luna 5.6 native PDF)

10/10 `OK`, `finish_reason=stop` on all 10, zero truncation, zero 429s, zero schema
violations. **Truncation is not the SBI risk it was predicted to be** — worst
statement (141 rows) used 7,627 completion tokens against a 96,000 cap (7.9%).

Defect tally over the 10 statements, every one adjudicated against the PDF with
PyMuPDF page/coordinate evidence:

| count | defect | verdict |
|---:|---|---|
| 8 | `LUNA_DROPPED_PRINTED_ROW__BEFORE_periodStart` | LUNA_WRONG, caused by a baseline rule |
| 3 | `LUNA_DROPPED_PRINTED_ROW__LEADING_BAND` | LUNA_WRONG, caused by a baseline rule |
| 1 | `LUNA_DROPPED_PRINTED_ROW__unclassified` | LUNA_WRONG |
| 2 | `LUNA_PAYMENT_RECEIVED_AS_DEBIT` | LUNA_WRONG, **caused by the baseline prompt being wrong** |
| 2 | `LUNA_DIRECTION_VS_CD_MARKER` | same 2 rows as above |
| 1 | `LUNA_AMOUNT_SCALED__DIV1000` | LUNA_WRONG |
| 4 | `LUNA_CLOSINGPOINTS_IS_CYCLE_CLOSING` | **CORRECT** — logged to prove the SBI rule holds |
| 0 | summary label mis-binding (either side) | clean on this sample |

---

## (a) VALIDATING the baseline's existing SBI rule — it is CORRECT, keep it

Baseline clause: *"For SBI cards: if closingPoints and cashback are not mentioned,
closing points must not be taken from total cashback earned or total reward points
earned from card issue date; it should reflect only the current statement cycle."*

This was an untested client assertion. **Measured: it is right, and it is load-bearing.**
SBI genuinely prints two different reward tables, and they hold different numbers:

- **stmt 1707857175** — the SAVINGS AND BENEFITS row prints `1072 | 5077 | 6590` under
  `For this statement | For this year | From the card issue date`. Luna returned
  `closingPoints = 1072`, the per-statement figure. Taking the lifetime column would
  have returned 6590 — a **6.1x** error. The incumbent also returned 1072.
- **stmts 837436340 / 1560321887 / 365516172 / 546216901** print the 4-cell cycle strip
  (`Previous Balance | Earned | Redeemed/Expired/Forfeited | Closing Balance`), e.g.
  `1256 | 109 | 0 | 1365` on 837436340. Luna took `1365` (Closing Balance) on all four.
  Verified by regex against the raw PDF text in `phase2_measure._points_evidence`.

**Change made:** the rule is **kept verbatim**, and *reinforced* with the layout
mechanics that make it followable — the two tables are now named explicitly, the
four cycle-strip cells are mapped one-to-one onto the four rewards fields, and the
prompt states that the labels are printed *after* their values in PDF text order
(they are, which is why a reading-order parse mis-binds them).

**Residual finding, not fixed by prompt text — stmt 515948911.** The PDF prints
`-1467 | -1467 | 44136` in the savings row and a `CASHBACK` block, with no cycle strip.
Luna returned `closingPoints = null`; the incumbent returned `1467` (sign dropped).
The printed value is **negative**. Neither source is right: `LUNA_WRONG` (null when a
value is printed) and `CSV_WRONG` (sign inverted) — a genuine `BOTH_WRONG`. The prompt
already says "RewardPoints can be negative"; the value is a cashback figure, so under
"if both points and cashback are present, select points" the correct answer is arguably
null. Flagged as **AMBIGUOUS** rather than papered over.

## (b) STRIPPING other banks' rules

Removed every rule that cannot fire on an SBI statement. Occurrence counts,
client → refined: HDFC 6→0, ICICI 1→0, INDUSIND 1→0, AU Bank 1→0,
Standard Chartered 1→0, IDFC 1→0, RBL 1→0, Marriott 2→0, eDGE 1→0,
Membership Rewards 1→0. (The one remaining `EDGE` is the `EDGE_CASES:` section header.)

That removed ~2,050 chars of dead weight. **The net prompt is still LONGER, not
shorter: 10,041 → 14,887 chars (+48.3%).** The brief's expectation, carried over from
the Axis corpus, was that stripping boilerplate would make the prompt shorter *and*
better. On SBI the strip is worth doing but is outweighed by the layout guidance the
corpus demanded. The output-token headroom argument for a leaner prompt **does not
apply here**: this is *input* tokens, and the measured constraint is not near any cap
(worst case 7,627 of 96,000 output tokens). Prompt input cost rises ~4,800 chars
(~1,200 tokens) per call against a ~19,000-token PDF payload — about +6% input.

---

## Rules ADDED — each with the observed defect that forced it

### 1. `SBI STATEMENT LAYOUT` block (new, at the top)
**Defect:** the ACCOUNT SUMMARY grid on page 1 emits all labels first and then all
values in a *different* order (verified: `page.get_text()` returns
`Credit Limit / Cash Limit / Available Credit Limit / Available Cash Limit` and the
figures in an unrelated sequence). A reading-order parse binds figures to the wrong
labels. **Statement id:** all 10; the geometry is documented in
`sbi_pdf_evidence.summary_evidence`. Luna got all four right on all 10, so this is
*prophylaxis for the 290 unseen statements*, honestly labelled as such — no observed
Luna defect here.

### 2. Leading-band transaction rows are transactions — **8 dropped rows fixed**
**Defect:** SBI prints statement-level credits ABOVE the
`TRANSACTIONS FOR <NAME>` header, and dates them *later* than the rows that follow.
Luna dropped the entire band on 2 of 10.
- **stmt 1707857175:** dropped `01 May 26 PAYMENT RECEIVED 000BD016121BALAAAJGB4VZ
  1,28,100.00 C` (p1 y=460) and `03 May 26 FUEL SURCHARGE WAIVER EXCL TAX 5.36 C`
  (p1 y=471) — 64 rows emitted vs 71 printed.
- **stmt 837436340:** dropped `01 Jul 26 PAYMENT RECEIVED … 15,050.00 C` (p1 y=460)
  — 137 vs 141.

### 3. The date-window rule must never DELETE a row — **the single biggest recall defect**
**Defect (root cause of 8 of the 12 dropped rows).** The baseline says
*"Transaction Date must not exceed the Statement Date, nor fall more than two months
prior to it"* and *"transaction dates must fall within statementPeriodStart and
statementPeriodEnd (inclusive) … If a parsed date fails this check, swap day/month and
re-validate."* Luna applied it as a **filter**, silently deleting legitimate printed rows:
- **stmt 837436340** (period 23/06–22/07/2026): dropped 4 rows printed `22 Jun 26`,
  one day before periodStart — `UPI-MONIKA LALIT CHANG 10.00 D`,
  `UPI-L A ENTERPRISE 10.00 D`, `UPI-PRAHALAD DHOBI 48.00 D`, `UPI-DINESHBHAI 115.00 D`.
- **stmt 1707857175** (period 17/04–16/05/2026): dropped 4 rows printed `16 Apr 26` —
  `UPI-REDEFINED PRIVATE L 20.00 D`, `… 105.00 D`, `… 20.00 D`,
  `UPI-A M R TEMPTATIONS 265.00 D`.

**Corpus-wide exposure, measured:** of 304 statements with a printed period, **76
(25.0%)** have at least one incumbent transaction dated outside it — **124 rows total**.
All 124 fall **BEFORE periodStart**; **0** fall after periodEnd and **0** after the
statement date. So on SBI the harmful half of the rule is the *lower* bound, not the
"must not exceed the Statement Date" clause the brief flagged from Axis.

**Change made — and this is a RECOMMENDATION, not a unilateral rewrite.** The user
previously chose to KEEP this rule as-is on Axis. I did **not** delete it. I narrowed
it to what it was presumably for (fixing an ambiguous day/month reading) and forbade
the destructive interpretation: *"THIS CHECK MAY ONLY CORRECT A DATE — IT MUST NEVER
DELETE A TRANSACTION."* The `must not exceed the Statement Date / two months prior`
sentence is dropped, because on this corpus it cannot help (0 rows violate the upper
bound) and it is the sentence that licenses deletion. If you want the original
wording restored, that is a one-line revert; the measured cost is ~124 rows across
the corpus.

### 4. The printed C/D column overrides everything — **fixes a baseline prompt ERROR**
**Defect:** the baseline says `DEBIT: … payment` and *"Payments TO the bank → DEBIT"*.
For a credit-card statement that is **backwards**: a payment received reduces the
balance and SBI prints it with `C`. Luna followed the prompt and got it wrong:
- **stmt 1120623464:** `PAYMENT RECEIVED 000DP016063184952z1QEsy` 34.00 →
  Luna `DEBIT`, PDF prints `C`, incumbent `CREDIT`. This is the statement's **only**
  transaction, so its transaction-level direction accuracy was 0%.
- **stmt 734857498:** `PAYMENT RECEIVED 000DP216187LEE0ICGZWZW9` 1074.00 →
  Luna `DEBIT`, PDF prints `C`, incumbent `CREDIT`.

**Corpus-wide:** the incumbent labels **485/485** `PAYMENT RECEIVED` rows `CREDIT`.
Luna-on-baseline got 8/10 right in the sample — i.e. the prompt made it wrong 20% of
the time on the highest-frequency credit row in the corpus.
**Change made:** `C → CREDIT`, `D → DEBIT`, `T → CREDIT` declared authoritative and
above description-reading; `PAYMENT RECEIVED` called out explicitly; the misleading
`payment → DEBIT` marker list removed; `C`/`D` added to the "never emit raw markers" list.

### 5. Comma is a thousands separator — **1 magnitude error**
**Defect — stmt 837436340:** printed `15,050.00`, Luna emitted **15.05**, a 1000x
error on a payment row (also the row it dropped from the leading band in the same
statement; both defects are logged separately). Change: Indian digit grouping spelled
out with `15,050.00` and `1,28,100.00` as worked examples.

### 6. Two-digit-year transaction dates
**Defect:** SBI prints transaction dates as `22 Jun 26`. Luna already normalised to
DD/MM/YYYY on 522/522 rows, so no Luna defect — but the same 2-digit form broke my
*scorer* (see below), which is direct evidence the format is a live hazard. Added as
prophylaxis with the explicit `22 Jun 26 → 22/06/2026` mapping and "never read the day
as the year".

### 7. `network` — the schema asks, the baseline never mentioned it
**Defect:** `network` has **0 occurrences** in the baseline prompt (as the brief noted)
yet is required by the schema. Baseline behaviour: Luna emitted `null` on 9/10 and
**`"VISA"` on stmt 1220226393**. Adjudicated against the PDF: `VISA` appears only in
boilerplate — the dispute paragraph *"All transaction disputes are resolved as per the
Network (VISA, MasterCard, Rupay, Amex) Guidelines"* (p7), a fee table (p6), and the
*"VISA Credit Card Pay"* payment-option blurb (p7). **Nothing** identifies this card's
network → `LUNA_WRONG`, `hallucinated_when_GT_null`. The incumbent hallucinates the
same way, on more statements: 37/315 rows carry a network value (`VISA` 23, `Visa` 8,
`RuPay` 2, `RUPAY` 2, `Rupay` 1, `Mastercard` 1) — those need per-statement
adjudication, reported in `SBI_REPORT.md`.
**Change made:** allowed values enumerated, and the three boilerplate locations named
as explicit non-evidence, with "a null is correct; a guessed network is a fabrication".

### 8. `issuerName` — 0 occurrences in the baseline
Baseline behaviour: 9/10 `"SBI Card"`, but **stmt 924475136** returned
`"SBI Cards and Payment Services Limited"` (the legal entity) where the incumbent said
`"SBI Card"`. Change: `issuerName = "SBI Card"`, with co-brand partners and the legal
entity name both named as wrong answers. **Flagged as non-discriminating** — the
incumbent is `SBI Card`/`SBI CARD`/`SBI card` on 314/315 rows, so a high score here is
casing normalisation, not extraction skill.

### 9. `totalMinimumAmountDue` and the four confusable summary labels
`totalMinimumAmountDue` has **0 occurrences** in the baseline. A new
`STATEMENT_LEVEL_SUMMARY` block maps all four labels and names the traps
(`Total Outstanding` ≠ `Total Amount Due`; `Cash Limit` ≠ `Credit Limit`;
`Available Cash Limit` ≠ `Available Credit Limit`; `Credit Limit` is a substring of
`Available Credit Limit`). No observed Luna defect on the 10 — prophylaxis, labelled.

### 10. `lastFourDigit` two-digit mask
SBI prints `XXXX XXXX XXXX XX25` — only the last **two** digits are real. Baseline
behaviour was already correct (`XX25`, 10/10 agreeing with the incumbent, whose values
are `XX99`-shaped on 312/315 and `xx99` on 3). Made explicit to protect against
backfilling to `0025`, which the baseline's own `0576` example invites.

### 11. `rawStatementId`
**Defect:** SBI **does** print `STMT No. : H26072450588` on page 1 — verified present
on **10/10** sampled statements. Luna extracted it on 10/10 unprompted. The GT prompt
(a different instrument) asserts this is "almost certainly NOT present"; on SBI that is
false. Added the `STMT No.` label so the behaviour is specified rather than incidental.

### 12. `COMPLETENESS IS MANDATORY`
Given SBI's density (mean 12.6, max 141 rows) and the 12 dropped rows above, an
explicit completeness instruction now names the grey-shaded and `#`-marked rows as
still-transactions, and the non-transaction blocks to exclude. Grey/`#` rows are real:
p1 of 837436340 prints *"Transactions highlighted in grey colour, if any, do not form
part of Purchases & Other Debits; #Transactions fully/partially converted to
Flexipay/Encash/Merchant EMI"* — a statement about which *subtotal* they belong to,
not about whether they are transactions.

### 13. `utilisationPercent`
**Measured: 0 of 300 SBI PDFs contain the string `utilis`/`utiliz`** (full text of all
300 scanned). The baseline's inference rule is retained unchanged, with "(SBI
statements do not print one.)" added so the field is understood as arithmetic, not
extraction. Reported both as-extracted and as-derived.

---

## Rule NOT added — a 14th change, found mid-run and deliberately deferred

**`TRANSFER TO …` rows carry no C/D marker.** Found while auditing the Phase-3 results:
`TRANSFER TO FLEXIPAY INSTALLMENT` (stmt 273593709) and `TRANSFER TO MERCHANT EMI`
(stmt 162725042) are printed with **no** direction marker. Verified across the corpus:
**14 statements have such a row and 0 of the 14 carry a C/D marker.** Both references
call them `CREDIT` (they move an amount out of the revolving purchase balance into an
instalment plan); the refined prompt's marker-less fallback list covers purchases, fees,
taxes, interest, markup and EMI instalments → DEBIT, so Luna returns `DEBIT` and scores
2 wrong on this pattern.

I wrote the fix, then **reverted it**, because the Phase-3 run was already in flight:
`run_arm.py` re-reads `SBI_PROMPT.txt` on every call, so editing it mid-run would have
produced two different prompts inside one arm and silently invalidated the arm's
provenance. Verified after reverting that all records carry a single
`prompt_sha256=da15a77509cca13e` matching the file on disk.

**Recommended next revision** (one clause, not applied here):

> "TRANSFER TO FLEXIPAY INSTALLMENT", "TRANSFER TO MERCHANT EMI", "TRANSFER TO ENCASH"
> and similar "TRANSFER TO …" rows are printed with NO C/D marker. They move an amount
> OUT of the revolving purchase balance into an instalment plan, so they are "CREDIT".

Expected effect: fixes 2 known direction errors; upper bound ~14 rows corpus-wide
(≈0.4% of 3,769). It is UNVERIFIED against the model — no run used it.

## 2026-08-11 — SBI-only prompt repair after the 300-statement review

No inference or re-sweep was run for these edits. Every impact below is a
**PREDICTION / UNVERIFIED** pending an authorised re-sweep.

### Edit 1 — preserve implausible-looking narration literally

Strengthened literal transcription so mangled, misspelled, mid-word-truncated, and
broken-URL-looking merchant text is preserved as the statement printed it, without
completion, correction, expansion, or reconstruction. This targets **17 description
cells (PREDICTION / UNVERIFIED)**, including 14 repeated broken-URL narrations on
statement 1712093656 and the observed `ONLIOLUTION`, `RANGAREDD`, and `INTE` cases.

### Edit 2 — keep the date column out of the description

Made date/narration separation independent of the `TRANSACTIONS FOR <NAME>` header:
the date column must never be prepended to the description, whether or not that header
is printed. This targets **5 description cells on statement 636217952 (PREDICTION /
UNVERIFIED)**; the layout is exposed on 65 statements without that header.

### Edit 3 — preserve printed terminal tokens

Required every visibly printed terminal description token, including country codes and
qualifiers, to be retained, while forbidding supply of tokens not printed. This targets
**3 description cells (PREDICTION / UNVERIFIED)**. The 13 printed trailing `IN` tokens
on statement 1349187066 are already emitted correctly and are reference defects, so the
rule deliberately preserves rather than removes them.

### Edit 4 — direction authority (C/D marker is authoritative)

Reinforced that SBI's printed C/D marker overrides narration wording whenever present:
*"Never infer direction from narration when a C/D marker is present."* SBI's C/D column
is safe and no other bank's glyph caveat applies. **Expected gain: 0 cells** — this is a
robustness/wording improvement, not a defect fix, because all 7 disputed direction cells
sit on rows that print no marker at all (see below).

A first version of this edit also added a blanket *"marker-less `TRANSFER TO ...` rows ->
`CREDIT`"* fallback. **That rule was wrong and has been removed.** It contradicted the
very principle this edit introduced — it inferred direction from narration — and the
evidence does not support it. See the next section.

### Edit 4b — the 7 marker-less `TRANSFER TO` direction cells are NOT prompt-fixable

**Finding: no reliable printed discriminator exists. No rule was written.** The blanket
`CREDIT` mapping was removed and deliberately not replaced with any other
narration-to-direction mapping. The generic marker-less fallback already in the prompt is
the only route for these rows, and the prompt now explicitly forbids pinning a direction
to a printed merchant phrase.

**Evidence 1 — the reference itself splits these rows almost exactly evenly.** Across all
300 statements there are exactly **14** `TRANSFER TO` rows, and the reference direction is
**DEBIT 7 / CREDIT 7**. The *same* description string carries *opposite* directions on
different statements:

| statement | description | reference direction | amount |
|---|---|---|---|
| 1040768215 | TRANSFER TO MERCHANT EMI | DEBIT | 22,251.04 |
| 162725042 | TRANSFER TO MERCHANT EMI | CREDIT | 5,114.00 |
| 1784860961 | TRANSFER TO MERCHANT EMI | CREDIT | 12,705.00 |
| 186548429 | TRANSFER TO MERCHANT EMI | CREDIT | 31,595.35 |
| 1939828045 | TRANSFER TO MERCHANT EMI | DEBIT | 6,176.27 |
| 423235138 | TRANSFER TO MERCHANT EMI | DEBIT | 27,528.36 |
| 525973295 | TRANSFER TO MERCHANT EMI | DEBIT | 34,994.20 |
| 664657130 | TRANSFER TO MERCHANT EMI | CREDIT | 32,778.70 |
| 749834844 | TRANSFER TO MERCHANT EMI | DEBIT | 13,910.00 |
| 807587861 | TRANSFER TO MERCHANT EMI | CREDIT | 88,770.63 |
| 834382309 | TRANSFER TO MERCHANT EMI | DEBIT | 2,29,470.49 |
| 273593709 | TRANSFER TO FLEXIPAY INSTALLMENT | CREDIT | 46,000.00 |
| 648670268 | TRANSFER TO FLEXIPAY INSTALLMENT | DEBIT | 51,601.00 |
| gmail_384287 | TRANSFER TO MERCHANT EMI | CREDIT | 74,857.60 |

Any blanket direction for this phrase is therefore wrong on ~7 of the 14 rows by
construction. A blanket `CREDIT` rule does not fix a 7-cell defect; it converts it into a
differently-distributed 7-cell defect while hardcoding a merchant-string-to-direction
mapping the data contradicts.

**Evidence 2 — PyMuPDF probe of all 14 rows in the source PDFs (read-only).** Nine
candidate discriminators were tested on raw, non-lowercased strings, with rows grouped by
a y-BAND rather than an exact baseline so a marker one or two points off still binds to
its row. Every one was eliminated:

| candidate discriminator | result |
|---|---|
| printed `C`/`D`/`T` marker on the row | **absent on all 14.** Corpus sweep of all 300 PDFs: 14 `TRANSFER TO` rows, **0** carrying any `C`/`D`/`T`/`M` marker |
| separate credit amount column | no — the amount's right edge matches the neighbouring `D` rows in the same layout (`x1=405.0`; `414.4` on the one page-2 case). Left edge varies only with digit count (right-aligned) |
| `+`/`-`/`CR`/`DR`/parenthesis sign token | none present on any of the 14 |
| text colour / font | identical on all 14 (colour `8355967`, font `SariOfLt`) |
| grey shading (the footer's "highlighted in grey" band) | no filled vector rect behind any of the 14, at any width; image counts are layout-driven, not direction-driven |
| section / table the row sits under | all 14 sit inside a `TRANSACTIONS FOR <NAME>` section (or the page-2 "Date / Transaction Details / Amount" continuation header). Same section type for both directions |
| wrapped-cell inheritance from the row above | **killed.** Each of the 14 prints its OWN date and sits at exactly the table's median row pitch (11.8 pt). The row above is a `D` row for both a CREDIT case (162725042) and a DEBIT case (1040768215), and two DEBIT cases (525973295, 648670268) sit directly under the section header with no row above to inherit from |
| `Previous Balance / Payments / Purchases` arithmetic strip | identical boilerplate on all 14; carries nothing direction-bearing |
| pairing with the `#`-marked source row of the same amount | cross-cutting, explains nothing: 4 of 7 CREDIT have a same-amount `#` row, and so do 2 of 7 DEBIT |

**Evidence 3 — the removed rule named a string that does not exist.** `TRANSFER TO ENCASH`
appears **0 times** in the 300-PDF corpus, so that clause was unjustified by the data
independently of the direction question.

**Conclusion.** On these rows the direction is not determinable from the row text or from
any printed structural feature of the document. The reference's own 7/7 split, on an
identical printed phrase with no printed marker to distinguish the cases, means the split
is either internally inconsistent or driven by information not present in the statement.
**These ~7 cells are not reliably prompt-fixable, and no prompt rule should claim them.**
Resolving them requires a client decision on how a marker-less balance transfer between
the revolving balance and an instalment plan should be signed — not a prompt edit.

### Deliberately not changed — non-prompt-fixable or harmful targets

- **52 date `both_null` rows:** Luna and the reference agree; the joint metric charge is
  a scorer defect.
- **71 undated tax/markup continuation rows:** inheriting the parent date is useful and
  conflicts with GT rule 14; no rule was added to null continuation-row dates. Only the
  3 inconsistent nulls merit a future consistency review.
- **14 description cells:** the reference drops text genuinely printed, including the
  trailing-country-code case; no prompt can improve those references.
- **2,255 amount `format_only` cells:** integer/float serialization has identical digits
  and numeric comparison already treats them as equal, so no serialization format was
  pinned and the expected gain is exactly zero cells.
- **14 `rewards.closingPoints` null disagreements:** the client cashback instruction and
  shared GT rule 13 contradict each other; this requires a client decision.
- **7 marker-less `TRANSFER TO` direction cells:** no printed discriminator separates the
  DEBIT cases from the CREDIT ones — nine candidates tested and eliminated against the
  PDFs, and the reference splits an identical phrase DEBIT 7 / CREDIT 7. No rule was
  written and the blanket `CREDIT` mapping was removed. See Edit 4b.
- The working leading-credit-band and two-reward-table rules were preserved; measured
  row alignment is 3,527/3,527 with zero lifetime leaks.
- No HDFC or ICICI rules were added, and the shared `GT_SCHEMA` was not changed.

## Anti-overfit note

These 13 changes were tuned on 10 statements and tested on ~300 — a 30x
extrapolation. Changes 2, 3, 4, 5 are backed by located defects; 1, 6, 9, 10 are
prophylactic and explicitly labelled as having **no** observed Luna defect. Phase 3
therefore reports every metric twice: over all statements, and over the ~290 held-out
statements excluding these 10. The held-out numbers are the ones to trust.

## Scorer defect found and fixed (not a prompt change, but it would have inverted a result)

The project's canonical `score.date_norm` does not parse SBI's dominant transaction-date
format, the 2-digit-year `DD Mon YY` — it returns the string unchanged, so
`'22 Jun 26' != '22/06/2026'`. **2,733 of 3,769 (72.5%)** incumbent transaction dates
are in that form, while Luna and Opus emit `DD/MM/YYYY` on 100% of rows. Unpatched, the
scorer would have charged the *incumbent* a fabricated 72.5% date-defect rate and
handed Luna an unearned win, while also corrupting the matcher's date tie-break.
`score_lib_sbi.date_norm` now wraps the canonical one (rather than editing it, so the
other two banks keep the identical yardstick).

---

## 2026-08-13 — SBI under the CLIENT's 26-leaf Gemini schema (`sbi/gemini/`)

Scope: the 12 PDFs in `~/Downloads/output/SBI/PDF` (the human said 15; the folder holds
12 — verified). Schema is the client's line-64 type map, converted by
`gemini/convert_schema.py` and tightened by `gemini/patch_schema.py`; the 26-leaf
contract is guarded by `gemini/assert_schema.py`, which is negative-tested in both
directions (enum-omits-null → exit 1, field-removed → exit 1).

**The comparator is NOT human ground truth.** The `data` blob in
`remaining pdfs ground truth/sbi.csv` carries `modelName: gemini-3-flash-preview` /
`databricks-gemini-3-flash` and `detectionSource: GEMINI`. It is the client's
INCUMBENT MODEL OUTPUT, i.e. the contract to match, and cells where it is contradicted
by the PDF are recorded as GT defects, not as our failures.

### Diagnosis first — 3 of the 5 "weak" fields were already correct

`gemini/probe_5fields.py` + `gemini/adjudicate_5fields.py`, evidence in
`gemini/probe/`:

- **`network` — no change made; null was already right.** Every VISA / MasterCard /
  Rupay / Amex occurrence in all 12 PDFs is boilerplate: the dispute-resolution
  paragraph, the international-fee table (`$175 for VISA and $148 … for Mastercard`),
  the "VISA Credit Card Pay" blurb and the "NEFT, Visa Money Transfer, MasterCard
  MoneySend" channel list. **Zero network tokens appear anywhere on page 1**, where the
  masked card number is printed (card number at x≈317–398, y≈75–85 on all 12). The
  page-1 header art was rendered and inspected visually: it carries the co-brand product
  logo and the SBI Card logo only — no network mark, so this is not an IMAGE_ONLY
  ceiling either. Incumbent says `RuPay` on **221159806**, whose only RuPay occurrence
  in the whole document is the dispute boilerplate → **GT_DEFECT**. 11/12 agree.
  Corpus-wide the incumbent populates `network` on just 37/315 rows, with case-variant
  values (`VISA`/`Visa`/`RUPAY`/`Rupay`/`Mastercard`), i.e. the field is unstable in the
  comparator itself. No rule was added, because any rule that produced a network here
  would have to fabricate from boilerplate or from the card number.
- **`pointsExpiringNext60Days` — no change; nothing is printed.** No 60-day figure
  exists in any of the 12 PDFs. 12/12 both-null. **NON-DISCRIMINATING — this field
  cannot be reported as an accuracy figure.**
- **`pointsRedeemedThisCycle` — rule deliberately NOT added.** The incumbent sources it
  from the `CARD CASHBACK CREDIT` **transaction row** on **1511624796** (782.00),
  **515948911** (4,191.00) and **1118980175** (1,544.50). But it leaves the field null
  on **1036185244** and **369606524**, which print the identical row type
  (390.00 and 424.00). The comparator is self-inconsistent, so a
  "cashback-credit row → pointsRedeemedThisCycle" rule would win 3 cells and **break 2
  that are currently correct**, while also violating "never roll up rewards from
  transactions". See Edit 4.

### Edit 1 — `closingPoints`: restore the clause our refined prompt had dropped

**The single real gap between our prompt and the client's.** The mention-count audit
(`gemini/gap_audit.py`, client body = lines 1–61 only, **line 64 excluded** because it
is the type map, not guidance) returns an **EMPTY PORT_IN list** — as on HDFC. But a
clause-level read found one decisive omission inside a sentence we had *kept*.

Client, lines 57–59: *"For SBI cards if any closing points and cashback is not mentioned
in the statement, 'closing points' should not be taken total cashback earned or total
reward points earned from card issue date. **it should be how much cashback earned on
current statement**."*

Ours had paraphrased that to *"it should reflect only the current statement cycle"* —
keeping the prohibition and **dropping the positive instruction**. Combined with our own
"set closingPoints = null ONLY if no numeric rewards **balance** is explicitly shown",
the model correctly concluded "no balance printed → null" and emitted null on 8 of 12,
against an incumbent that populates `closingPoints` on **314/315 rows corpus-wide**.

Also replaced the prompt's **factually wrong claim that SBI prints two reward tables**.
Measured on the 12 (`gemini/probe_rewards.py`): the four-cell
`Previous Balance | Earned | Redeemed/Expired/Forfeited | Closing Balance` strip our
prompt called "the ONLY source" is present on **1 of 12 statements** (221159806). The
other 11 use one of two single-figure shapes. The prompt now enumerates the three
measured shapes and maps each explicitly:

| shape | statements | closingPoints source |
|---|---|---|
| 1 — four-cell balance strip | 221159806 | `Closing Balance` |
| 2 — one current-statement figure (`CARD CASHBACK SUMMARY…`, `Reward Point Summary`, `NeuCoins Summary`) | 1036185244, 1120623464, 1152718739, 1511624796, 1707857175, 369606524, 515948911, 1118980175, 393366914, 905768587 | that figure (also `pointsEarnedThisCycle`) |
| 3 — `Current Stmt Period \| Till Last Cycle \| Earned Till Date` | 1390952698 | `Current Stmt Period` |

Added `closingPoints is NEVER COMPUTED`. This targets a measured live defect:
**1390952698**, where the prior output emitted `closingPoints = 53724` =
`openingPoints (53724) + earned (0)`, a derivation both prompts forbid; the incumbent
says `0`, the `Current Stmt Period` cell.

PREDICTED, UNVERIFIED until measured: `closingPoints` 3/12 → ~10/12, and
`pointsEarnedThisCycle` repaired on **1707857175** (was null, printed `1072` under
`NeuCoins`) and **393366914** (was null, printed `0` under `Reward Points`). Two cells
stay wrong for GT reasons: **1152718739** (incumbent `1879` = the `From the card issue
date` LIFETIME column, which the client's own prompt forbids) and **515948911**
(incumbent `1467` where the PDF prints `-1467`).

### Edit 2 — `pointsExpiringNext30Days`: zero guidance → the printed-"NONE" reading

Both prompts had **0 mentions** of this field. **221159806** prints a
`Points Expiry Details` cell whose value is the word **`NONE`**, and the incumbent
records `pointsExpiringNext30Days = 0` — a defensible reading: the label is printed and
says nothing is expiring. Rule added, scoped so it can only fire where the label
actually appears: `NONE` → 0, a printed figure → that figure verbatim including 0, and
null when no expiry cell is printed (every shape-2 and shape-3 statement, i.e. 11/12).
Confirmed live on the one-PDF smoke test: arm A now emits `0`.

### Edit 3 — `txnType` vocabulary: zero guidance in either prompt

0 mentions in ours, 0 in the client body. Added the closed vocabulary
`PURCHASE, PAYMENT, REFUND, REVERSAL, CASHBACK, FEE, TAX, INTEREST, EMI, CASH_ADVANCE,
UPI` with SBI-specific anchors (`PAYMENT RECEIVED…`→PAYMENT, `IGST DB @…`→TAX,
`ANNUAL FEE CHARGED`/`FUEL SURCHARGE WAIVER EXCL TAX`→FEE, `FP EMI nn/nn`→EMI,
`CARD CASHBACK CREDIT`→CASHBACK), and mirrored it VERBATIM as the schema enum.
`CASH_ADVANCE` is included although this 12-file sample never emits it — narrowing the
enum to the sample would bake the sample into the contract. Note the incumbent leaves
`txnType` null on 138/193 rows here, so this field has a weak oracle.

### Edit 4 — `CARD CASHBACK CREDIT` is a transaction, not a rewards cell

Our prompt said "cashback credited or transferred = pointsRedeemedThisCycle" while also
saying "extract rewards ONLY from statement-level rewards sections" — an internal
contradiction, since on SBI the only place a cashback credit appears is a transaction
row. Resolved in the safe direction and stated explicitly, per the DO-NOT-PORT finding
above: that row must not populate any `rewards.*` field. This preserves the 2 correct
cells (1036185244, 369606524) rather than chasing the 3 the incumbent populates.

### Edit 5 — direction markers: the prompt's marker list was wrong

Prompt said "a single-character direction column: C, D or T". Measured on the 12: the
markers actually printed are **C, D and M** — `M` appears on **905768587**'s two
`FP EMI nn/nn` Flexipay instalment rows. Added `M → DEBIT` (which is what the prior
output already produced via the fall-through rule, so this documents rather than changes
behaviour) and a fall-through for any other marker. `T → CREDIT` kept. `FP`/`EN` were
NOT added: neither appears in this sample.

**Verified, not assumed: there is no ITFRupee font in any of the 12 files, and zero bare
`C` tokens outside the direction column on page 1.** The client's
`CR, C, + as CREDIT` clause — catastrophic on HDFC, where ITFRupee maps the rupee sign
to ASCII `C` — is therefore **harmless on SBI**, where `C` genuinely is the credit
marker and the rupee sign is a backtick (`Amount ( ` )`).

### Edit 6 — deleted rules for fields the 26-leaf schema cannot emit

`additionalProperties: false` makes these dead instructions at best and an
instruction/schema conflict at worst. Removed: the whole `INFERENCE_RULES` allowlist
(`financeChargesThisCycle`, `utilisationPercent`), `BONUS_POINTS_RULE`
(`bonusPointsThisCycle`), `cards[].bigPicture.cardCreditLimit /
cardAvailableCreditLimit`, `statementMeta.rawStatementId`, and the
`statementPeriodStart / statementPeriodEnd` output rule. `gemini/gap_audit.py` now
reports **zero orphan lines**.

Two live rules were RELOCATED rather than lost with their sections:
1. the transaction-date sanity check needs the printed statement period, so the
   `"for Statement Period: <start> to <end>"` sentence is retained explicitly as an
   **internal reference that is not an output field**;
2. `MISSING_DATA_RULE` referenced "fields listed in INFERENCE_RULES", which would have
   dangled — rewritten to the now-unconditional "nothing in this schema is ever
   inferred, derived or computed".
The `isPrimaryCard` rule was added in the freed space (1 mention → 2), and the
date-field list was trimmed to the emittable dates.

### Edit 7 — the dangling schema reference

`"strictly matching the provided schema"` named a schema the prompt does not contain;
the schema arrives via `response_format`. Reworded to refer to the schema supplied with
the request.

### Deliberately NOT changed

- The **undated tax/IGST continuation row** contract divergence (our prompt inherits the
  parent date; the incumbent nulls it; 71 rows charged for it on the 300-corpus). Nulling
  those rows would raise the measured date score while **destroying the parent date on
  71+ rows**. Not applied; it is a contract divergence, not a misread.
- Any `TRANSFER TO …` → direction rule. Already tried and removed: the incumbent splits
  those rows 7 DEBIT / 7 CREDIT on identical narration with no discriminator.
- HDFC/ICICI-specific rules (ITFRupee glyph, HDFC column layout, ICICI mask scheme).
  SBI's mask is `XXXX XXXX XXXX XX57` — only the last TWO digits are real, and the prior
  output already matched the PDF 12/12 on `lastFourDigit`.
- `Points Earned Till Date` as a `closingPoints` label. It is in the client's label list
  but is an other-bank label; on SBI it maps to the lifetime column that the client's own
  SBI-specific sentence forbids.

### Edit 8 — `txnType` REFUND anchor: fixing a regression Edit 3 itself caused

Edit 3 gave anchors for PAYMENT / TAX / FEE / EMI / CASHBACK / CASH_ADVANCE / PURCHASE but
**none for REFUND or REVERSAL**, and then said "use null when the row's kind is not
determinable". The first arm-A run measured the consequence: **4 rows on 515948911** where
the incumbent says `REFUND` and arm A emitted `null`, all four merchant rows carrying a
CREDIT marker —

```
AMAZON PAY INDIA PRIVA WWW.AMAZON.IN IN   29,899  CREDIT
WWW DYSON IN GURGAON IN                   29,400  CREDIT
Razorpay Payments GURGAON IN               2,990  CREDIT
BLINK COMMERCE PVT LTD BANGALORE IN        2,313  CREDIT
```

Arm B (the previous prompt, no txnType guidance) got all 4 right. Added the anchor "a
MERCHANT row carrying a CREDIT marker → REFUND", plus a REVERSAL anchor and a narrowing of
the null instruction to "do not null a row whose direction and narration already identify
it". **Re-measured: arm A now 55/55 on the cells the incumbent populates, identical to
arms B and C. Regression closed.**

## Measured outcome of this pass (12 statements, 3 arms, 36/36 calls OK, zero 429s)

| field | A (new) | B (previous) | C (client) | note |
|---|---|---|---|---|
| **rewards.closingPoints** | **10/12** | 3/12 | 3/12 | Edit 1. Both remaining misses are GT defects; vs the PDF arm A is right 12/12 |
| rewards.pointsEarnedThisCycle | 11/12 | 11/12 | 10/12 | Edit 1 also repaired 1707857175 + 393366914 relative to the client arm |
| rewards.openingPoints | 10/12 | 9/12 | 10/12 | Edit 1 (SHAPE 3 `Till Last Cycle` binding) |
| rewards.pointsExpiringNext30Days | 12/12 | 12/12 | 12/12 | Edit 2 fires on 221159806; NON-DISCRIMINATING on this sample |
| rewards.programType | 11/12 | 11/12 | 9/12 | pre-existing refinement, not this pass |
| transactions[].txnType (incumbent-populated cells) | 55/55 | 55/55 | 55/55 | Edit 3 + Edit 8 net-neutral vs B |
| transactions[].direction / amount / currency | 100% | 100% | 100% / 100% / 96.3% | direction unaffected by Edit 5, as expected |
| cards[].cardMeta.network | 11/12 | 11/12 | 11/12 | no rule changed; the 1 miss is a GT defect |

Full tables, adjudication verdicts, PORT_IN/DO_NOT_PORT and the UNVERIFIED section:
`gemini/SBI_GEMINI_SCHEMA_TEST_REPORT.md`.

### Two measurement defects found and corrected during this pass (not model findings)

1. **Greedy transaction matching manufactured a fake regression.** `analyse.py` first
   matched rows greedily on (amount, description). SBI repeats identical
   description+amount pairs across dates (1707857175 has a long `UPI-REDEFINED PRIVATE L`
   @ 20.00 run), so one dropped row shifted the whole run and produced a cascade of
   phantom date mismatches — reporting **arm A at 93.8% on `date`** and a false
   `description` regression. Replaced with order-preserving LCS alignment; the date is
   excluded from the match key because it is under measurement. Corrected figures: `date`
   A 99.5% (one cell), `description` 100%.
2. **A whitespace-collapsed numeric probe falsely accused the ground truth.** Searching
   for numbers in text with all whitespace stripped destroys token boundaries
   (`12 720 1879` → `127201879`), so a word-bounded search for `1879` reported NOT PRINTED
   for a figure that IS printed on 1152718739. Numbers are now matched against the word
   token list; collapsed text is used only for alphabetic labels, where mid-word line-wrap
   is the genuine hazard.

### Deltas that were repeat-tested rather than claimed

- `transactions[].date`, arm A 188/189 vs B 190/190 — the single cell is 905768587's
  undated `IGST DB @ 18.00%` row. Three fresh arm-A calls: `03/06/2026`, `03/06/2026`,
  `null`. **Non-deterministic; not an effect of any edit. No revert.**
- `Cashfree*FLIPKART INTE` truncated to `IN` on 369606524 — stable 3/3 in arm A, **but
  arm B is only 1/3 correct and arm C also truncates.** Model-level transcription
  weakness across all arms, not a regression from this pass.
- Row completeness on the 71-row statement 1707857175: A=69, B=70, C=**71**. Arm A
  repeats: 70, 69, 70. **Both refined prompts reproducibly drop rows on the longest
  statement while the client's shorter prompt does not.** Pre-existing, present in B,
  and A is within variance of B — but it is a real open issue and is flagged for
  follow-up rather than fixed here. The `closingPoints` gain is not traded for it.
