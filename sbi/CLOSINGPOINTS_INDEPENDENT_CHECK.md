# SBI reward-points closing balance: independent PDF check

## CORRECTION — 2026-08-14

**PREDICTED correction (static PDF inspection; model output UNVERIFIED):** The conclusion
below that `closingPoints=null` was correct on 15/15 is wrong under the user's SBI
cashback convention. The detector anchored on three balance headings and never inspected
the separate page-1 `CARD CASHBACK SUMMARY FOR THIS STATEMENT` / `CASHBACK SUMMARY FOR
THIS STATEMENT` blocks. Its passing positive control on 221159806 proved only that the
detector finds layouts it was written to look for; it did not validate coverage of the
cashback layout.

**PREDICTED corrected result:** 7/15, not the previously reported 5/15, are Shape 2a
cashback statements. This includes 369606524 and 1118980175, whose heading omits `CARD`.
For those seven, the printed signed `CASHBACK / Amount` figure is mapped to both
`closingPoints` and `pointsEarnedThisCycle` by convention. The remaining eight Shape 2b
points statements retain `closingPoints=null`. See `SBI_REWARDS_SHAPE2_CORRECTION.md` for
the per-statement coordinates. No model rerun was performed, so extraction accuracy is
**UNVERIFIED**.

## Verdict

`rewards.closingPoints = null` is **correct on all 15/15 target statements**. None prints a labelled closing/current reward-points or cashback-points balance. No target prints an opening/previous points balance, a redeemed/expired points value, or a numeric 30/60-day points-expiry disclosure.

This conclusion is geometry-based. All pages of every PDF were scanned. The three-column `SAVINGS AND BENEFITS SECTION` is a cumulative-flow grid (`For this statement`, `For this year`, `From the card issue date`), not a balance grid. Page-1 `Previous Balance` belongs to the rupee-denominated ACCOUNT SUMMARY.

Coordinates below are PDF points, `(x0,y0)` unless a center is explicitly marked. Page numbers are 1-based.

## 15-statement answer

