# SBI `closingPoints` adjudication — 12 statements

## CORRECTION — 2026-08-14

**PREDICTED correction (static PDF inspection; model output UNVERIFIED):** The verdict
below that only 221159806 should have non-null `closingPoints` is wrong under the user's
SBI cashback convention. The adjudication detector anchored on three balance headings
and did not inspect the separate page-1 cashback block. The 221159806 positive control
proved only that the detector recognizes the layouts it searches for.

**PREDICTED corrected 12-set targets:** Shape 2a maps the printed cashback figure to
`closingPoints` for 1036185244=106, 1511624796=476, 905768587=453,
515948911=-1467, 369606524=375.25, and 1118980175=1525.25. Equality with
`pointsEarnedThisCycle` is intentional only for those PDF-backed Shape 2a blocks.
221159806 remains 18068; the four Shape 2b statements and 1390952698 remain null.

**PREDICTED separate correction for 1390952698:** The earlier description of arm D's
`openingPoints=53724` as a lifetime-flow defect was wrong. The PDF prints `REWARD
SUMMARY` columns `Current Stmt Period` (x=45.5), `Till Last Cycle` (x=195.9), and
`Earned Till Date` (x=328.3), with values 0 (x=80.1), 53724 (x=211.9), and 12380
(x=356.4), all at y=403.7. Shape 3 correctly maps `Till Last Cycle` to
`openingPoints`; arm D was correct. The incumbent 12380 is `Earned Till Date`, which the
prompt intentionally forbids. No behavior change was made for Shape 3.

No model rerun was performed because authentication is dead; all new extraction behavior
and accuracy are **UNVERIFIED**. The superseded analysis remains below as history.

## Verdict

Arm A is a regression on this field. It copied a current-cycle accrual flow into both
`closingPoints` and `pointsEarnedThisCycle` on 11 of 12 statements (counting non-null
`0 == 0`). A stock and a flow
are distinct. The only genuine closing balance in this sample is **221159806 = 18068**;
the PDF-correct result for the other 11 statements is `closingPoints=null`.

The PDF, not the Gemini-3-Flash-derived incumbent ground truth, is authoritative. In
particular, the lifetime totals printed in the `From the card issue date` column are not
closing balances.

## Method

Coordinates are PyMuPDF `get_text("dict")` span coordinates, with pages numbered from
1 here. Page-1 reward blocks were searched for explicit stock labels (`Previous
Balance`, `Closing Balance`) and flow labels. Every later `SAVINGS AND BENEFITS SECTION`
was bound in two dimensions: row label near `x=37`; column header at `y≈83`; current
cycle near `x=250`, YTD near `x=369`, lifetime near `x=497`. Extraction order was not
used because these PDFs can emit values before headers.

## Per-statement PDF evidence and targets

