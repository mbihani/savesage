# ICICI prompt refinement — changelog

Every change below cites a **specific observed defect and statement id** from the Phase-1
10-statement baseline run. Nothing here is a stylistic preference.

## Baseline instrument (stated plainly)

| | |
|---|---|
| **Baseline file** | `/Users/mayanck.bihani/Savesage/SYSTEM PROMPT.txt` — the client's own production prompt |
| File size | **10,111 bytes**, sha256 `c618380769746a4abe988cb12cb947d979bac81424deb371286183a3454ca362` |
| **What was actually sent** | the **inner string literal only**. The file is a *Python source file*: it opens `SYSTEM_PROMPT = """` and closes `"""`. Wrapper stripped. |
| **Bytes sent** | **10,088 bytes** / 10,041 characters (bytes > chars: the prompt contains non-ASCII — `₹` and the `→` arrows in its own mapping rules) |
| sha256 of sent string | `9dc59e63b6957bf24ca3fdb9f7dee9389f5650dc030c7abfae7d3277ae025bac` |
| **Refined file** | `ICICI_PROMPT.txt` — **11,885 bytes** / 11,842 chars, sha256 `2ba790951037a779a84043bdd2cf3a930514898be9b62a5d32c3eedbe74350f6` |
| Net size change | **+1,801 chars (+17.9%)** — see "Did stripping make it shorter?" below |

`luna_prompt/LUNA_PROMPT.txt` was **deliberately not used** as the baseline. It is
ground-truth-flavoured ("this output will be used to score other models") and, worse for this
corpus, it hard-codes `issuerName — ... For this corpus that is "Axis Bank"`. It is the wrong
instrument for an ICICI production baseline. (It remains the Opus-GT instrument, unchanged, so
the GT stays identical across the icici/hdfc/sbi workers.)

**Schema**: `luna_prompt/LUNA_SCHEMA.json` sent **unchanged** in `response_format`, verified
byte-identical to `gt298_lib.GT_SCHEMA`. Unchanged across all three banks.

## Fields the baseline prompt never names

Verified by grep against the sent string: `network` **0** occurrences, `issuerName` **0**,
`totalMinimumAmountDue` **0**, `txnType` **0**. These four are in the schema but get **no prompt
guidance whatsoever**. Any baseline miss on them is a *prompt-coverage* gap, not a model
capability failure, and is reported as such.

---

## Lever (a) — VALIDATING the existing ICICI rule

The baseline carried exactly one ICICI clause (baseline lines 96–97):

> `- For ICICI BANK statements: do not compute closingPoints using total points earned and redeemed; if not available, set to null. Points transferred = points redeemed.`

This was an **untested client assertion**. I checked it against all 304 real PDFs.

**VERDICT: both halves are CORRECT, and the rule is more necessary than the client knew.**

1. **"Do not compute closingPoints from earned and redeemed" — CORRECT and load-bearing.**
   Measured across 304 PDFs: `Total Points earned` appears in **122/304** and `Points earned on
   iShop` in **122/304**. These are *cycle earn* figures printed in the statement header. **No
   ICICI PDF in this corpus prints a closing/available points balance under any label.** So the
   only way to produce `closingPoints` is to compute it — which is exactly what the rule forbids.
   Luna obeyed: `closingPoints = null` on 10/10 baseline statements.
   The **incumbent CSV does not obey it**: it emits a non-null `closingPoints` on **56/304**
   statements, and on the sampled cases the value equals `pointsEarnedThisCycle` exactly
   (e.g. `1010092654`: closing 495 = earned 495; `553419366`: 134 = 134; `518298999`: 340 = 340).
   That is the forbidden derivation. **The client's rule is right and their own incumbent parser
   violates it on ~18% of the corpus.**

2. **"Points transferred = points redeemed" — CORRECT, and narrower than it looks.**
   `Points Transferred` appears in only **8/304** PDFs, always as the header pair
   `Points Earned | Points Transferred to PAYBACK | PAYBACK Account Number`
   (verified on `380476562`, `586112625`, `601053102`). So the mapping is sound but applies to 8
   statements. Retained and made explicit about the PAYBACK context.