| Statement | Pages scanned | Rewards layouts (page; heading y) | Labelled points closing/current balance | Other requested points fields | ACCOUNT SUMMARY balance-like hit |
|---|---:|---|---|---|---|
| 1120623464 | 7 | p1 `Reward Point Summary` y364.5; p2 `SAVINGS AND BENEFITS SECTION` y60.1 | **No**; no label/coordinate | opening no; redeemed no; 30/60 expiry no | p1 `Previous Balance` (29.8,303.8) = 34.21 at (44.3,333.5), money |
| 1602650870 | 7 | p1 `Reward Point Summary` y364.5; p2 savings y60.1 | **No** | no / no / no | p1 (29.8,303.8) = 0.10 at (46.5,333.5), money |
| 1707857175 | 8 | p1 `NeuCoins Summary` y364.7; p3 savings y60.3 | **No** | no / no / no | p1 (30.3,304.0) = 1,28,099.54 at (33.2,333.7), money |
| 1765558172 | 7 | p1 `CARD CASHBACK SUMMARY FOR THIS STATEMENT` y363.9; p2 savings y60.3 | **No** | no / no / no | p1 (30.3,304.0) = 0.00 at (46.6,333.7), money |
| 186473748 | 7 | p1 `Reward Point Summary` y364.5; p2 savings y60.3 | **No** | no / no / no | p1 (29.8,303.8) = 1,202.57 at (38.1,333.5), money |
| 369606524 | 7 | p1 `CASHBACK SUMMARY FOR THIS STATEMENT` y363.7; p2 savings y60.1 | **No** | no / no / no | p1 (29.8,303.8) = 5,563.81 at (37.9,333.5), money |
| 406632776 | 7 | p1 `CARD CASHBACK SUMMARY FOR THIS STATEMENT` y363.9; p2 savings y60.3 | **No** | no / no / no | p1 (30.3,304.0) = 15.00 CR at (30.0,334.4), money |
| 533941211 | 7 | p1 `CARD CASHBACK SUMMARY FOR THIS STATEMENT` y363.7; p2 savings y60.1 | **No** | no / no / no | p1 (29.8,303.8) = 28,033.22 at (35.8,333.5), money |
| 658182494 | 7 | p1 `Reward Point Summary` y364.5; p2 savings y60.1 | **No** | no / no / no | p1 (29.8,303.8) = 69,590.78 at (36.4,333.5), money |
| 746869826 | 7 | p1 `Reward Point Summary` y364.5; p2 savings y60.3 | **No** | no / no / no | p1 (29.8,303.8) = 53,852.21 at (36.4,333.5), money |
| 850576275 | 8 | p1 `CARD CASHBACK SUMMARY FOR THIS STATEMENT` y363.7; p3 savings y60.3 | **No** | no / no / no | p1 (29.8,303.8) = 65,815.99 at (36.4,333.5), money |
| 1024471256 | 7 | p1 `Reward Point Summary` y364.7; p2 savings y60.3 | **No** | no / no / no | p1 (30.3,304.0) = 19,788.10 at (36.2,333.7), money |
| 1118980175 | 7 | p1 `CASHBACK SUMMARY FOR THIS STATEMENT` y363.7; p2 savings y60.1 | **No** | no / no / no | p1 (29.8,303.8) = 1,50,994.51 at (32.6,333.5), money |
| 393366914 | 7 | p1 `REWARD SUMMARY` y364.5; p2 savings y60.1 | **No** | no / no / no | p1 (29.8,303.8) = 10,686.10 at (36.4,333.5), money |
| 877262556 | 7 | p1 `CARD CASHBACK SUMMARY FOR THIS STATEMENT` y363.7; p2 savings y60.1 | **No** | no / no / no | p1 (29.8,303.8) = 0.00 at (46.5,333.5), money |

Counts: labelled closing/current points balance **0/15**; opening/previous points balance **0/15**; redeemed/expired points value **0/15**; numeric 30-day or 60-day expiry disclosure **0/15**.

## Full rewards grids

For every savings grid, the verbatim headers and approximate x-centres are `For this statement` (x≈257.2), `For this year` (x≈381.2), and `From the card issue date` (x≈505.9). Values below are bound by x-position, not extraction order. Each row is `label @ (x0,y0): [statement, year, issue-date]`, with each value shown as `text@(x0,y0)`.

