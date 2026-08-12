# HDFC Gemini-schema capture report

## Executive answer

**MEASURED on 15 statements / 288 transaction rows only. No result here is extrapolated to the 281-statement HDFC corpus.** Both arms used the same 26-leaf converted Gemini schema and the same model; only prompt text differed.

The refined HDFC prompt captured every transaction row and was materially safer than the unmodified Gemini prompt. It was perfect on printed dates, amounts, currency, and transaction reward-point cells; it made 3 direction errors and 1 description error. The generic prompt made 141 direction errors, 46 currency errors, and 8 description errors after probe correction. Thus the generic prompt **does not** still beat the refined prompt on description: corrected results are HDFC **287/288** versus generic **280/288**. The earlier apparent generic advantage was a probe defect.

Several 100% results do not discriminate between prompts: issuer, statement/due dates, all four statement totals/limits, last four digits, transaction date/amount, and printed transaction reward points were 100% in both arms. Currency is also a weak discriminator of general capability because all 288 rows in this sample are INR.

## Probe correction and description result

The identical corrected PDF probe was applied to both arms. It now assembles wrapped narration around the date/amount baseline, replaces control characters with spaces, and excludes a standalone bold `EMI` badge in the adjacent column. It retains whitespace-flexible comparison, semantic unpadded dates, comma-independent Indian-number parsing, word/column boundaries, and ITFRupee `C` handling.

| arm | before | after | change |
|---|---:|---:|---:|
| refined HDFC | 277/288 (11 wrong) | **287/288 (1 wrong)** | +10 |
| generic Gemini | 282/288 (6 wrong) | **280/288 (8 wrong)** | -2 |

Of the original 11 HDFC errors, all 11 were probe artifacts: 4 wrapped descriptions (`10378`, `809843802`, two in `838900283`), 6 separate EMI badges (`567125239`, three in `853991354`, two in `1787504092`), and 1 control-separated `AGGREGATOR\x01EMI...` string. Correcting the badge probe also exposed one previously hidden genuine HDFC error: `629227338` returned `EMI AMAZONMUMBAI` although the badge is separate. Final HDFC genuine errors: **1**.

Of the original 6 generic errors, 5 were probe artifacts (the same 4 wraps plus control normalization) and 1 was genuine. Correct column isolation then exposed 7 generic model errors where it had copied a separate EMI badge (the six named rows plus `629227338`). Final generic genuine errors: **8**.

Geometry confirms the badge is outside narration. Examples: `567125239` badge x=114.36–125.76, narration x=136.29–233.65; `853991354` x=232.25–243.65 versus x=250.74–337.43; `1787504092` x=240.58–251.99 versus x=260.17–347.56; `629227338` x=240.58–251.99 versus x=260.17–312.78. Full evidence is in `geometry_adjudication.json`.

## Direction adjudication and ITFRupee headline

All three remaining refined-arm direction errors are genuine model errors:

| statement / printed row | model | PDF verdict | fitz evidence |
|---|---|---|---|
| `567125239`, `UPICC-687912822278-17-07-2025` | DEBIT | CREDIT | page 1 y=760.37, `+` present, amount green `0x05c747` |
| `567125239`, `UPICC-519872895172-17-07-2025` | DEBIT | CREDIT | page 1 y=774.54, `+` present, amount green `0x05c747` |
| `1723515293`, `Reinstating_Diff_1%_Swiggy_Cbk_Rev` | CREDIT | DEBIT | page 2 y=308.37, no `+`, dark `0x333333` |

The last row is the independently observed defect. PR #6's marker-first rule did not prevent narration semantics (`Cbk_Rev`) from overriding the absence of a printed credit marker. **PREDICTED recommendation, not tested:** make the rule operationally absolute: “If the amount has neither a leading `+` nor trailing `Cr/CR` and is not green, output DEBIT even if narration says reversal, cashback, refund, reinstating, or credit.” No prompt edit or new inference was performed.

The causal A/B is strong:

