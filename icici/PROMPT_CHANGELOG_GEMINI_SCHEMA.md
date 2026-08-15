# ICICI — schema parity + prompt refinement changelog

Scope: `icici/*` only. Nothing in `hdfc/` or `sbi/` was read as a source of bank-specific
rules and nothing there was modified.

Every entry names the statement id and the coordinates the rule was derived from, and is
labelled **MEASURED** (observed in the 3-arm run) or **PREDICTED** (reasoned, not yet
separable on an 11-statement sample).

---

## STEP 0 — Provenance of the supplied outputs: **ATTRIBUTABLE**

* `~/Downloads/ICICI_PROMPT.txt` is **byte-identical** to the committed prompt at `ec7b423`
  — md5 `fc21013c7dfbfc38c6503b4a97140ebc`, sha256 `79325334991ca5ec…`, 14,995 B.
* The 11 JSONs in `~/Downloads/output/ICICI/JSON/` carry **32 leaf paths**, i.e. a wider
  schema than the client's 26-leaf type-map (extra: `cards[].bigPicture.card{,Available}CreditLimit`,
  `rewards.bonusPointsThisCycle`, `statementMeta.rawStatementId`,
  `statementMeta.statementPeriod{Start,End}`).
* They are **6/11 byte-identical** to `icici/luna_refined/json` (the 304-statement corpus run),
  which recorded `prompt_sha256 = 2ba79095…` — the **v1** prompt (11,885 B, commit `b2cf196a`).
* The 5 differing statements settle it. On **232344130** the v1 run's descriptions carry
  mid-word splits (`UPI-570244597745-GLOBAL H OSPITALITY SO IN`,
  `UPI-570683062088-PANCHWAT I MARKET`) while the supplied JSONs have them joined
  (`…GLOBAL HOSPITALITY SO IN`). That is exactly the "narration fidelity" repair in v2
  (commit `a22d794a`).

**Conclusion: the supplied JSONs were produced by our current committed v2 prompt.** The
user's six-field report is therefore directly actionable against it, and every count in it
reproduced exactly (network 0/11 populated, closingPoints 0/11, redeemed 5/11,
cardDisplayName null on 232344130 + 952325284, `Coral` vs `ICICI Bank Coral Credit Card`).

---

## PART E — Client schema adopted (the parity gap)

`icici/gemini/GEMINI_SCHEMA.json`, converted from the client's `SCHEMA` type-map
(`~/Downloads/gemini-3-flash--prompt-shcema.txt`, line 64) by `convert_schema.py`.

* **Exactly 26 leaves.** No field added. `statementLevelSummary.utilisationPercent` is
  **absent by design** and left absent — it is not in the client's type-map, no ICICI PDF
  prints a utilisation figure, and it belongs in code. Recorded in
  `GEMINI_SCHEMA_PROVENANCE.json → absent_by_design` together with the other six paths the
  supplied JSONs emit but the contract does not.