- **1120623464, p2:** `Cash Back`@(37.2,97.8): [0.00@(250.1,97.8), 0.00@(369.3,97.8), 0.00@(498.8,97.8)]; `Petrol Surcharge Waiver`@(38.2,108.8): [0.00@(250.1,109.6), 0.00@(369.3,109.6), 27.80@(496.5,109.6)]; `Reward Points`@(37.5,120.6): [0@(255.8,120.6), 0@(375.0,120.6), 4791@(497.6,120.6)]. Page 1: `Points Earned`@(34.8,387.0) = 0@(55.5,402.8).
- **1602650870, p2:** `Cash Back`@(37.2,97.8): [0.00@(250.1,97.8), 0.00@(369.3,97.8), 100.00@(494.2,97.8)]; `Petrol Surcharge Waiver`@(38.2,108.8): [0.00@(250.1,109.6), 0.00@(369.3,109.6), 1,758.40@(490.8,109.6)]; `Reward Points`@(37.5,120.6): [0@(255.8,120.6), 500@(370.4,120.6), 15667@(495.3,120.6)]. Page 1 `Points Earned`@(34.8,387.0) = 0@(55.5,402.8).
- **1707857175, p3:** `Petrol Surcharge Waiver`@(39.0,97.9): [5.36@(250.3,99.5), 5.36@(369.5,99.5), 5.36@(498.9,99.5)]; `NeuCoins`@(38.6,113.7): [1072@(249.1,115.3), 5077@(368.3,115.3), 6590@(497.8,115.3)]. Page 1 `NeuCoins`@(37.5,387.2) = 1072@(48.8,402.9).
- **1765558172, p2:** `Offer Cashback`@(37.0,97.9): [0.00@(250.3,97.9), 0.00@(369.5,97.9), 0.00@(498.9,97.9)]; `Petrol Surcharge Waiver`@(38.3,109.0): [0.00@(250.3,109.7), 0.00@(369.5,109.7), 0.00@(498.9,109.7)]; `Card Cashback`@(37.0,120.8): [11@(253.6,120.8), 11@(372.8,120.8), 3919@(497.8,120.8)]. Page 1 `CASHBACK`@(23.5,380.7), `Amount`@(31.6,390.7) = 11@(44.9,405.3).
- **186473748, p2:** `Cash Back`@(37.2,98.0): [0.00@(250.1,98.0), 0.00@(369.3,98.0), 0.00@(498.8,98.0)]; `Petrol Surcharge Waiver`@(38.2,109.0): [0.00@(250.1,109.8), 0.00@(369.3,109.8), 66.94@(496.5,109.8)]; `Reward Points`@(37.5,120.8): [0@(255.8,121.6), 277@(370.4,121.6), 8423@(497.6,121.6)]. Page 1 `Points Earned`@(34.8,387.0) = 0@(55.2,402.8).
- **369606524, p2:** `Offer Cashback`@(36.5,97.8): [0.00@(250.1,97.8), 0.00@(369.3,97.8), 0.00@(498.8,97.8)]; `Petrol Surcharge Waiver`@(37.4,108.8): [0.00@(250.1,108.8), 0.00@(369.3,108.8), 0.00@(498.8,108.8)]; `Card Cashback`@(36.5,120.6): [375.25@(245.8,120.6), 0.00@(369.1,120.6), 0.00@(500.2,120.6)]. Page 1 `CASHBACK`@(27.2,383.1), `Amount`@(34.6,392.2) = 375.25@(32.6,402.0).
- **406632776, p2:** `Offer Cashback`@(37.0,97.9): [0.00@(250.3,97.9), 0.00@(369.5,97.9), 0.00@(498.9,97.9)]; `Petrol Surcharge Waiver`@(38.3,109.0): [0.00@(250.3,109.7), 0.00@(369.5,109.7), 0.00@(498.9,109.7)]; `Card Cashback`@(37.0,120.8): [50@(253.6,120.8), 460@(370.5,120.8), 460@(500.0,120.8)]. Page 1 `CASHBACK`@(23.5,380.7), `Amount`@(31.6,390.7) = 50@(44.9,405.3).
- **533941211, p2:** `Offer Cashback`@(36.5,97.8): [0.00@(250.1,97.8), 0.00@(369.3,97.8), 0.00@(498.8,97.8)]; `Petrol Surcharge Waiver`@(37.4,108.8): [0.00@(250.1,109.6), 0.00@(369.3,109.6), 0.00@(498.8,109.6)]; `Card Cashback`@(36.5,120.6): [297@(251.2,120.6), 4736@(368.1,120.6), 15424@(495.3,120.6)]. Page 1 `CASHBACK`@(23.1,380.5), `Amount`@(31.4,390.7) = 297@(42.2,405.1).
- **658182494, p2:** `Cash Back`@(37.2,97.8): [0.00@(250.1,97.8), 0.00@(369.3,97.8), 0.00@(498.8,97.8)]; `Petrol Surcharge Waiver`@(38.2,108.8): [0.00@(250.1,109.6), 0.00@(369.3,109.6), 35.95@(496.5,109.6)]; `Reward Points`@(37.5,120.6): [0@(255.8,121.4), 0@(375.0,121.4), 12968@(495.3,121.4)]. Page 1 `Points Earned`@(34.8,387.0) = 0@(55.2,402.8).
- **746869826, p2:** `Cash Back`@(37.2,98.0): [0.00@(250.1,98.0), 0.00@(369.3,98.0), 0.00@(498.8,98.0)]; `Petrol Surcharge Waiver`@(38.2,109.0): [0.00@(250.1,109.8), 0.00@(369.3,109.8), 0.00@(498.8,109.8)]; `Reward Points`@(37.5,120.8): [433@(251.2,121.6), 1891@(368.1,121.6), 6107@(497.6,121.6)]. Page 1 `Points Earned`@(34.8,387.0) = 433@(51.5,402.8).
- **850576275, p3:** `Offer Cashback`@(36.5,98.0): [0.00@(250.1,98.0), 0.00@(369.3,98.0), 0.00@(498.8,98.0)]; `Petrol Surcharge Waiver`@(37.4,109.0): [0.00@(250.1,109.8), 0.00@(369.3,109.8), 0.00@(498.8,109.8)]; `Card Cashback`@(36.5,120.8): [3925@(249.0,121.6), 13255@(365.9,121.6), 90025@(495.3,121.6)]. Page 1 `CASHBACK`@(23.1,380.5), `Amount`@(31.4,390.7) = 3925@(40.8,405.1).
- **1024471256, p2:** `Cash Back`@(37.5,97.9): [0.00@(250.3,97.9), 0.00@(369.5,97.9), 0.00@(498.9,97.9)]; `Petrol Surcharge Waiver`@(39.0,109.0): [0.00@(250.3,109.7), 0.00@(369.5,109.7), 10.42@(496.7,109.7)]; `Reward Points`@(38.0,120.8): [19@(253.6,120.8), 1311@(368.3,120.8), 4203@(497.8,120.8)]. Page 1 `Points Earned`@(35.2,387.2) = 19@(53.5,402.9).
- **1118980175, p2:** `Offer Cashback`@(36.5,97.8): [0.00@(250.1,97.8), 0.00@(369.3,97.8), 0.00@(498.8,97.8)]; `Petrol Surcharge Waiver`@(37.4,108.8): [0.00@(250.1,109.6), 5.40@(369.3,109.6), 5.40@(498.8,109.6)]; `Card Cashback`@(36.5,120.6): [1,525.25@(242.9,120.6), 0.00@(369.3,120.6), 0.00@(500.4,120.6)]. Page 1 `CASHBACK`@(27.2,383.1), `Amount`@(34.6,392.2) = 1,525.25@(29.2,402.0).
- **393366914, p2:** `Cash Back`@(37.2,97.8): [0.00@(250.1,97.8), 0.00@(369.3,97.8), 0.00@(498.8,97.8)]; `Petrol Surcharge Waiver`@(38.2,108.8): [0.00@(250.1,109.6), 0.00@(369.3,109.6), 0.00@(498.8,109.6)]; `Reward Points`@(37.5,120.6): [0@(255.8,121.4), 0@(375.0,121.4), 5828@(497.6,121.4)]. Page 1 `REWARD SUMMARY` with `Reward Points`@(28.5,387.0) = 0@(55.2,402.8); it has no `Current Stmt Period`/`Till Last Cycle`/`Earned Till Date` columns in this PDF.
- **877262556, p2:** `Offer Cashback`@(36.5,97.8): [0.00@(250.1,97.8), 0.00@(369.3,97.8), 0.00@(498.8,97.8)]; `Petrol Surcharge Waiver`@(37.4,108.8): [0.00@(250.1,109.6), 0.00@(369.3,109.6), 0.00@(498.8,109.6)]; `Card Cashback`@(36.5,120.6): [1667@(249.0,120.6), 1667@(368.1,120.6), 1667@(497.6,120.6)]. Page 1 `CASHBACK`@(23.1,380.5), `Amount`@(31.4,390.7) = 1667@(40.0,405.1).

