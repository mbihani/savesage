# SBI row completeness on long statements — statement 1707857175, the 28 Apr 2026 cluster

Model `databricks-gpt-5-6-luna`, `max_tokens` 96000, `reasoning_effort` medium, client
26-leaf `GEMINI_SCHEMA.json`, one user message, native PDF, filename `statement.pdf`.
Transport is `sbi/gemini/runner.py` **unchanged** — the new drivers import it and inject
only prompt text into `runner.PROMPTS`.

---

## 1. Headline

The PDF prints **71** transaction rows. The client's short prompt (arm C) emitted 71; both
refined prompts emitted 70. **Arm C was right and our prompt was dropping a genuine
printed row.** A one-bullet, SBI-scoped, positive completeness rule was added. Nothing was
removed or shortened, and every gated field held.

| metric on 1707857175 | pre-fix | post-fix |
|---|---|---|
| row count == 71 | 8 / 12 | **12 / 12** |
| **row-exact vs the PDF** | **4 / 12** | **11 / 12** |

Row-exactness, Fisher exact one-sided: **p = 0.0047** (p = 0.0006 pooling the 4 earlier
pre-fix samples). On row count alone: p = 0.047.

**Row count is a misleading metric on this defect** — see §3.

---

## 2. PDF-derived true row count (established before looking at any model output)

`pdf_rowtruth.py` reconstructs the printed table from span geometry. A row is a printed
line whose **rightmost span is a lone `C`/`D`/`T`/`M` inside the page's own learned marker
band**, carrying an Indian-grouped decimal amount. The leading date is optional (see the
probe bug in §6). Wrapped-narration continuation lines are excluded because they carry
neither an amount nor a marker.

| sid | PDF rows | armB | armC | armD |
|---|---|---|---|---|
| **1707857175** | **71** | 70 | **71** | 70 |
| 515948911 | 56 | 56 | 56 | 56 |
| 369606524 | 16 | 16 | 16 | 16 |
| 1511624796 | 15 | 15 | 15 | 15 |
| 905768587 | 8 | 8 | 8 | 8 |
| 1036185244 | 7 | 7 | 7 | 7 |
| 1118980175 | 7 | 7 | 7 | 7 |
| 1152718739 | 4 | 4 | 4 | 4 |
| 1390952698 | 4 | 4 | 4 | 4 |
| 221159806 | 2 | 2 | 2 | 2 |
| 393366914 | 2 | 2 | 2 | 2 |
| 1120623464 | 1 | 1 | 1 | 1 |

**The probe agrees with all three arms on 11 of 12 statements.** That independent agreement
is what licenses trusting it on the twelfth. The verdict is 71, so the fix target is the
PDF, not arm C.

### The 28 Apr 2026 cluster, with printed y-positions

| idx | page | y | date | amount | mk | description |
|---|---|---|---|---|---|---|
| 28 | 1 | 806.91 | 27/04/2026 | 90.00 | D | `UPI-REDEFINED PRIVATE L` |
| **29** | **1** | **818.71** | 28/04/2026 | **40.00** | D | `UPI-SHASHANK VISHNUPANTNSE` |
| **30** | **1** | **830.52** | 28/04/2026 | **20.00** | D | `UPI-REDEFINED PRIVATE L` |
| **31** | **2** | **130.09** | 28/04/2026 | **20.00** | D | `UPI-REDEFINED PRIVATE L` |
| 32 | 2 | 141.89 | 28/04/2026 | 1750.00 | D | `UPI-SHAUKEEN ENTERPRISE` |
| 33 | 2 | 153.70 | 29/04/2026 | 20.00 | D | `UPI-SHASHANK VISHNUPANTNSE` |

**The geometry adds a fact the brief did not have: the exact-duplicate pair straddles the
page break.** idx30 is the **last** transaction row on page 1 (y=830.52, page bottom) and
idx31 is the **first** on page 2 (y=130.09, page top), byte-identical to it.
`UPI-REDEFINED PRIVATE L` at exactly 20.00 recurs dozens of times in this one statement.

---

## 3. The failure is not a pure drop — this corrects the brief

The brief recorded "a PURE DROP, not a substitution or a merge", derived from diffing
against arm C. Diffing against **the PDF** shows three distinct failure modes, all confined
to idx29–31:

| mode | n | effect |
|---|---|---|
| drop one identical 20.00 | → 70 | pure drop |
| drop the 40.00 **and** one 20.00 | → 69 | double drop |
| **emit `28/04/2026 20.00 UPI-SHASHANK VISHNUPANTNSE`** — description of idx29 bound to the amount of idx30/31 — and drop the real 40.00 | **→ 71** | **fabricated row, correct count** |

That third mode is why **row count hides the defect**: 4 of the 8 pre-fix samples that
"got 71" were carrying a row the PDF never prints. The mechanism is **row-boundary
misalignment in a dense same-date cluster at a page break**, not de-duplication alone —
which is why the shipped rule also pins each amount to its own printed line.