### A defect the existing rule created — and the highest-value single finding

The baseline's *generic* rewards rule (line 48) lists **`Closing Balance`** as a label from which
to populate `closingPoints`. On ICICI that is actively dangerous:

**`Closing Balance` appears in 277/304 PDFs with the byte-identical value `26,958.20` in all 277.**

It is not the cardholder's anything. It is line 18 of a **pre-printed illustrative worked example**
— the numbered `SL. No | Transaction` table introduced by *"On statement dated Nov 08, 2025,
following Minimum Amount Due is calculated"* — which ships as static boilerplate on every ICICI
statement regardless of customer. Confirmed on `1010092654` (p11), `1025056219` (p8),
`1025079069` (p8). A prompt that says "populate closingPoints from Closing Balance" is a standing
instruction to emit a **fabricated ₹26,958.20** on 91% of the corpus. Neither Luna nor the
incumbent fell for it in the sample, but the instruction is live and the trap is corpus-wide, so
the refined prompt fences the whole boilerplate table off explicitly.

---

## Lever (b) — STRIPPING other banks' rules

15 of the baseline's 136 lines are rules for banks not in this corpus. Removed:

| Removed | Baseline lines | Why safe |
|---|---|---|
| INDUSIND `totalAmountDue` vs Total Outstanding | 98 | no IndusInd statements |
| AU Bank `statementDate` from period sentence | 99 | no AU statements |
| HDFC bonus-points-not-closingPoints | 100–101 | no HDFC statements |
| Standard Chartered card-acct-number | 102–103 | no StanChart statements |
| IDFC FIRST SELECT card-number source | 104–105 | no IDFC statements |
| SBI closingPoints cycle rule | 106–108 | no SBI statements |
| RBL **colour-based** direction (red/black=DEBIT, green=CREDIT) | 118–121 | no RBL statements; and a colour rule is a live hazard on ICICI, which marks direction with a textual `CR` suffix |
| HDFC cardholder-name-row + HDFC rewards field priorities | 122–125, 127–136 | no HDFC statements |
| `NeuCoins` / `Marriott Bonvoy` / `eDGE REWARD POINTS` from the programType and rewards-label lists | 42, 48 | none occur in any ICICI PDF |

**Deliberately KEPT** (bank-agnostic despite sitting in an HDFC block): baseline line 126,
*"if the transaction amount has a `+`/`Cr`/`C`/`CREDIT` marker, set direction to CREDIT"* — ICICI
prints exactly this `CR` suffix, so the rule is directly load-bearing here. It was de-scoped from
"HDFC transaction description statements" to apply generally.

