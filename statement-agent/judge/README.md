# Opus-5 statement judge

Opus-5 reads the native PDF independently and returns ground truth for exactly four
scalar paths (two per-card identity fields and two statement reward fields) and three
per-transaction paths (date, description, amount). The candidate extraction is not
shown to Opus; comparison and aggregation happen locally.

Transactions are paired solely on normalized description similarity using strict 1:1,
order-insensitive assignment. HDFC uses 0.55; ICICI uses 0.60. SBI and Axis default to
0.60. Date and amount never participate in pairing because doing so would make their
reported correctness circular. Equal description scores may use relative row position
only as a deterministic tie-break.

Dates, numbers, descriptions, and last-four values are normalized before correctness
is decided. Equal canonical values with different serialization are `FORMAT_ONLY` and
are not charged. Null PDF ground truth is `ABSENT_IN_PDF`; missing or extra transaction
rows are `UNMATCHED_ROW`. A refusal, truncated completion, or invalid JSON produces an
explicit `JUDGE_ERROR` summary whose seven sentinel comparisons are all unscored
`ABSENT_IN_PDF`; judge failure is therefore never reported as extraction inaccuracy.
The prompt explicitly handles image-only card art, HDFC's
ITFRupee `C`, ICICI's backtick rupee glyph, truncated narrations, and static reward
boilerplate.

The judge deliberately does not emit a fabrication verdict. When Opus cannot support
a field from the complete PDF (including images), it returns null and comparison marks
the field `ABSENT_IN_PDF`, regardless of the candidate value. This structural rule
avoids the prior false-accusation failure mode more safely than text-layer substring
search. `evidence.py` remains a conservative optional diagnostic utility, not part of
scoring; it uses whitespace-flexible, word/digit-bounded patterns that accept Indian
grouping and unpadded dates.

Aggregation reports per-field accuracy and two overall cell-level readings. `strict`
charges description fidelity differences. `narration_forgiven` treats description
cells as correct so narration truncation or layout artifacts do not obscure financial
date/amount quality. Both exclude `ABSENT_IN_PDF` from the denominator; `UNMATCHED_ROW`
is charged.

## Differences from the legacy scorers

Card display names uniformly use HDFC's `norm_key`—which removes every
non-alphanumeric character—followed by containment across all banks. ICICI and SBI's
legacy scorers instead use punctuation-preserving `text` with `lenient_hit`. The
judge is therefore marginally more lenient on punctuation-only ICICI/SBI card-name
differences: this deviation can only over-report agreement, never under-report it.
Real card display names in the evaluated Indian corpus contain no punctuation, so the
divergent branch does not fire there. We deliberately accept this immaterial deviation
instead of adding bank-specific comparison paths.

The transaction matcher is also intentionally more non-circular than the ICICI/SBI
legacy scorers. For equal description-similarity scores, this judge applies HDFC's
relative-position tie-break instead of the ICICI/SBI date tie-break; date never enters
pairing. These two documented deviations mean small card-name or transaction-pairing
deltas from published ICICI/SBI baselines are possible and are not by themselves
regressions. Numeric values retain the legacy absolute/relative tolerance.