- 13/15 PDFs embed `ITFRupee`; the two Pixel Play PDFs do not.
- In that font the rupee glyph is stored at code point `0x43`, text-layer ASCII `C`.
- All 141 generic direction errors are true DEBIT rows called CREDIT, and all 141 carry an ITFRupee `C`.
- Generic claimed **181 CREDIT** rows; the PDF has **40** true CREDIT rows.
- ITFRupee layouts: 141/274 errors (51.5%). Pixel Play control: **0/14** errors. The no-font files therefore have a lower error rate, strongly supporting the bare-`C` causal mechanism.
- Independent `+` and green signals agree on 288/288 PDF rows.

## Field-by-field result (all 26 schema leaves)

`H/G` means refined-HDFC/generic. “Verified” uses the PDF as oracle; “not discriminating” means both arms were perfect. Population counts are observed outputs, not accuracy claims.

| schema leaf | measured result on 15 statements |
|---|---|
| `statementMeta.issuerName` | H/G 15/15 verified; not discriminating |
| `statementMeta.statementDate` | H/G 15/15 verified; not discriminating |
| `statementMeta.dueDate` | H/G 15/15 verified, including semantic date comparison; not discriminating |
| `statementLevelSummary.totalAmountDue` | H/G 15/15 verified; not discriminating |
| `statementLevelSummary.totalMinimumAmountDue` | H/G 15/15 verified; not discriminating |
| `statementLevelSummary.totalCreditLimit` | H/G 15/15 verified; not discriminating |
| `statementLevelSummary.availableCreditLimit` | H/G 15/15 verified; not discriminating |
| `cards[].cardMeta.cardDisplayName` | 16/16 populated in both; **UNVERIFIED** because product names may be image-only header artwork and text-layer absence is not proof of fabrication |
| `cards[].cardMeta.productFamily` | H/G 15 populated, 1 null; **UNVERIFIED** for the same image-only reason |
| `cards[].cardMeta.lastFourDigit` | H/G 16/16 verified; not discriminating |
| `cards[].cardMeta.network` | H 16/16, G 15/16 verified; refined wins by one |
| `cards[].cardMeta.isPrimaryCard` | H 7 populated/9 null; G 3/13; **UNVERIFIED** because no reliable printed oracle was established |
| `transactions[].date` | H/G 288/288 verified; not discriminating (format conformance is separate) |
| `transactions[].description` | H **287/288**, G **280/288** after corrected probe |
| `transactions[].amount` | H/G 288/288 verified; not discriminating |
| `transactions[].direction` | H **285/288**, G **147/288** |
| `transactions[].txnType` | H 288 populated; G 247 populated/41 null; **UNVERIFIED semantic classification** because the PDF does not print a transaction-type field and the Gemini schema pins no enum |
| `transactions[].rewardPointsOnThisTransaction` | H 8 populated/280 null, G 7/281; all 7 comparable printed cells correct in both; remaining nulls were not charged without a printed cell oracle |
| `transactions[].currency` | H 288/288; G 242/288. All rows are INR, so H accuracy is measured but cannot establish FX generalization |
| `rewards.programType` | H/G 14 populated/1 null; correctness **UNVERIFIED** by current scalar probe |
| `rewards.openingPoints` | H/G 9 populated/6 null; correctness **UNVERIFIED** |
| `rewards.pointsEarnedThisCycle` | H 12/3, G 14/1 populated/null; correctness **UNVERIFIED** |
| `rewards.pointsRedeemedThisCycle` | H 14/1, G 8/7; correctness **UNVERIFIED**; null frequency alone is not a miss verdict |
| `rewards.closingPoints` | H 9/6, G 10/5; correctness **UNVERIFIED** |
| `rewards.pointsExpiringNext30Days` | H/G 8/7 populated/null; correctness **UNVERIFIED** |
| `rewards.pointsExpiringNext60Days` | H/G 8/7 populated/null; correctness **UNVERIFIED** |

No transactions were dropped: every statement in both arms has `unmatched_model=0` and `unmatched_pdf=0` (288 each).