> **Cross-check on the `C` clause, prompted by the HDFC worker's finding.** The sibling HDFC
> worker measured that HDFC PDFs encode `₹` as a bare ASCII `C` (ITFRupee font), which makes this
> same *"`C` ⇒ CREDIT"* clause corrupt direction on 64% of *their* corpus. I therefore tested
> whether keeping it is safe on ICICI, rather than assuming:
> * ICICI uses the **`RupeeForadian`** font, which renders `₹` as a **backtick** — `` `1,70,000.00 ``
>   — in **303/304** PDFs. It is never a `C`.
> * ICICI marks credits with an explicit **`CR`** suffix (`5,240.00 CR`), never a bare `C`.
> * A regex for a bare `C` adjacent to an amount hit 69/304, but on inspection **every hit was the
>   `C` of the printed label "Available **C**redit Limit"**, not a currency glyph or a direction
>   marker — a false positive of my own probe, not a trap in the corpus.
>
> So the clause is safe **on ICICI specifically**, for a reason that does not transfer: the
> currency glyph differs per bank. Same clause, opposite verdict on HDFC — which is itself the
> strongest argument for per-bank prompts over one shared multi-bank prompt.

Also **re-exampled** baseline line 84, `cardDisplayName`, whose only worked examples were
`"HDFC Regalia"` and `"SBI SimplyCLICK"` — replaced with ICICI products actually in the corpus.

### Did stripping make the prompt shorter? — NO. Measured, not assumed.

On the Axis corpus, removing bank boilerplate made the prompt shorter *and* better. **That did not
reproduce here.** Stripping removed ~1.0k chars, but the ICICI-specific rules the measured defects
demanded added ~2.8k, for a **net +1,801 chars (+17.9%)**. The two levers are reported separately
because they moved in opposite directions: (b) is a pure simplification, (a) plus the new
defect-driven rules is a net expansion. I am not claiming a shorter prompt for ICICI.

---

## Rules ADDED — each traced to an observed defect

### 1. `issuerName` is always "ICICI Bank" (co-brand is not the issuer)
**Prompt coverage gap** — `issuerName` occurred **0 times** in the baseline.
**Observed: the co-brand trap did NOT reproduce.** Luna returned `"ICICI Bank"` on **10/10**
baseline statements, across 10 *distinct* co-branded products (Coral, AdaniOne, MMT, Amazon,
HPCL, MMT_NEW, Expressions, Sapphiro, Platinum). The incumbent also got 10/10.
On the Axis corpus the same gap cost 18/94. **It cost 0/10 here — I am reporting a
non-reproduction, not a fix.** The rule is added as *cheap insurance only*, since the corpus is
heavily co-branded (133/304 Amazon, 45 Coral, 26 Sapphiro, …) and the field had zero guidance.
It is explicitly **not** credited with any measured lift.

### 2. `network` MUST be null — the four-network disclaimer is not evidence
**Prompt coverage gap** — `network` occurred **0 times** in the baseline.
**PDF evidence:** all four of `Visa`, `Mastercard`, `RuPay`, `American Express` appear in
**10/10** sampled PDFs — always and only in one boilerplate sentence:
*"For RuPay/American Express/ Visa/Mastercard Credit Cards: Fuel surcharge and corresponding GST
levied is included…"*. It lists every network and identifies none.
- **Luna defect (`670758880`)**: emitted `network="VISA"` with that disclaimer as the sole
  network mention in the file → **hallucination**. The refined rule targets this directly.
- **Incumbent defect (`820648402`)**: emitted `American Express`, `Mastercard`, `RuPay` for the
  three cards on one statement — i.e. it walked the disclaimer list in order and assigned one
  network per card. Also `2054837190`: `VISA` with no supporting evidence. **These are
  `CSV_WRONG`.** Luna's `null` was right in both.

### 3. `rawStatementId` = null; "Invoice No" is not a statement id
**Observed on 10/10 baseline statements** — Luna emitted the header `Invoice No:` value as
`rawStatementId` every single time: `1010092654`→`1574020800107986`,
`1066621585`→`1574050800563261`, `1711342048`→`1574211100311200`,
`2054837190`→`1574280500249755`, `516479745`→`1574020700866620`, `557652636`→`1574080200425180`,
`670758880`→`1574120701593277`, `820648402`→`1574020300805977`, `979848021`→`1574310700035791`,
and `524015761`→`03072026_32573`. The incumbent returned `null` on all 10.
This was the single most frequent baseline defect (10/10). **Verified fixed** on the 3-statement
smoke test: all three now return `null`.

### 4. Foreign-currency rows: report the **rupee** amount as INR
**Luna defect, 3 rows across 2 statements.** ICICI prints an `Intl.# amount` column to the LEFT of
`Amount (in ₹)`. PDF evidence (`2054837190` p2):
`CLAUDE.AI SUBSCRIPTION ANTHROPIC.COM US* | 0 | 118 USD | 11,632.05`
Luna reported `amount=11632.05` (the **rupee** figure) with `currency="USD"` — it took the amount
from one column and the currency from the other, producing a row that claims US$11,632 for a
US$118 charge. Same defect on `2054837190` (`LOVABLE LOVABLE.DEV US*`, 489.78) and `979848021`
(`ANTHROPIC* CLAUDE SUB`, 2369.31). The incumbent said `INR` on all three and was **right**.
**Verified fixed**: non-INR rows went 2→0 and 1→0 on the smoke test.

