# SBI Luna Shape-2 verification — 2026-08-14

## Scope and provenance

- Model: `databricks-gpt-5-6-luna`
- Request: native PDF, one call per statement, `reasoning_effort=medium`, strict
  `json_schema`, neutral filename `statement.pdf`
- Current prompt: `sbi/SBI_PROMPT.txt`, SHA-256
  `b7e06b291803cbcf46bbc6a07af427363d545d3c87d39ee8f64113c8058b3b92`
  (commit `cb36d3b`)
- Schema: `sbi/gemini/GEMINI_SCHEMA.json`, SHA-256
  `53b67ea4c0cd4e5f1caa916b9f1c251b3104815ffbbcce56ea9373a3b8f45c3c`;
  `assert_schema.py` passed with exactly 26 leaves and null-safe enums.
- All coordinates below are page-1 PDF word bounding boxes `(x0,y0,x1,y1)` in PDF
  points, obtained from the source PDF with PyMuPDF. Downloads were read-only.

## Task 1 — 15-statement Shape-2 verification

Every call finished `stop` with outcome `OK`; no call saw a 429. Shape 2a passed
7/7 (100% hit rate), and Shape 2b passed 8/8. Opening and redeemed points stayed null
on every statement. For Shape 2a, `closingPoints` and `pointsEarnedThisCycle` both equal
the printed cashback amount by design.

| sid | shape | printed figure and page-1 bbox | closingPoints | pointsEarnedThisCycle | result |
|---|---:|---|---:|---:|---|
| 877262556 | 2a | 1667 `(40.03,405.12,58.18,415.29)` | 1667 | 1667 | PASS |
| 850576275 | 2a | 3925 `(40.75,405.12,58.90,415.29)` | 3925 | 3925 | PASS |
| 533941211 | 2a | 297 `(42.15,405.12,55.76,415.29)` | 297 | 297 | PASS |
| 406632776 | 2a | 50 `(44.87,405.28,53.77,415.25)` | 50 | 50 | PASS |
| 1765558172 | 2a | 11 `(44.87,405.28,53.77,415.25)` | 11 | 11 | PASS |
| 369606524 | 2a | 375.25 `(32.60,401.97,57.50,412.14)` | 375.25 | 375.25 | PASS |
| 1118980175 | 2a | 1,525.25 `(29.22,401.97,60.88,412.14)` | 1525.25 | 1525.25 | PASS |
| 1120623464 | 2b | 0 `(55.54,402.76,60.08,412.93)` | null | 0 | PASS |
| 1602650870 | 2b | 0 `(55.54,402.76,60.08,412.93)` | null | 0 | PASS |
| 186473748 | 2b | 0 `(55.25,402.76,59.79,412.93)` | null | 0 | PASS |
| 658182494 | 2b | 0 `(55.25,402.76,59.79,412.93)` | null | 0 | PASS |
| 393366914 | 2b | 0 `(55.25,402.76,59.79,412.93)` | null | 0 | PASS |
| 746869826 | 2b | 433 `(51.50,402.76,65.11,412.93)` | null | 433 | PASS |
| 1024471256 | 2b | 19 `(53.50,402.92,62.40,412.89)` | null | 19 | PASS |
| 1707857175 | 2b | 1072 NeuCoins `(48.84,402.92,66.63,412.89)` | null | 1072 | PASS |

Records: `sbi/gemini/json_shape2_verify/`.

## Task 2 — arm-E regression gate

The old seven terminal records and five infrastructure failures were all produced with
the pre-`cb36d3b` prompt SHA
`f7ff966bd7f23082dece02f9fccb92ee2d3c8a8c7a5193d1e4288921b5472b94`.
They are preserved in `json_armE_pre_cb36d3b/`. Mixing those records with the new gate
expectations would have been incoherent, so all 12 arm-E statements were re-driven under
the current prompt SHA `b7e06b291803...`. Arm D remains the older pre-row-completeness
baseline with prompt SHA `756213dac6e42e8458a7f0dcfab66665db2b8761bb3c9f7c3527b7bb344ea37c`.

Result: **AMBER — 12/12 terminal model records, one protected-field failure.**

- Closing points: all 12 match the current gate expectations, including the six Shape-2a
  values and the genuine 18,068 SHOP & SMILE balance on `221159806`.
- Duplication invariant: 6 BACKED equalities, 0 UNBACKED equalities. The negative
  synthetic self-test passed. All six equalities are backed by page-1 Shape-2a headers.
- Network: null 12/12.
- Expiry fields: `pointsExpiringNext60Days` null 12/12;
  `pointsExpiringNext30Days` null 11/12. `221159806` returned `0`, so the gate correctly
  failed rather than reporting green.
- Row completeness: `1707857175` returned 71 rows and is row-EXACT against
  `pdf_rowtruth.json`. The current-prompt historical repeats remain 11/12 row-exact.
- Transaction types: zero off-vocabulary values; REFUND still fires (17 rows).
- SAVINGS-grid prohibition: neither `For this year` nor `From the card issue date`
  occurs in any current arm-E record.
- Infrastructure: 12/12 outcome `OK`, finish reason `stop`; no current arm-E call saw a
  429. No infrastructure event was scored as a model failure.

The exact gate output is reproducible with:
`caffeinate -dimsu python3 sbi/gemini/gate_armE.py` (exit 1 because of the expiry value).

## Token accounting

`usage_raw` is preserved verbatim on every current record. For all 27 calls, the identity
`prompt_tokens + completion_tokens == total_tokens` holds, and
`reasoning_tokens <= completion_tokens`. Reasoning tokens are inside completion tokens
(OpenAI convention) and were not added again.

| run | calls | prompt | completion | total | reasoning (inside completion) | mean total/statement | median | max |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Shape-2 verification | 15 | 315234 | 18546 | 333780 | 5062 | 22252.00 | 21568 | 27051 |
| Current-prompt arm E | 12 | 248598 | 16257 | 264855 | 4307 | 22071.25 | 21604.5 | 27393 |
| Combined | 27 | 563832 | 34803 | 598635 | 9369 | 22171.67 | 21586 | 27393 |

No dollar estimate is reported because Luna pricing is unpublished.