* `strict: true` (in the request's `response_format`), `required` lists every property on
  every object, `additionalProperties: false` everywhere.
* **Nullability is a TYPE ARRAY** — `"type": ["string","null"]`, not `anyOf` — matching the
  sibling banks. Both enums were therefore built to include `null` **inside the enum list**:
  * `transactions[].direction` → `["DEBIT","CREDIT",null]`
  * `transactions[].txnType` → the sibling banks' 11-value vocabulary **verbatim**
    (`PURCHASE PAYMENT REFUND REVERSAL CASHBACK FEE TAX INTEREST EMI CASH_ADVANCE UPI`) plus
    `null`. Deliberately **not** narrowed to the values this 11-statement sample happens to
    contain, so cross-bank comparison stays valid.
* **Enums verified safe before any sweep.** Smoke test on exactly one PDF (693462745):
  HTTP 200, `finish_reason=stop`, token identity 14996+650=15646. Enums did **not** need to
  be reverted.
* `assert_schema.py` guards the contract; `test_assert_schema.py` **negative-tests it in both
  directions** and it rejects all 8 injected defects (enum-omits-null on a nullable leaf;
  enum-contains-null on a non-nullable leaf; 27th leaf; leaf removed; leaf retyped;
  disallowed key; type-inconsistent enum member; leaf renamed) while accepting the real schema.

### Descriptions added — only where guidance was previously ZERO
The gap audit found 5 fields with no prose guidance anywhere; 4 got descriptions, plus
`txnType` (one incidental prompt mention) and `totalCreditLimit`:
`productFamily`, `openingPoints`, `pointsExpiringNext30Days`, `pointsExpiringNext60Days`,
`txnType`, `totalCreditLimit`. All wording is ICICI-derived, not copied from HDFC/SBI.

**Deliberately NOT described**, with reasons (the sibling parity gap, considered on merits):
* `isPrimaryCard` — HDFC needed a description because its prompt said nothing (0→14 populated).
  ICICI already has an explicit measured rule and the field is populated on **21/21** cards.
  Adding one risked contradicting the prompt.
* `network` — already governed by the evidence-first rule that measures **0 fabrications**.
* `cardDisplayName` — see the honesty note under (f).

---

## STEP 2 — Gap audit: PORT_IN list is **EMPTY**

The client's file carries the whole type-map on **line 64**. **14 of 26 fields hit only that
line** — `cardDisplayName, currency, direction, isPrimaryCard, issuerName, network,
openingPoints, pointsExpiringNext30Days, pointsExpiringNext60Days, productFamily,
totalAmountDue, totalCreditLimit, totalMinimumAmountDue, txnType`. Excluding it, **no field
has client prose guidance our prompt lacks**, the same result as HDFC and SBI.
Line numbers for every hit are printed by `gemini/gap_audit.py` so the exclusion is auditable.

**Reverse check (the SBI regression case): none found.** All five positive client
instructions are still present in our prompt, including the `CR, C, +` → CREDIT /
`DR, D, -` → DEBIT marker allowlist.

**Reconciliation:** `icici/GENERIC_PROMPT.txt` (10,088 B) is **not** the client's generic
prompt — it is a larger, already-enriched intermediate. The true client generic prompt is
`icici/gemini/GEMINI_GENERIC_PROMPT.txt`, extracted from the client file's `SYSTEM_PROMPT`
block and **md5-identical to `hdfc/gemini/`'s copy** (`cb77453a…`). Arm C uses the latter.

---

## STEP 3 — Deletions: rules commanding fields the schema cannot emit

With `additionalProperties:false` these were active instruction/schema **CONFLICTS**.

| # | Deleted | Old lines | Note |
|---|---|---|---|
| D1 | `INFERENCE_RULES` allowlist + finance-charge inference | 57–62 | `financeChargesThisCycle` unemittable |
| D2 | Utilisation-percent inference | 63–67 | `utilisationPercent` unemittable |
| D3 | `BONUS_POINTS_RULE` (whole section) | 73–78 | `bonusPointsThisCycle` unemittable |
| D4 | `rawStatementId` / "Invoice No" rule | 121–122 | `rawStatementId` unemittable |

**Every line of every section was checked before deleting.** `orphan_audit.py` flags any
orphan line that also names an in-schema field. Three fired:

* **L65** (`If totalCreditLimit and totalAmountDue (or cardCreditLimit and
  cardLevelTotalAmountDue …)`) names in-schema `totalAmountDue`/`totalCreditLimit`, but only
  as *inputs to the unemittable utilisation output*. Nothing is lost by deleting it. **Dropped.**
* **L179** (`For all date fields (statementDate, dueDate, statementPeriodStart,
  statementPeriodEnd, transaction date …): always format DD/MM/YYYY`) — **LIVE** for
  `statementDate`, `dueDate` and transaction dates. **RELOCATED SEMANTICALLY**: reworded to
  `For all date fields (statementDate, dueDate, and every transaction date)`. Wording had to
  change because it enumerated two unemittable fields.
* **L182** (sanity check bounding transaction dates by `statementPeriodStart/End` and
  `statementDate`, with the day/month swap heuristic) — **LIVE**. **RELOCATED SEMANTICALLY**:
  the statement-period bound became "use the statement period printed on the page only as a
  reading aid for this check; it is not itself an output field", preserving the `statementDate`
  bound, the two-month window and the swap-and-revalidate heuristic.

**Contrast with HDFC, worth recording:** on HDFC the `BONUS_POINTS_RULE` had a live tail
governing `pointsEarnedThisCycle` and had to be relocated. ICICI's equivalent section
(73–78) is entirely about `bonusPointsThisCycle` and carries **no** in-schema rule, so it was
fully deletable. Verified line-by-line, not assumed from the sibling.

**Consequence handled:** deleting D1 emptied the `INFERENCE_RULES` allowlist, leaving
`MISSING_DATA_RULE`'s "EXCEPT for fields listed in INFERENCE_RULES" clause dangling. It was
rewritten into an unconditional evidence-first null policy: *"there is no field you are
allowed to compute when the statement does not print it."*

---

## STEP 4 — The six flagged fields, adjudicated against measured PDF evidence

**Four of the six are REFERENCE DEFECTS, not prompt defects.** The incumbent CSV reads
`detectionSource=GEMINI`, `modelName=gemini-3-flash-preview` / `databricks-gemini-3-flash`
for all 315 rows — it is the client's own parser, not ground truth. It covers **11/11** of
these statements. (`ICICI.xlsx` could not be opened: `openpyxl` is absent and this machine
blackholes pypi, so coverage there is **UNVERIFIED**; the CSV's 11/11 made it unnecessary.)

### (a) network — REFERENCE DEFECT. **No prompt change; rule deliberately not weakened.**
Measured with a word-bounded, whitespace-flexible, de-duplicated matcher
(`probe/measure_net_l4.py`, self-tested):
* 10 of 11 statements contain **exactly one** occurrence each of VISA / MASTERCARD / RUPAY /
  AMERICAN EXPRESS, all inside the single four-network line
  `For RuPay/American Express/ Visa/Mastercard Credit Cards: Fuel surcharge and corresponding
  Goods and Services Tax` (e.g. 1529317035 p1 bbox `[304.85, 794.39, 349.49, 800.19]`).
  It lists all four networks and identifies nothing.
* **952325284: zero network tokens** in the text layer.
* **205034973's "VISA ×3" is a FALSE POSITIVE.** The two extra hits are the cardholder's own
  city in the address block — `visakhapatnam` p1 `[38.25,106.83,87.5,113.58]` and
  `Visakhapatnam` p1 `[38.25,123.77,88.66,130.52]`. They appear only under
  whitespace-stripped (LOOSE) matching; strict word-bounded matching rejects them. A prior
  probe (`probe/measure_all.py`) reported them as real because it called
  `page.search_for()` once per regex match — quadratic double counting — and had no word
  boundaries. **Corrected count: 0 genuine non-disclaimer network hits anywhere in the corpus.**
* **Two further reasons a BIN can never settle this**, both added to the prompt as hardening:
  the leading four printed characters are themselves frequently masked (`0000XXXXXXXX6043`,
  8 of 21 cards), and two cards on the **same** statement carry different leading digits
  (1737715836: `3747XXXXXXXX4004` beside `5241XXXXXXXX8004`).
* **HARD CEILING, reported not "fixed":** on 952325284 the network (`VISA PLATINUM`) and the
  product name (`Coral`) exist **only inside a raster marketing image** — an Aadhaar-update
  banner showing a *specimen* card `4375 5174 1234 5678`. A rule to mine artwork was **not**
  added; instead the prompt now states explicitly that a network name appearing only inside a
  picture is never evidence.
* Incumbent asserts 4 network values (1737715836 `Mastercard`; 606359443
  `American Express`,`MasterCard`; 693462745 `Visa`) — all BIN-consistent but **printed
  nowhere**. **MEASURED: all three arms produced 0 network values.** Our null is correct.

### (b) closingPoints — REFERENCE DEFECT. **One real prompt conflict fixed.**
* `Closing Balance` occurs **exactly once** per statement, as row **`SL. No` 18** of the
  pre-printed illustrative Minimum-Amount-Due example, in a table whose money column header
  is a bare `` ` `` at x=523.54. Label at x=85.04, value **26,958.20** at x≈517–537,
  y=**117.66** — *identical y across statements*, alongside frozen specimens (99.00, 1,200.00,
  220.00, 3.60, 958.20) and hardcoded dates (`Nov 08, 2025`, `Oct 08, 2023`). Money, never points.
  1529317035 and 952325284 contain no `Closing` string at all.
* **All four rewards layouts were catalogued geometrically across all pages** (below) and
  **none prints a closing or opening POINTS balance.** So `closingPoints = null` is correct on
  **11/11**, and `0/11 populated` is the right answer, not a shortfall.
* **PROMPT DEFECT FOUND AND FIXED:** the inherited generic rule instructed the model to
  populate `closingPoints` from labels *including* "Closing Balance" and "Closing Points".
  On ICICI "Closing Balance" is *always* the money specimen, so that rule directly
  contradicted the boilerplate-exclusion rule in the same prompt. Replaced with:
  *"closingPoints is a REWARDS BALANCE … a figure whose own column or row label is about
  points, not about money"*, plus an explicit ICICI clause that `Closing Balance` must never
  populate it. **MEASURED: 0 wrong on all three arms** (latent conflict removed;
  0 cells changed on this sample — **PREDICTED** value is on statements where the model might
  otherwise have taken the specimen).
* **Incumbent populates closingPoints on 5 statements — 1737715836=661, 232344130=301,
  354990911=10, 606359443=725, 693462745=0 — and in every case it equals its own
  `pointsEarnedThisCycle`.** That is the one-cell-into-two-fields defect, UNBACKED by any
  printed balance, and it is the *incumbent* committing it. **MEASURED: our duplication
  invariant is 0 equal / 0 UNBACKED on all three arms.**

### (c) programType — CONVENTION difference; the incumbent breaks the client's own rule.
Programme names quoted with coordinates; four layouts, four names:
* Layout 1 heading `ICICl Bank Rewards` p1 y=635.9 x=66.52 (the final I renders as `l`).
* Layout 2 `MakeMyTrip My Cash` p1 y=653.82 x=70.14 — **the user's `myCash` hypothesis is
  CONFIRMED**; the PDF prints `My Cash`.
* Layout 3 `EARNINGS` p1 y=653.82 x=91.14, with `Amazon Pay balance*` y=679.4 x=112.56.
* Layout 4 `Points Transferred to PAYBACK (Acc:…)` p1 y=504.69 x=361.0.
* The client's own prompt says *"DO NOT copy payment methods or wallet names as programType"*
  and lists `Cashback` / `Reward Points` / `Membership Rewards`. `Amazon Pay balance` and
  `PAYBACK` **are** wallet/programme brand names — **so the incumbent is doing exactly what
  the client's instruction forbids.** Our class value complies.
* **Decisive corroboration — MEASURED:** arm C (the client's unmodified generic prompt)
  reproduces the incumbent's values *exactly*: `My Cash`, `ICICI Bank Rewards`,
  `Amazon Pay balance`, `MakeMyTrip My Cash`, `PAYBACK`. **arm C scores 0/11**; arms A and B
  score **11/11**.
* Change made: `programType` restricted to the closed vocabulary in the prompt, plus a
  per-layout mapping and an explicit "never emit these brand names" list.
  **MEASURED: 11/11 in both A and B** (hardening; no cell moved between A and B).

### (d) pointsRedeemedThisCycle — **OURS ALREADY CORRECT. The duplication signature is a FALSE ALARM.**
Ran the duplication test as instructed rather than assuming:
* **205034973 — `TWO_DISTINCT_CELLS_EQUAL`.** `Earned` (label x=58.71) → **33** at x=**69.55**;
  `Earnings transfered to Amazon Pay balance` (label x=112.23) → **33** at x=**146.5**. Both
  values on line y=698.3, two separate printed cells 77pt apart.
* **952325284 — `TWO_DISTINCT_CELLS_EQUAL`.** `Points Earned` x=206.92 and
  `Points Transferred to PAYBACK (Acc:9401165034966009)` x=361.0 (both y=504.69); values
  **146** at x=**246.37** and **146** at x=**494.68** (y=523.84) — 248pt apart.
* So the equality is **genuinely printed** in both cases, not one cell written twice.
  A sub-agent asked to measure this concluded "the same numeric value is printed once but
  labelled with both headers"; that contradicts its own quoted evidence and is **wrong** —
  the direct measurement above supersedes it.
* Verified the existing rule: `Points Transferred to PAYBACK` appears verbatim
  (952325284 p1 y=504.69 x=361.0), so *transferred = redeemed* is **evidence-backed**.
* No candidate redeemed value traces to a transaction row.
* Incumbent instead fabricates `redeemed = 0` on the five Layout-1 statements, which print no
  redeemed cell at all. **Our 5/11 populated is exactly right** — and scored against the
  geometric oracle, **arms A, B and C all score 11/11.**
* Change made: the four-layout catalogue with **explicit column binding**, plus
  *"EARNED AND TRANSFERRED MAY LEGITIMATELY BE EQUAL … Only refuse to fill the second field
  when the statement prints just ONE cell."* **PREDICTED** robustness; **MEASURED** no change
  on this sample (already 11/11).

### (e) lastFourDigit — **OURS CORRECT; PR #8's fix holds. Incumbent materially worse.**
* Model output matches the measured card-number headings on **21/21 cards, 11/11 statements
  exactly, 0 values containing `X`** — in all three arms.
* The mask is mid-string as documented (`4315XXXXXXXX2000` → `2000`).
* **The claimed card-ORDERING bug on 205034973 does not reproduce.** Headings in reading order
  are `0000XXXXXXXX7212` (p1 y=392.14) then `4315XXXXXXXX2000` (p1 y=420.42); the model emits
  `['7212','2000']` — same order, not swapped.
* Both space-separated-mask filenames checked: **952325284 genuinely prints spaced masks**
  (`4375 XXXX XXXX 4008` p1 y=219.64, and `0000 XXXX XXXX 0599` y=619.49 /
  `4375 XXXX XXXX 4008 - SAMIK DAS` y=709.49 as `Card Number :` cells), whereas
  **1529317035 prints its mask unspaced** (`0000XXXXXXXX6043`) despite the spaced filename.
* Change made (guards only, no behaviour change measured): the spaced-mask form, the
  `Card Number : … - <NAME>` labelled-cell form with the trailing cardholder name to ignore,
  and *"NEVER take a card number from a PICTURE"* — the page-1 banners display specimen cards
  `4378 XXXX XXXX XXXX` and `4375 5174 1234 5678`. **PREDICTED**; **MEASURED 21/21 unchanged.**
* **The filename is NOT a usable oracle** and was not used as one: the filename card is absent
  from the PDF text on 1737715836, 1770339352, 232344130 and 693462745, and two different
  statements (1737715836, 1770339352) carry the *same* filename card `4748XXXXXXXX5000`.
* Incumbent: emits only ONE card on 7 of 11 statements, `[]` on 238910814, and
  **`XXXX9003`** on 1529317035 — the masked-slicing defect PR #8 fixed on our side.
* No HDFC or SBI mask rule was imported.

### (f) cardDisplayName — **the reference IS incoherent; this field cannot be scored strictly.**
Honesty requirement, discharged:
* **The product name is VECTOR ARTWORK, not text.** The page-1 top-right identity band holds
  22–42 vector drawing ops and only marketing rasters. `SAPPHIRO`, `CORAL`, `RUBYX`/`RUBIX`
  are **absent from the entire text layer** of their statements, yet both Luna and the
  incumbent emit them — they are reading letterforms visually. The two even disagree on
  spelling (`Rubyx` vs `RubiX`), which is what two models reading stylised glyphs produces.
* The reference convention is incoherent, as warned: bare (`Sapphiro`), expanded
  (`Coral Credit Card`, `ICICI Bank RubiX Credit Card`), the generic logo string
  (`ICICI Bank Credit Card` on 232344130, whose identity slot is **visually empty** — verified
  by render), and missing (`[]` on 238910814).
* **RECOMMENDATION TO THE CLIENT (a convention decision, not a modelling fix):** decide
  whether `cardDisplayName` is the bare product name or the expanded marketing name, and
  accept that on ICICI it is only recoverable by a vision-capable reader. Until then this
  field must not be used as an accuracy metric — tuning against it optimises toward noise.
* Change made — **ONE convention, chosen on evidence** (`ICICI_CARD_IDENTITY`): the bare
  printed form; null when the identity block shows no product name; never from a marketing
  sentence (`*My Cash earned on qualifying expenditure using MakeMyTrip ICICI Bank Credit
  Card …`, 1529317035 p1 y≈601–720 — the source of the expanded leak); never from card artwork.
* **MEASURED, and reported as CONSISTENCY not accuracy:** arm A is now internally consistent —
  `MakeMyTrip`, `Sapphiro`, `amazon pay`, `Coral`, `Rubyx`, null ×3. Fill rate is
  **17/21, unchanged from arm B**, but arm B was inconsistent (`ICICI Bank Coral Credit Card`
  on 238910814 vs `Coral` on 354990911, and `['ICICI Bank Coral Credit Card', None]` on
  354990911 — one card null).
* **`productFamily` improved 9/21 → 17/21 (+8 cells, MEASURED)** — the largest single
  measured gain in this change, analogous to HDFC's `isPrimaryCard` 0→14.
* **One acknowledged loss:** 693462745 goes `HPCL Coral` → `null`. Its identity block carries
  an **HP logo mark, not a text product name** (verified by render), so null is defensible
  under the chosen convention, but this is genuinely **AMBIGUOUS_IN_PDF** — a co-brand
  identifiable from a logo image. Net fill is unchanged because 354990911 gained a card.
* Arm C emitted the **cardholder's name** `SAMIK DAS` as cardDisplayName on 952325284 — a
  defect the refined prompt prevents.

---

## Other measured ICICI facts added to the prompt

* **Rupee sign: ICICI encodes ₹ as U+0060 GRAVE ACCENT (backtick) in font `RupeeForadian`**
  — measured on all 11; the amount header prints `Amount (in `)`. **ICICI shows ZERO instances
  of the HDFC ITFRupee `C`-as-rupee artifact.** So on ICICI a `C` beside an amount really is a
  CREDIT marker, and the existing direction rule is safe here. The HDFC glyph rule was
  **not** imported; ICICI's own encoding is described instead.
* Indian digit grouping (`1,23,456.78`) stated explicitly — read the whole grouped number.
* Layout 1's footnote *"The total points earned are inclusive of points earned on iShop"*
  (p1 y=688.19/697.79) means iShop is a **subset**: the prompt now forbids adding the two.

---

## Regression gate

| Gate | Arm A (new) | Arm B (prev) | Verdict |
|---|---|---|---|
| `transactions[].description` EXACT | 109/172 (63.4%) | 109/172 (63.4%) | **identical — no regression** |
| `transactions[].description` ≥0.95 | 153/172 (89.0%) | 153/172 (89.0%) | **identical** |
| transaction row recovery | 172/172, 0 missing, 0 extra | 172/172, 0 missing, 0 extra | held |
| `network` fabrications | 0 | 0 | held |
| `lastFourDigit` | 21/21 cards, 11/11 sets | 21/21, 11/11 | held |
| `pointsEarned` / `pointsRedeemed` | 11/11 / 11/11 | 11/11 / 11/11 | held |
| `productFamily` populated | **17/21** | 9/21 | **+8 improved** |
| `cardDisplayName` populated | 17/21 (consistent) | 17/21 (inconsistent) | fill equal, consistency improved |

**No revert recommended.** Description exactness is byte-identical between A and B, so the
prompt edit trades nothing away on the field where the incumbent still leads on the project's
strict headline.

**Note on the description metric:** 63.4% here is *not* comparable to the project's 92.70%
headline. This oracle reconstructs each narration by joining the PDF's word cells with single
spaces, so ICICI's mid-word wraps normalise differently. It is a *relative* gate — A vs B on
identical logic — not a restatement of the headline figure.

**Honest summary of what this change did and did not buy:** on these 11 statements arm A
**ties** arm B on every correctness-scored field and improves `productFamily` (+8) and
cardDisplayName consistency. The substantive deliverables are the 26-leaf schema with
null-safe enums and a negative-tested guard, the removal of four instruction/schema
conflicts and one internal contradiction, and documented evidence that **four of the six
flagged fields were reference defects rather than prompt defects**. Arm C shows what the
client baseline costs: programType 0/11, transaction date 44.2%, currency 129/172,
isPrimaryCard 0/21.