### 5. The Reward Points column is not the amount (row/column drift)
**Luna defect (`979848021`), the most serious extraction error found.** Luna returned **40** rows
where the PDF and incumbent both have **41**: it dropped one `PANTPROJECT COM MUMBAI IN` row and
assigned that row's `2,290.00` to the *next* row, `Interest Amount Amortization - <5/6>Vijay
Sales`, whose true amount is `123.76`. Root cause is the three-way column layout
`description | Reward Points | Amount`, aggravated by four consecutive PANTPROJECT rows at the
identical amount 2,290.00 with reward points `+45/-45`.
**Verified fixed**: refined run returns **41** rows and `Vijay Sales = 123.76`.
Also drove the added "extract exact duplicate rows, do not de-duplicate" clause.

### 6. `isPrimaryCard` — first card under Statement Summary is primary
**Baseline defect, 11 card-instances across 7 statements** — Luna returned `null` for every card
on every multi-card statement (`1066621585`, `516479745` ×3, `524015761` ×2, `557652636`,
`670758880`, `820648402` ×3). ICICI genuinely never prints the words "primary"/"add-on"
(grep confirms: 0 hits in `820648402`), so `null` was defensible — but the incumbent populates it
and downstream consumes it, so an ordering rule is given.
**Verified fixed**: `820648402` went `[None,None]` → `[True,False,False]`.

### 7. Card-grouping rule — recovers a genuinely missed card
**Luna defect (`820648402`)**: Luna found **2** cards; the PDF prints **3** masked card numbers
(`5241XXXXXXXX8008` and `3747XXXXXXXX5002` on p1, `6528XXXXXXXX3009` on p2). Luna returned 5002
and 3009 and **missed 8008** entirely — a whole card, and its transactions were grouped under a
masked-number heading on a later page. **Verified fixed**: refined run returns all three
(`5002`, `8008`, `3009`).

### 8. Amortization rows are real transactions; keep `<n/m>` verbatim
Defensive, from observation rather than a scored defect: `Principal/Interest Amount Amortization -
<n/m>MERCHANT` rows are ubiquitous (`979848021`, `2054837190`, `1010092654`) and sit adjacent to
the boilerplate EMI *forecast* example that must be excluded. Made explicit so the exclusion of
the specimen table cannot over-fire onto real EMI rows. Description fidelity matters: the one
baseline description defect was a truncation, `...UPI-133114539429-Amaz` vs `...Amazo`
(`1010092654`).

---

## Defects observed but deliberately NOT "fixed"

- **`rewards.programType` (8/10 "disagreements")** — Luna said `Cashback`/`Reward Points`; the
  incumbent said `Adani One`, `MakeMyTrip My Cash`, `Amazon Pay balance`, `ICICI Bank Rewards`,
  `My Cash`. The baseline prompt explicitly instructs *"DO NOT copy payment methods or wallet
  names as programType"* and gives `"Cashback"`/`"Reward Points"` as the canonical types.
  **Luna is following the client's own rule and the incumbent is breaking it.** Scored as
  `CSV_WRONG`; no prompt change. Changing this to match the incumbent would mean weakening a
  correct behaviour to improve an agreement metric.
- **`transactions[].amount` sign (12 rows, `1066621585`)** — Luna `+50000`, incumbent `-50000` on
  `BBPS Payment received … 50,000.00 CR`. The baseline states *"transactions->amount is ALWAYS a
  positive number. Never negate…regardless of direction"*. Luna obeys; the incumbent negates
  credits. `CSV_WRONG`, no change.
- **`dueDate` format (`524015761`)** — Luna `21/07/2026` (the required `DD/MM/YYYY`), incumbent
  `July 21, 2026`. Luna correct; `CSV_WRONG`.
- **`txnType` (110 rows)** — the incumbent emits `null` for `txnType` on ~99 rows while Luna
  classifies them. `txnType` has **0 occurrences** in the baseline prompt, so neither side was
  asked for it; this is a coverage gap, and it is a *secondary* field, not one of the 16. Left
  alone rather than tuned against a mostly-null reference.
- **`utilisationPercent`** — no model emits it. Not a prompt problem; reported as-extracted and
  as-derived. No rule added.

---

## Defect found DURING the full Phase-3 run — proposed for v2, NOT in the scored prompt

**`lastFourDigit` returns mask characters where real digits are printed.** Found while
auditing the full refined run, i.e. *after* the prompt under test was frozen. On 4 cards
Luna returned an `X`-masked value where the PDF prints the digits plainly:

| statement | printed in PDF | Luna | Opus GT |
|---|---|---|---|
| `1051615644` | `4315XXXXXXXX5002` | `XX02` | `5002` |
| `1064311771` | `4315XXXXXXXX8001` | `X001` | `8001` |
| `1147813475` | `0000XXXXXXXX5021` | `XX21` | `5021` |
| `1147813475` | `4315XXXXXXXX4018` | `XX18` | `4018` |

**Root cause is the client prompt itself.** Its `lastFourDigit` rule says *"Replace all
masked characters with `X` and keep only the final 4 characters (e.g. `XXXXXXX56` → `XX56`)"*.
That worked example is for a card whose *final four positions* are genuinely masked. On
ICICI the mask sits in the MIDDLE (`4315XXXXXXXX5002`) and the last four are real digits, so
the "keep the final 4 characters" instruction is being applied to a window Luna has
mis-sliced. Luna is following the letter of a rule written for a different mask layout.

**Proposed v2 rule** (not applied, so the reported numbers stay honest to the prompt tested):

> On ICICI the card number is printed as `NNNNXXXXXXXXNNNN` — the mask is in the MIDDLE and
> the LAST FOUR characters are real digits. `lastFourDigit` = those four digits
> (`4315XXXXXXXX5002` → `"5002"`). Never return an `X` in `lastFourDigit` when the final four
> printed characters are digits.

Deliberately **not** hot-patched mid-run: changing the prompt after 33 of 304 calls would
mean reporting a metric no single prompt ever produced. It is the top candidate for the next
iteration, and the residual-risk section of `ICICI_REPORT.md` carries it.

## Anti-overfit posture

Tuned on 10, tested on the full scoreable set (~304) — a ~30× extrapolation. Phase 3 therefore
reports every metric **both** over all statements **and** excluding these 10 tuning statements:

`2054837190, 1010092654, 820648402, 979848021, 1066621585, 516479745, 1711342048, 524015761,
557652636, 670758880`

Six of the eight added rules are *null-forcing or evidence-requiring* (nos. 1–4, plus the
boilerplate fence), which lowers fabrication risk rather than pattern-matching the 10. Rules 5–7
are layout rules verified against the printed PDF, not against the incumbent's answers. If the
held-out numbers come in materially worse than the all-statements numbers, that is stated plainly
in `ICICI_REPORT.md`.

---

# v2 — post-full-run prompt repair (2026-08-11)

Four edits to `ICICI_PROMPT.txt`, each traced to a defect **measured on the completed
304-statement run** (`final_scores.json`, `notes.desc_defect_classes`, `network_vs_pdf.json`),
not to the 10-statement Phase-1 baseline.

**IMPACT IS UNVERIFIED.** No model inference was run for this change and no re-sweep was
authorised, so every figure below is a **PREDICTION**. `final_scores.json`, `ICICI_FINAL.md`,
`MEASUREMENT_FIX.md` and the report tables are **unchanged** and still describe the v1 prompt.
Nothing here may be quoted as a measured improvement until a full re-sweep is scored.

Prompt file: 11,885 bytes (v1, sha256 `2ba790951037a779a84043bdd2cf3a930514898be9b62a5d32c3eedbe74350f6`)
→ 14,995 bytes (v2, sha256 `79325334991ca5ec423118cbb1c7d70236240bf05519100cc2884129b0f17105`).
`luna_prompt/LUNA_SCHEMA.json` / `GT_SCHEMA` **untouched**, still byte-identical across
icici/hdfc/sbi. No HDFC-only or SBI-only rule was imported: this file contains no rupee-`C`
glyph rule (an HDFC ITFRupee artifact ICICI does not exhibit), no five-column layout rule and no
`TRANSACTIONS FOR` header rule.

## v2.1 — `lastFourDigit`: the mask is in the MIDDLE on this bank

**Measured defect:** `cards[].cardMeta.lastFourDigit` = **95.79%** (387/404 correct; 15
`wrong_value` + 2 `null_when_populated` charged, plus 1 `format_only` = 18 non-identical cells).
**16** of those 18 are Luna returning an `X` mask where the GT has real digits: `XX02`/`5002`,
`X001`/`8001`, `XX21`/`5021`, `XX18`/`4018`, `XX88`/`9188`, `XX05`/`1005`, `XX03`/`8003`,
`XX07`/`5007`, `XX08`/`2008`.
**Decisive corpus evidence:** all **404** GT `lastFourDigit` values are four real digits — zero
contain a mask. ICICI never masks the final four; it prints `NNNNXXXXXXXXNNNN`, mask in the
middle. (This corpus-wide check was performed by the reviewer against the GT extraction; the
per-card JSON is gitignored client PII and is not present in this worktree, so it is not
re-derivable from the committed artifacts here.)

**Root cause is the inherited client rule**, not the model. Removed from `EDGE_CASES`:

> Replace all masked characters with "X" and keep only the final 4 characters
> (e.g. XXXXXXX56 → "XX56", ******56 → "XX56", 4111111111111234 → "1234").
> ... Correct behavior: "XXXX XXXX XXXX XX12" → "XX12" (NOT "0012").

That worked example is written for a **trailing** mask. Kept the anti-fabrication half of the
rule (no padding, no expansion, no backfilling digits into masked positions) and re-pointed it:
`X` is written only for a position that is itself masked in the print. Added to
`ICICI_BANK_RULES` a bank-specific rule: take the four characters at the RIGHT END of that card's
own masked card-number heading (`4315XXXXXXXX5002` → `"5002"`, `0000XXXXXXXX3225` → `"3225"`),
never return `X` when those four printed characters are digits, do not slice from the middle, do
not read the leading BIN fragment, and bind the value to the SAME card section (never another
card's heading, the account number, the `Invoice No`, or a transaction reference).

This supersedes the "Proposed v2 rule" recorded in the section above — it is now applied.

**Cells targeted:** the 16 mask cells of 404. **PREDICTION (UNVERIFIED):** ceiling ≈ **99.3%**
from 95.79%, i.e. ~14 of the 17 charged disagreements resolving. This is a prediction, not a
measurement, and the accuracy figure of record remains **95.79%** until a re-sweep is scored.
**Explicitly NOT fixed:** statement `205034973`, where Luna has both values but **swapped**
between the two cards (`7212`/`2000` vs GT `2000`/`7212`). That is card **ordering**, not mask
slicing, and this edit does not address it. The card-binding clause may or may not help; no
claim is made. The existing `isPrimaryCard` / multi-card grouping rules were left alone rather
than duplicated — they are already explicit ("the card whose transactions are listed first under
the Statement Summary is the primary card").

## v2.2 — trailing country code belongs to the description

**Measured defect:** **92** of the 295 `description` defects are
`dropped_trailing_country_code`, concentrated in six statements —
`232344130` (49), `629527188` (24), `310385621` (13), `283344944` (3), `843301192` (2),
`203051285` (1). A 13th-class case, `GOOGLE *Discovery Plus g.co/helppay#` vs
`... g.co/helppay# US`, also appears among the 12 real character differences.