## Label/header inventory (verbatim)

Distinct rewards-grid row/value labels: `Amount`; `CASHBACK`; `Card Cashback`; `Cash Back`; `NeuCoins`; `Offer Cashback`; `Petrol Surcharge Waiver`; `Points Earned`; `Reward Points`.

Distinct rewards-grid column headers: `For this statement`; `For this year`; `From the card issue date`. There is no `Closing Balance`, `Current Balance`, `Previous Balance`, redemption/expiry, 30-day, or 60-day header in any target rewards block.

Distinct rewards-block headings: `CARD CASHBACK SUMMARY FOR THIS STATEMENT`; `CASHBACK SUMMARY FOR THIS STATEMENT`; `NeuCoins Summary`; `REWARD SUMMARY`; `Reward Point Summary`; `SAVINGS AND BENEFITS SECTION`.

## Positive control, image check, and probe audit

Positive control `/Users/mayanck.bihani/Downloads/output/SBI/PDF/decrypt_encrypt_221159806_...pdf`: p1 `SHOP & SMILE SUMMARY`@(175.0,362.2). The detector binds `Previous Balance`@(27.6,391.7)=18068@(36.8,405.1), `Earned`@(107.9,391.7)=0@(112.6,405.1), `Redeemed/Expired`@(160.3,387.0)+`/Forfeited`@(171.7,392.8)=0@(179.3,405.1), `Closing Balance`@(229.6,391.7)=**18068**@(238.5,405.1), and `Points Expiry Details`@(326.4,391.7)=`NONE`@(341.5,405.7). Thus the same x-column method fires on a known printed closing balance.