| statement | page-1 evidence (label/value coordinates) | later-grid current row | PDF-correct target `(closing, earned, redeemed, opening, program)` |
|---|---|---|---|
| 1036185244 | p1 `CARD CASHBACK SUMMARY…` `(101.3,363.7)`; `CASHBACK=106` `(42.2,405.1)` | p2 `Card Cashback=106` `(251.2,120.6)` | `(null,106,null,null,Cashback)` |
| 1118980175 | p1 `CASHBACK SUMMARY…` `(101.3,363.7)`; `1525.25` `(29.2,402.0)` | p2 `Card Cashback` `(x≈37,y=120.6)` = `1525.25` `(242.9,120.6)` | `(null,1525.25,null,null,Cashback)` |
| 1120623464 | p1 `Reward Point Summary` `(180.0,364.5)`; `Points Earned` `(34.8,387.0)` = `0` `(55.5,402.8)` | p2 `Reward Points` `(x≈37,y=120.6)` = `0` `(255.8,120.6)` | `(null,0,null,null,Reward Points)` |
| 1152718739 | p1 `Reward Point Summary`; `Points Earned=12` `(53.1,402.8)` | p2 `Reward Points` row `(x≈37,y=121.7)`: `12/720/1879` `(244.7/370.4/497.6,121.7)` | `(null,12,null,null,Reward Points)` |
| 1390952698 | p1 `REWARD SUMMARY` `(188.1,363.9)`: `Current Stmt Period=0` `(80.1,403.7)`, `Till Last Cycle=53724` `(211.9,403.7)`, `Earned Till Date=12380` `(356.4,403.7)`; no closing label | p2 `Cash Back=0` `(250.3,97.9)`, irrelevant | `(null,0,null,53724,Reward Points)` |
| 1511624796 | p1 `CARD CASHBACK SUMMARY…` `(101.3,363.7)`; cashback value `476` `(42.9,405.1)` | p2 `Card Cashback` `(x≈37,y=120.8)` = `476` `(251.2,121.6)` | `(null,476,null,null,Cashback)` |
| 1707857175 | p1 `NeuCoins Summary` `(189.3,364.7)`; `NeuCoins` `(37.5,387.2)` = `1072` `(48.8,402.9)` | p3 `NeuCoins` `(38.6,113.7)` = `1072` `(249.1,115.3)` | `(null,1072,null,null,NeuCoins)` |
| 221159806 | p1 `SHOP & SMILE SUMMARY` `(175.0,362.2)`; `Previous Balance=18068`, `Earned=0`, `Redeemed/Expired/Forfeited=0`, `Closing Balance=18068` at `(36.8/112.6/179.3/238.5,405.1)` beneath labels at `y≈392` | p2 `Reward Points=0/2/18068` `(255.8/375.0/495.3,121.4)`; last is lifetime, not balance source | `(18068,0,0,18068,Reward Points)` |
| 369606524 | p1 `CASHBACK SUMMARY…` `(101.3,363.7)`; `375.25` `(32.6,402.0)` | p2 `Card Cashback` `(x≈37,y=120.6)` = `375.25` `(245.8,120.6)` | `(null,375.25,null,null,Cashback)` |
| 393366914 | p1 `REWARD SUMMARY` `(187.2,364.5)`; `Reward Points` `(28.5,387.0)` = `0` `(55.2,402.8)` | p2 `Reward Points` `(x≈37,y=120.6)` = `0` `(255.8,121.4)` | `(null,0,null,null,Reward Points)` |
| 515948911 | p1 `CARD CASHBACK SUMMARY…` `(101.3,363.7)`; cashback value `-1467` `(39.3,405.1)` | p3 `Card Cashback` `(x≈37,y=120.6)` = `-1467/-1467/44136` `(247.6/366.8/495.3,121.4)` | `(null,-1467,null,null,Cashback)` |
| 905768587 | p1 `CARD CASHBACK SUMMARY…` `(101.3,363.7)`; cashback value `453` `(42.2,405.1)` | p2 rows `Offer Cashback / Petrol Surcharge Waiver / Card Cashback` `(x≈37,y=97.8/108.8/120.6)`; program row `453` `(251.2,120.6)` | `(null,453,null,null,Cashback)` |

`Cash Back` and `Offer Cashback` are savings/offer rows when a distinct `Card Cashback`
row exists. `Petrol Surcharge Waiver` is a fee waiver and maps to no rewards field on
all 12 statements.

## Existing-arm values

Tuples are `(closing, earned, redeemed, opening, program)`; `—` means null.