The token **is printed** in the PDF (verified on `232344130`: `UPI-570397032082-Babasahe b IN`);
the GT keeps it and **Luna drops it**, so Luna is the wrong side here. Added an ICICI rule: the
terminal country-code token (`IN`, `US`, ...) is the last token of the narration, not a separate
column, even though it is laid out flush to the right edge of the description cell — keep it with
its separating space exactly as printed. Paired with a hard guard: **never supply a country code
that is not visibly printed for that row** (not from merchant identity, billing currency, or a
neighbouring row) — the rule must not become a fabrication licence.

**Cells targeted:** 92 (+1 in the real-difference class). **PREDICTION (UNVERIFIED):** partial
recovery only. The 92 concentrate in six statements, which points at a layout/template
interaction in how that cell is rendered rather than a pure instruction gap; full recovery is
**not** guaranteed and no accuracy figure is claimed.

## v2.3 — narration is transcribed, not interpreted

**Measured defect:** **12** `real_character_difference` cells (of 4,097 rows). They are **not**
mostly casing: they are changed reference digits (`389476433876` vs `291859843978`,
`352358915`/`426486404`), a spelling substitution (`SHAMBU` vs `SHAMBHU`), substituted merchant
text (`MYNTRA DESIGNS PRIVATE L Bangalore IN` vs `Myntra BANGALORE IN`), differing truncation
points (`...Amaz` vs `...Amazo`, `TELECOMM` vs `TELECOMMU`), intra-cell line-wrap spacing
(`Google Pay` vs `Google P lay`) and one dropped character (`DND BOSCH ADUGODI` vs
`DND BOSC H ADUGODI 1`).

