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
rows are `UNMATCHED_ROW`. The prompt explicitly handles image-only card art, HDFC's
ITFRupee `C`, ICICI's backtick rupee glyph, truncated narrations, and static reward
boilerplate. Conservative text-support helpers use whitespace-flexible, word/digit-
bounded patterns and accept Indian grouping and unpadded dates.

Aggregation reports per-field accuracy and two overall cell-level readings. `strict`
charges description fidelity differences. `narration_forgiven` treats description
cells as correct so narration truncation or layout artifacts do not obscure financial
date/amount quality. Both exclude `ABSENT_IN_PDF` from the denominator; `UNMATCHED_ROW`
is charged.