## Schema diff and orphan rules

The adopted Gemini schema has exactly 26 leaves; GT_SCHEMA has 32; all 26 Gemini leaves are shared. GT_SCHEMA-only leaves are:

| present in GT_SCHEMA, absent from Gemini | present in Gemini, absent from GT_SCHEMA |
|---|---|
| `cards.bigPicture.cardAvailableCreditLimit` | none |
| `cards.bigPicture.cardCreditLimit` | none |
| `rewards.bonusPointsThisCycle` | none |
| `statementMeta.rawStatementId` | none |
| `statementMeta.statementPeriodEnd` | none |
| `statementMeta.statementPeriodStart` | none |

Shared constraint differences: Gemini leaves `transactions.direction` and `transactions.txnType` as unrestricted strings; GT_SCHEMA supplies enums.

`utilisationPercent` is absent from **both** schemas and was deliberately not added, preserving comparability with the client's Gemini baseline. Prior measurement found it emitted in only 1 of 2,636 calls, and no PDF prints a utilization figure; it must be computed in code.

Prompt rules for `financeChargesThisCycle` and `rewards.bonusPointsThisCycle` are orphaned because neither field exists in the adopted schema. Recommendation only: remove unreachable positive instructions while retaining the bonus-to-earned anti-reclassification safeguard if this 26-leaf contract remains; do not add fields to this baseline schema.

Port audit in `PROMPT_CHANGELOG.md`: the generic prompt yielded no safe verbatim HDFC field-rule port. Changes were (1) `txnType` wording made prompt-enforced because the new schema has no enum, (2) the generic Marriott closing-points guidance was inverted based on HDFC evidence so the transaction column/bonus-only summary cannot become `closingPoints`, and (3) direction uppercase vocabulary was made prompt-enforced. The generic bare-`C` rule and mask-preservation rule were rejected on HDFC evidence. No ICICI, Standard Chartered, IDFC FIRST SELECT, SBI, or bare-`C`⇒CREDIT clause was ported.

Scope grep result: **0 occurrences** of `ICICI`, `Standard Chartered`, `IDFC FIRST SELECT`, or word-boundary `SBI`; **0 bare-`C`⇒CREDIT rule**. Occurrences of `CREDIT` are HDFC marker examples/labels, including explicit prohibitions on treating `C` as credit.

## Operational integrity

| arm | calls | outcome/status | finish | prompt tokens | completion tokens | total tokens | completion/prompt |
|---|---:|---|---|---:|---:|---:|---:|
| refined HDFC | 15 | 15 OK / HTTP 200 | 15 `stop` | 142,321 | 23,725 | 166,046 | 0.1667 |
| generic | 15 | 15 OK / HTTP 200 | 15 `stop` | 84,661 | 25,503 | 110,164 | 0.3012 |

No truncation, RATE_LIMITED, 429, non-OK outcome, or failure class occurred. On every call, `prompt_tokens + completion_tokens == total_tokens`; reasoning tokens are inside completion under the OpenAI convention, not additive. No dollar estimate is made.

All 30 calls sent the neutral filename `statement.pdf`, not `os.path.basename`; filename/card-digit leakage was prevented.

Opus-5 GT overlaps all 15 statements and was used as a second reference. One GT/PDF direction disagreement was found: GT calls the printed `1% Swiggy CashBack` ₹5.99 row in `1723515293` DEBIT, while the PDF has both `+` and green and is CREDIT; this is a **REFERENCE_DEFECT** and the PDF wins. The 15 JSONs under `~/Downloads/output/HDFC/JSON/` overlap all statements but are a **prior run, unattributable**—unknown prompt/schema and different workspace—not ground truth.

## Measurement boundary

Everything above labelled measured is local evidence from these 15 statements only. The recommended marker-rule strengthening and orphan cleanup are **PREDICTED / UNTESTED**. No new Luna or Opus calls were made, and no claim is generalized to the 281-statement corpus.