Strengthened the literal-transcription instruction into an explicit ICICI clause: preserve
printed capitalisation character-for-character; copy reference/UPI numbers digit-for-digit from
that row's own text; do not spell-correct, expand, abbreviate or substitute a merchant name;
stop at ICICI's fixed-width mid-word truncation point exactly.

**Cells targeted:** at most 12 of 4,097. **PREDICTION (UNVERIFIED):** a **small and uncertain**
gain. Several of these 12 are cases where the **GT** carries the PDF line-wrap artifact and Luna
is arguably the stronger side; a prompt instruction cannot make Luna reproduce the reference's
artifact. **No 12-cell fix is claimed.** (Note also that `ICICI_FINAL.md` §1 characterises these
12 as "largely casing" with `fuel Surcharge`/`MAKE MY TRIP` examples; the artifact's own
`real_character_differences` list contains no casing-only case. Reports were out of scope for
this change and were left untouched.)

## v2.4 — `network`: the working rule preserved and sharpened

**Measured result to protect:** Luna fabricates **0** networks; the incumbent **72**
(`network_vs_pdf.json`: on the 190 PDF-adjudicated statements, Luna 248 correct nulls / 0
unsupported, incumbent 41 unsupported, and even the Opus GT 7 unsupported). The v1 rule is
working and was **not weakened**. Every v1 element is retained verbatim in substance: default
null, the four-network fuel-surcharge disclaimer explicitly excluded as evidence, and no
inference from BIN or product name.