---

## 4. Isolation experiment — both competing hypotheses ruled out

`ablate_rowcount.py`, 24 calls on this one statement. Each variant differs from the current
prompt by exactly one excision or addition; each excision is asserted present, so a stale
fragment fails loudly rather than silently ablating nothing.

| variant | Δ prompt | n per repeat | row-exact |
|---|---|---|---|
| **base** (current prompt) | — | 71,70,70,71,71,71,69,71,71,70,71,71 | **4/12** |
| `nodate` — minus statement-period + date sanity-check rules | −731 ch | 71,70,71 | no better |
| `noband` — minus leading-band + `TRANSACTIONS FOR` rules | −536 ch | 70,71,71 | no better |
| `norewards` — minus the whole REWARDS_RULES block | **−7433 ch (−36%)** | 70,71,70 | no better |
| **`fix`** — base + the new rule | +770 ch | **71 ×12** | **11/12** |

- **(b) a specific rule suppressing the row — NOT SUPPORTED.** No single excision restores
  it. Consistent with arm C carrying its *own*, stricter date-bound rule ("must not exceed
  the Statement Date, nor fall more than two months prior") while still emitting all 71.
- **(a) prompt length / attention dilution — NOT SUPPORTED.** Deleting **36%** of the
  prompt — a block that cannot legitimately affect whether a transaction row is emitted —
  left the defect exactly where it was. **No prompt shortening was performed**, so no
  measured rewards win was put at risk and there is no length/accuracy tradeoff to decide.
- **(c) output truncation — RULED OUT WITH DATA.** Every sample: `finish_reason == "stop"`
  and `prompt_tokens + completion_tokens == total_tokens`. Completion was 3.7k–4.9k tokens
  against a 96000 cap.

The defect is **stochastic**, not deterministic: the unmodified prompt is row-exact ~33% of
the time. Any single-sample conclusion here would have been noise — the brief's insistence
on repeats was load-bearing.

---

## 5. The change

One bullet added to `TRANSACTION RULES`, immediately after the existing
`COMPLETENESS IS MANDATORY` bullet. Nothing removed; no schema change (26 leaves,
`assert_schema.py` passes); no HDFC/ICICI wording, glyph rule or mask rule imported; no
`TRANSFER TO → direction` rule reintroduced.

> - ONE OUTPUT ROW PER PRINTED ROW, INCLUDING REPEATS. These statements print long runs
>   of small UPI payments in which the same payee recurs for the same amount on the same
>   date many times over. Two or more CONSECUTIVE rows that are identical in date, amount
>   and description are SEPARATE genuine payments, not one row printed twice: emit every
>   one of them and never merge or de-duplicate them. This holds when the repeat spans a
>   PAGE BREAK, i.e. the last row of one page and the first row of the next are identical
>   — that is two printed rows and both MUST be emitted. Keep each amount bound to the
>   description printed on ITS OWN line; never carry an amount up or down from a
>   neighbouring row. Count the printed transaction rows and emit exactly that many.

Shipped `SBI_PROMPT.txt` sha256 `f7ff966bd7f2…` is **byte-identical** to the measured `fix`
variant — what was measured is what shipped.

---

## 6. Defect found in my own measurement code — disclosed

**The first version of `pdf_rowtruth.py` required the leftmost span of a row to be a
date. It was wrong, and it accused the model.**

SBI prints tax-continuation rows with **no date of their own** — `IGST DB @ 18.00%
190.74  D` — inheriting the date above. Requiring a date silently discarded every one of
them, so the probe reported **7 where the PDF prints 8** (905768587) and **1 where it
prints 2** (221159806) — i.e. it claimed the model had *invented* two rows that are in fact
printed. All three arms agreed on 8 and 2; the probe was the outlier, which is exactly the
signal the brief said to distrust.

Fixed: the date is optional and inherited from the preceding dated row. Wrapped-narration
continuation lines remain excluded because they carry neither amount nor marker. **After
the fix the probe agrees with all arms on 11/12 statements** — the agreement that makes its
verdict of 71 on the twelfth trustworthy. Had I not chased this, I would have reported two
phantom model fabrications and possibly a phantom regression.

---

## 7. Repeat table (statement 1707857175, PDF prints 71)

| arm | prompt | n per repeat | count==71 | **row-exact** |
|---|---|---|---|---|
| pre-fix (`base`) | `SBI_PROMPT.txt` @ `756213dac6e4` | 71,70,70,71,71,71,69,71,71,70,71,71 | 8/12 | **4/12** |
| **post-fix (`fix`)** | `SBI_PROMPT.txt` @ `f7ff966bd7f2` | 71,71,71,71,71,71,71,71,71,71,71,71 | **12/12** | **11/12** |
| earlier pre-fix samples | armD, armA rep1/2/3 | 70, 70, 69, 70 | 0/4 | 0/4 |
| arm B (previous refined) | `SBI_PROMPT_PREV.txt` | 70 | 0/1 | 0/1 |
| arm C (client generic) | `GEMINI_GENERIC_PROMPT.txt` | 71 | 1/1 | 1/1 |

`fix` rep11 still produced the hybrid row. **The fix reduces the defect rate; it does not
prove elimination.**

---

## 8. Regression gate — arm E vs arm D

Arm D is preserved on disk untouched. **Status: 7 of 12 statements scored.** The OAuth
refresh token for profile `fevm-stable` expired mid-run; 5 statements and 3 repeats
returned `NETWORK_ERROR` / failed to mint a token. These are recorded as
**INFRASTRUCTURE, never as model failures**, are non-terminal, and will be retried by
`python3 run_armE.py --reps 3` once `databricks auth login --profile fevm-stable` is run.

Not yet scored: **515948911, 1118980175, 221159806, 393366914, 905768587** + 3 long-statement repeats.

### Scored so far (7/12) — no movement on any gated item

| sid | PDF rows | D n | E n | D exact | E exact | note |
|---|---|---|---|---|---|---|
| 1707857175 | 71 | 70 | **71** | ✗ | **✓** | target statement |
| 369606524 | 16 | 16 | 16 | ✗ | ✗ | pre-existing description issue, see below |
| 1511624796 | 15 | 15 | 15 | ✓ | ✓ | |
| 1036185244 | 7 | 7 | 7 | ✓ | ✓ | |
| 1152718739 | 4 | 4 | 4 | ✓ | ✓ | |
| 1390952698 | 4 | 4 | 4 | ✓ | ✓ | |
| 1120623464 | 1 | 1 | 1 | ✓ | ✓ | |

| gated item | armD | armE | verdict |
|---|---|---|---|
| `closingPoints` null on all 7 scored | null ×7 | null ×7 | held (221159806=18068 **unscored**) |
| **DUPLICATION `closingPoints == pointsEarnedThisCycle`** | **0/12** | **0/12** | **held — the critical invariant** |
| `network` | null ×7 | null ×7 | held |
| `pointsExpiringNext30/60Days` | null ×7 | null ×7 | held |
| `pointsEarnedThisCycle` | 106, 0, 12, 0, 476, 1072, 375.25 | identical | held |
| `txnType` off-vocabulary | 0 | 0 | held |
| `txnType` REFUND anchor | 13 rows | 13 rows | held — anchor still fires |
| row counts, other statements | — | identical | held |
| `finish_reason` / token accounting | stop, balanced | stop, balanced | held |

`assert_schema.py`: **PASS** — 26 leaves, enums null-coherent under strict mode.

### Out-of-scope observation on 369606524 (not a regression)

Two description-fidelity mismatches, **present identically in arms C and D**, so
pre-existing and unrelated to this change. Verified against raw spans:

- PDF prints `Cashfree*FLIPKART INTE Bengaluru IND`; **all** arms emit
  `CASHFREE*FLIPKART IN…`, truncating `INTE`→`IN`. A real model defect, arm-independent.
- PDF prints `FLIPKART BENGALURU KAR (Pay in EMIs)` with **no** `IND`; arms C and D append
  `IND` (a fabrication). **Arm E does not** — it matches the PDF.

Arm E is therefore one row *better* here, but n=1 and this was not the target; I am not
claiming a description fix.

---

## 9. Honesty bounds

- Measured on **12 statements only** (7 fully, for the gate). **Not extrapolated** to the
  ~300-statement SBI corpus. The hypothesis that this shape of statement is common there is
  **PREDICTED / UNVERIFIED**.
- `fix` is row-exact **11/12, not 12/12**. This reduces the defect rate; it does not
  eliminate it.
- The regression gate is **incomplete (7/12)** pending re-authentication. The five unscored
  statements include **221159806**, which carries the `closingPoints == 18068` assertion —
  the single most important gated value — so the gate is **not yet green** and this work
  should not be treated as fully verified until `run_armE.py` completes.
- Every failed call was infrastructure (`NETWORK_ERROR`, expired OAuth). Zero 429s, zero
  IP-ACL rejections. No infrastructure failure was scored as a model result.

## Files

| file | role |
|---|---|
| `pdf_rowtruth.py` / `pdf_rowtruth.json` | PDF-derived true row counts (probe bug in §6 disclosed) |
| `ablate_rowcount.py` / `ablate_rowcount.json` | isolation experiment, 24 calls |
| `run_armE.py` | arm E driver, reuses `runner.py` unchanged |
| `gate_armE.py` | regression gate; refuses to score records with no parsed JSON |
| `json_armE/`, `json_armE_rep*/` | arm E outputs (arm D preserved) |
| `json_armE_{base,nodate,noband,norewards,fix}_r*/` | per-variant ablation outputs |
