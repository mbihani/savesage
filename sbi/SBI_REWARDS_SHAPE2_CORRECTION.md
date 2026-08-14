# SBI Shape 2 cashback correction — static evidence

Status: **PREDICTED** from PyMuPDF page-1 text geometry; model behavior and accuracy are
**UNVERIFIED** because authentication is dead. Coordinates are `(x0,y0)` PDF points.
“Old” means the pre-change prompt contract, not a fresh model result.

## 15-statement set (`Downloads/SBI/PDF`)

| sid | header @ coordinate | printed label/value @ coordinate | class | old closingPoints | PREDICTED new closingPoints |
|---|---|---|---|---:|---:|
|1120623464|Reward Point Summary @(180.0,364.5)|Points Earned; 0 @(55.5,402.8)|2b|null|null|
|1602650870|Reward Point Summary @(180.0,364.5)|Points Earned; 0 @(55.5,402.8)|2b|null|null|
|1707857175|NeuCoins Summary @(189.3,364.7)|NeuCoins; 1072 @(48.8,402.9)|2b|null|null|
|1765558172|CARD CASHBACK SUMMARY FOR THIS STATEMENT @(101.3,363.9)|CASHBACK / Amount; 11 @(44.9,405.3)|2a|null|11|
|186473748|Reward Point Summary @(180.0,364.5)|Points Earned; 0 @(55.2,402.8)|2b|null|null|
|369606524|CASHBACK SUMMARY FOR THIS STATEMENT @(101.3,363.7)|CASHBACK / Amount; 375.25 @(32.6,402.0)|2a|null|375.25|
|406632776|CARD CASHBACK SUMMARY FOR THIS STATEMENT @(101.3,363.9)|CASHBACK / Amount; 50 @(44.9,405.3)|2a|null|50|
|533941211|CARD CASHBACK SUMMARY FOR THIS STATEMENT @(101.3,363.7)|CASHBACK / Amount; 297 @(42.2,405.1)|2a|null|297|
|658182494|Reward Point Summary @(180.0,364.5)|Points Earned; 0 @(55.2,402.8)|2b|null|null|
|746869826|Reward Point Summary @(180.0,364.5)|Points Earned; 433 @(51.5,402.8)|2b|null|null|
|850576275|CARD CASHBACK SUMMARY FOR THIS STATEMENT @(101.3,363.7)|CASHBACK / Amount; 3925 @(40.8,405.1)|2a|null|3925|
|1024471256|Reward Point Summary @(181.0,364.7)|Points Earned; 19 @(53.5,402.9)|2b|null|null|
|1118980175|CASHBACK SUMMARY FOR THIS STATEMENT @(101.3,363.7)|CASHBACK / Amount; 1525.25 @(29.2,402.0)|2a|null|1525.25|
|393366914|REWARD SUMMARY @(187.2,364.5)|Reward Points; 0 @(55.2,402.8)|2b|null|null|
|877262556|CARD CASHBACK SUMMARY FOR THIS STATEMENT @(101.3,363.7)|CASHBACK / Amount; 1667 @(40.0,405.1)|2a|null|1667|

## 12-statement set (`Downloads/output/SBI/PDF`)

| sid | header @ coordinate | printed label/value @ coordinate | class | old closingPoints | PREDICTED new closingPoints |
|---|---|---|---|---:|---:|
|1036185244|CARD CASHBACK SUMMARY FOR THIS STATEMENT @(101.3,363.7)|CASHBACK / Amount; 106 @(42.2,405.1)|2a|null|106|
|1120623464|Reward Point Summary @(180.0,364.5)|Points Earned; 0 @(55.5,402.8)|2b|null|null|
|1152718739|Reward Point Summary @(180.0,364.5)|Points Earned; 12 @(53.1,402.8)|2b|null|null|
|1390952698|REWARD SUMMARY @(188.1,363.9)|Current / Till Last / Earned Till Date; 0 / 53724 / 12380 @(80.1 / 211.9 / 356.4,403.7)|3|null|null|
|1511624796|CARD CASHBACK SUMMARY FOR THIS STATEMENT @(101.3,363.7)|CASHBACK / Amount; 476 @(42.9,405.1)|2a|null|476|
|1707857175|NeuCoins Summary @(189.3,364.7)|NeuCoins; 1072 @(48.8,402.9)|2b|null|null|
|369606524|CASHBACK SUMMARY FOR THIS STATEMENT @(101.3,363.7)|CASHBACK / Amount; 375.25 @(32.6,402.0)|2a|null|375.25|
|515948911|CARD CASHBACK SUMMARY FOR THIS STATEMENT @(101.3,363.7)|CASHBACK / Amount; -1467 @(39.3,405.1)|2a|null|-1467|
|1118980175|CASHBACK SUMMARY FOR THIS STATEMENT @(101.3,363.7)|CASHBACK / Amount; 1525.25 @(29.2,402.0)|2a|null|1525.25|
|221159806|SHOP & SMILE SUMMARY @(175.0,362.2)|Closing Balance; 18068 @(238.5,405.1)|1|18068|18068|
|393366914|REWARD SUMMARY @(187.2,364.5)|Reward Points; 0 @(55.2,402.8)|2b|null|null|
|905768587|CARD CASHBACK SUMMARY FOR THIS STATEMENT @(101.3,363.7)|CASHBACK / Amount; 453 @(42.2,405.1)|2a|null|453|

## Shape 2b recommendation — not implemented

**PREDICTED recommendation:** keep Shape 2b unchanged. Its printed labels are lone
`Points Earned`, `Reward Points`, or `NeuCoins` figures at the coordinates above, not
closing-balance labels. A points programme can print a genuine running strip: 221159806
shows `Previous Balance / Earned / Redeemed-Expired-Forfeited / Closing Balance` with
18068 / 0 / 0 / 18068. Therefore copying a lone Shape 2b earned figure into
`closingPoints` would recreate the prior mis-slot defect. This recommendation was
explicitly **not implemented**; runtime accuracy remains **UNVERIFIED**.

## 1390952698 correction

**PREDICTED correction:** `openingPoints=53724` is correct for Shape 3 because 53724 is
printed under `Till Last Cycle`; 12380 is under forbidden `Earned Till Date`. No Shape 3
behavior was changed. Model behavior is **UNVERIFIED**.