Sharpened into explicit evidence-first form: return a network only when the statement visibly
prints **this** card's own network as its own label or logo caption in or beside that card's
section; otherwise null. Extended the never-infer list to marketing/cross-sell copy, merchant
names in the transaction table, rewards/offer wording, and another card's network on the same
statement; added "if you cannot point to a printed network label for that specific card, the
answer is null."

**Cells targeted:** 0 (regression guard). **PREDICTION (UNVERIFIED):** no change to Luna's 0
fabrications; the intent is to keep them at 0 under the other three edits.

## Deliberately NOT changed in v2

- **The 191 `spacing_only` description cells (the single largest defect class).** No rule
  demanding exact whitespace reproduction was added. The **GT itself** carries PDF line-wrap
  artifacts that split inside words (`Google P lay`, `SHIL PA`, `Canara H ospital Cante`), so on
  these cells the reference is the weaker side. Instructing a model to reproduce a renderer's
  arbitrary intra-cell spacing is brittle and template-specific. The recommended treatment is
  **scorer normalisation**, which is a separate decision the user has not approved; no scorer,
  adjudicator or artifact was touched here.
- **`cardDisplayName` (91.86%, 32 disagreements).** Not tuned. The GT convention is incoherent —
  some values generic (`ICICI Bank Credit Card`), some short (`Coral`), and at least two GT
  values are **masked card numbers** (`0000XXXXXXXX1126`) which are plainly not product names;
  the product name is image-only in 123 of 298 PDFs. Tuning against an incoherent reference
  optimises toward noise. **Needs a client naming convention first.**
- **`currency` (100%).** By construction — a single distinct reference value corpus-wide, so the
  metric cannot move. No currency rule added.
- **`isPrimaryCard` / card ordering.** The existing rule is already explicit; a near-duplicate
  was not added.
- **Reports and artifacts.** `final_scores.json`, `ICICI_FINAL.md`, `MEASUREMENT_FIX.md`,
  `report_tables.md`, the scorers and the adjudicator are untouched. They describe the **v1**
  prompt and remain correct as such.