Relevant target page 1s contain 4 or 5 image objects; each savings page contains 7. Image rectangles include a full-page background `(0.0,0.1,610.7,1008.2)` on page 1 and a savings-section background around `(34.5,57.1,572.3,97.3)`. Text spans for headings, labels, and values sit on top of these backgrounds. There is therefore no evidence of an unparsed image-only balance cell; the PDF text layer fully exposes the grids. This is not a claim that pages contain no images.

Probe bug disclosed: the first probe used the unavailable `python` command and failed before reading PDFs; it was rerun with `python3`. A second display-only helper used an ID regex that failed when it reached `decrypt_encrypt_*`; this truncated only that diagnostic printout, not the page scan or geometry extraction. The final extraction used `_(\d+)_19` and covered all 15. The naive `balance` scan also finds page-1 ACCOUNT SUMMARY money values; those were explicitly rejected by y/x geometry and the rupee column.

## Follow-up: redeemed / opening / label gap

Scope is exactly the 15 PDFs in `Downloads/SBI/PDF`; denominators below do not extrapolate beyond those 15. Coordinates are Poppler PDF points in `[xMin,yMin,xMax,yMax]` form. The PDF, not the incumbent CSV, is treated as truth.

### Redeemed and opening disagreements

| statement | field | incumbent | ours | printed? | label + page + coords | verdict |
|---|---|---:|---:|---|---|---|
| 1120623464 | pointsRedeemedThisCycle | 0 | null | No | No redeemed/expired/forfeited cell. p1 `Points Earned` `[34.76,387.02,84.32,397.19]` = `0` `[55.54,402.76,60.08,412.93]`; this is earned, not redeemed. | **CORRECTLY_NULL** |
| 1118980175 | pointsRedeemedThisCycle | -1544.5 | null | No points value | p1 transaction `CARD CASHBACK CREDIT` `[70.65,459.42,160.61,469.59]` has rupee `Amount ( \` )` = `1,544.50` `[373.31,459.42,404.97,469.59]`, credit. No redeemed/expired/forfeited rewards cell exists. | **CORRECTLY_NULL** |
| 393366914 | pointsRedeemedThisCycle | 0 | null | No | No redeemed/expired/forfeited cell. p1 `Reward Points` `[28.47,387.02,80.50,397.19]` = `0` `[55.25,402.76,59.79,412.93]`; the block is a single earned figure. | **CORRECTLY_NULL** |
| 658182494 | pointsRedeemedThisCycle | 0 | null | No | No redeemed/expired/forfeited cell. p1 `Points Earned` `[34.76,387.02,84.32,397.19]` = `0` `[55.25,402.76,59.79,412.93]`. | **CORRECTLY_NULL** |
| 1120623464 | openingPoints | 0 | null | No | Neither candidate matches: p1 money `Previous Balance` `[29.78,303.83,83.75,312.72]` = `34.21` `[44.35,333.50,64.72,343.67]`; p2 lifetime `Reward Points` row `[37.54,120.61,89.57,130.78]`, `From the card issue date` = `4791` `[497.60,120.61,515.75,130.78]`. Neither is 0 or an opening-points cell. | **CORRECTLY_NULL** |

