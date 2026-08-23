# Opus-5 statement judge

Opus-5 reads the native PDF independently and returns ground truth for 28 judged
fields: per-card identity and credit-limit fields, statement metadata, the statement
summary box, the rewards summary, and five per-transaction paths (date, description,
amount, direction, reward points). The candidate extraction is not shown to Opus;
comparison and aggregation happen locally.

Transactions are paired solely on normalized description similarity using strict 1:1,
order-insensitive assignment. HDFC uses 0.55; ICICI uses 0.60. SBI and Axis default to
0.60. Date and amount never participate in pairing because doing so would make their
reported correctness circular. Equal description scores may use relative row position
only as a deterministic tie-break.

Dates, numbers, descriptions, and last-four values are normalized before correctness
is decided. Equal canonical values with different serialization are `FORMAT_ONLY` and
are not charged. Null PDF ground truth is `ABSENT_IN_PDF`; missing or extra transaction
rows are `UNMATCHED_ROW`. A refusal, truncated completion, or invalid JSON produces an
explicit `JUDGE_ERROR` summary whose 28 sentinel comparisons are all unscored
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

## Where judge results show up in MLflow

Judge results land in the experiment two complementary ways:

1. **Per-trace metrics** (`judge/scorer.py::score_trace`). Each judged parse
   run gets `judge.accuracy`, `judge.accuracy_forgiven`, and the per-field
   accuracies logged back to that SAME run via
   `MlflowClient.log_metric(run_id, ...)`, plus a `judged=true` tag. Good for
   drilling into one parse, but scattered across individual runs — there is no
   single place to see all judge results together.

2. **Aggregated Evaluation Run** (`judge/evaluator.py::run_mlflow_evaluation`).
   After scoring the sampled traces, `run_judge_evaluation` additionally calls
   `mlflow.models.evaluate` (the non-deprecated successor to `mlflow.evaluate`
   as of MLflow 3.0) to create ONE evaluation run named `judge-evaluation`,
   tagged `eval_run=true`. It carries a per-row `eval_results_table` artifact
   (one row per judged trace: `run_id`, `bank`, strict/forgiven accuracy, the
   28 per-field accuracies) plus aggregate metrics from two **custom
   scorers** (`mlflow.models.make_metric`): `judge.mean_strict_accuracy` and
   `judge.mean_narration_forgiven`. This run renders in the experiment's
   **Evaluations** tab — the aggregated cross-trace view the per-run metrics
   cannot provide.

A custom scorer here is just a Python function passed to `make_metric`: it
receives the input columns as pandas Series (e.g. `predictions` →
`strict_accuracy`, `narration_forgiven_accuracy` → that column) and returns a
`MetricValue` with per-row `scores` and an `aggregate_results` dict. MLflow
writes the per-row scores into the eval table and the aggregate into the run's
metrics. The evaluation run is best-effort: if mlflow is unavailable it is
skipped and the per-trace metrics + JSON summary still return.

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