| statement | A (new, broken) | B (previous) | C (client) | PDF target |
|---|---|---|---|---|
| 1036185244 | `106,106,—,—,Cashback` | `—,106,—,—,Cashback` | `—,106,—,—,CASHBACK` | `—,106,—,—,Cashback` |
| 1118980175 | `1525.25,1525.25,—,—,Cashback` | `—,1525.25,—,—,Cashback` | `—,1525.25,—,—,CASHBACK` | `—,1525.25,—,—,Cashback` |
| 1120623464 | `0,0,—,—,Reward Points` | `—,0,—,—,Reward Points` | `—,0,—,—,IRCTC Reward Points` | `—,0,—,—,Reward Points` |
| 1152718739 | `12,12,—,—,Reward Points` | `—,12,—,—,Reward Points` | `—,12,—,—,Reward Points` | `—,12,—,—,Reward Points` |
| 1390952698 | `0,0,—,53724,Reward Points` | `—,0,—,—,Reward Points` | `12380,0,—,53724,Club Vistara` | `—,0,—,53724,Reward Points` |
| 1511624796 | `476,476,—,—,Cashback` | `—,476,—,—,Cashback` | `—,476,782,—,CASHBACK` | `—,476,—,—,Cashback` |
| 1707857175 | `1072,1072,—,—,NeuCoins` | `1072,1072,—,—,NeuCoins` | `1072,—,—,—,NeuCoins` | `—,1072,—,—,NeuCoins` |
| 221159806 | `18068,0,0,18068,Reward Points` | same | same | same |
| 369606524 | `375.25,375.25,—,—,Cashback` | `—,375.25,—,—,Cashback` | `—,375.25,424,—,CASHBACK` | `—,375.25,—,—,Cashback` |
| 393366914 | `0,0,—,—,Reward Points` | `0,0,—,—,Reward Points` | `0,—,—,—,REWARD_POINTS` | `—,0,—,—,Reward Points` |
| 515948911 | `-1467,-1467,—,—,Cashback` | `—,-1467,—,—,Cashback` | `—,1467,—,—,CASHBACK` | `—,-1467,—,—,Cashback` |
| 905768587 | `453,453,—,—,Cashback` | `—,453,—,—,Cashback` | `—,453,—,—,CASHBACK` | `—,453,—,—,Cashback` |

## Four-arm PDF scoring

Each cell is exact matches out of 12 against the adjudicated PDF targets above, not the
stored Gemini-derived ground truth.

| arm | closing | earned | redeemed | opening | program | total |
|---|---:|---:|---:|---:|---:|---:|
| A — shipped regression (`f230115`) | 1 | 12 | 12 | 12 | 12 | 49/60 |
| B — previous prompt | 10 | 12 | 12 | 11 | 12 | 57/60 |
| C — client prompt | 9 | 9 | 10 | 12 | 3 | 43/60 |
| D — corrected prompt | 12 | 12 | 12 | 12 | 12 | 60/60 |

Duplication (`closingPoints == pointsEarnedThisCycle`, both non-null, including zero):
A **11/12**, B **2/12**, C **0/12**, D **0/12**. The earlier 10/12 observation omitted
one zero-equality; the archived files establish the reproducible 11/12 count.

Arm D returned the PDF target tuple on all 12 statements. Because `runner.py` is
unchanged and exposes only A/B/C switches, D was executed through its A prompt slot and
then frozen under `json_armD`; the archived broken A remains under
`json_armA_broken_f230115`, and `json_armA` is restored to the shipped A records.

## Ground-truth caveat

The stored GT was generated by `gemini-3-flash-preview` (`detectionSource=GEMINI`). It
is an incumbent contract, not source truth. Its lifetime/grid interpretations lose to
the labelled PDF geometry; a corrected arm may therefore score worse against GT while
being more faithful to the statements. Concrete disagreements include GT
`1390952698.closingPoints=53724` (the PDF labels 53724 `Till Last Cycle`, not closing),
GT `1707857175.closingPoints=1072` (the PDF presents a single NeuCoins cycle flow), and
GT `393366914.closingPoints=0` (the PDF labels the zero as current-statement reward
points). Arm D deliberately returns null for all three closing fields.

## Run and contract validation

The D run completed 12/12 with HTTP 200 outcomes, zero calls observing 429, and no
403/IP-ACL failures. The request retained filename `statement.pdf`, concurrency 2,
96,000 max tokens, medium reasoning, strict `GEMINI_SCHEMA.json`, and no body `model`
key. `assert_schema.py` passes: exactly 26 leaves; both schema enums include null.

The 26-file `~/Downloads/output/SBI/` stat manifest (absolute path, integer mtime, size)
has the same pre/post-run SHA-256 `46e939d654f36e7c2a910c05c80651a4e916ec26694d8ad94835f4a1847eeb78`;
the 12-PDF subset remains `0e367df6b1f733e28c437488c3f2ece1a4a7fb0903d3d067ef2cefb4067f6da0`.
These reproduce the original BSD-`stat` manifest encoding (literal `\\t` separators),
confirming that no source-file mtime or size changed.