All 4/4 redeemed disagreements and the 1/1 opening disagreement are incumbent defects. In particular, the opening value is neither (i) the money Previous Balance nor (ii) the lifetime flow; it appears to be a copied/default zero, plausibly from the printed earned/current-statement zero.

`CARD CASHBACK CREDIT` occurs in 4/15 PDFs: 533941211 (₹1,400.00), 369606524 (₹424.00), 850576275 (₹1,984.00), and 1118980175 (₹1,544.50). The incumbent converts it to redeemed only for 1118980175 (negative because it is a credit), while leaving redeemed null for the other 3/4 identical-shape cases. Porting that behavior would improve 1 incumbent-comparison cell but would break 3 currently correct cells, and would be semantically wrong in all four because these are money transactions, not points cells. The p1 ACCOUNT SUMMARY is also a rupee table and supplies no rewards field.

### Earned-label inventory and prompt coverage

Counts are statements containing the label in the rewards summary/grid, not raw text hits. Earned agreement is incumbent versus ours on the statements containing that label; all 15/15 corpus-level earned cells agree.

| verbatim label | statements | named in prompt? | earned correct where present? |
|---|---:|:---:|---|
| `Amount` | 7 | YES | 7/7 |
| `CASHBACK` | 7 | YES | 7/7 |
| `Card Cashback` | 7 | YES | 7/7 |
| `Cash Back` | 7 | YES | 7/7; explicitly excluded when distinct Card Cashback exists |
| `NeuCoins` | 1 | YES | 1/1 |
| `Offer Cashback` | 7 | YES | 7/7; explicitly excluded as the selected accrual row |
| `Petrol Surcharge Waiver` | 15 | YES | 15/15; explicitly mapped to no rewards field |
| `Points Earned` | 6 | YES | 6/6 |
| `Reward Points` | 7 | YES | 7/7 |

There is no prompt label-inventory gap in this 15-statement set: 9/9 distinct labels are named, and no unnamed label has a cell to fix.

### Recommendations

- **pointsRedeemedThisCycle — NO CHANGE.** The prompt already says a single-current-statement rewards block has no redeemed cell and explicitly forbids using `CARD CASHBACK CREDIT`. Changing it to mimic the incumbent would fabricate four money-to-points mappings and would turn 3/4 currently correct nulls into errors.
- **openingPoints — NO CHANGE.** The only disagreement has no printed opening-points cell. Its `Previous Balance` is ₹34.21 and its lifetime Reward Points flow is 4791; the incumbent 0 matches neither. The prompt already separates money, current-cycle flow, lifetime flow, and opening balance.
- **pointsEarnedThisCycle labels — NO CHANGE.** All 9/9 observed labels are already named and earned agrees 15/15. There is no cell an additional label would fix.

Probe bug disclosed: an initial exact-label inventory naively counted 24 occurrences of `Amount` because it included transaction-table headers. Restricting the scan to the page-1 rewards block and the Savings and Benefits rewards grid gives the correct 7-statement rewards-label count. No model accusation or recommendation uses the naive count.
