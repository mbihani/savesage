# Parallel implementation contracts

All shared contracts are stdlib-only in `contracts/`. Do not add Pydantic,
LangGraph, MLflow, FastAPI, psycopg, or Databricks SDK imports there. Adapter
modules imported by tests must also remain stdlib-only; third-party imports in
`app/main.py`, auth, DB, graph, and telemetry code must be function-local unless
their module is never on the contract-test import path.

## ABC seams and owners

| ABC | Owner | Implementation home |
|---|---|---|
| `ExtractionAdapter` | Workstream 2 | `harness/extraction_adapter.py` and graph modules |
| `JudgeAdapter` | Workstream 5 | `harness/judge_adapter.py`, `judge/` |
| `ResultStore` | Workstream 3 | `db/` |
| `FeedbackStore` | Workstream 3 | `db/` |
| `TraceSink` | Workstream 4 | new telemetry modules under `harness/` |
| `MemoryStore` | Workstream 2 | `memory/` and graph wiring |

## File-level ownership

- WS2 owns new graph modules, `skills/extract_statement.py`,
  `harness/extraction_adapter.py`, and `harness/cli.py`.
- WS3 owns `db/`, `skills/persist_result.py`, and
  `skills/record_feedback.py`; it may append its resource block to `app.yaml`.
- WS4 owns new `harness/tracing*.py` modules; it may append its MLflow resource
  block to `app.yaml`.
- WS5 owns `judge/`, `skills/judge_statement.py`, and
  `harness/judge_adapter.py`.
- WS6 owns `app/` and new static frontend assets.

Downstream workstreams must not edit `contracts/`, `schema/`, `prompts/`,
`rules/`, `memory/`, this ownership document, or another workstream's files.
Coordinate any necessary contract change through WS1 rather than editing a seam.

## Judge discipline

`FieldComparison` accepts exactly seven paths. The three transaction paths are
per matched row. Matching is description-similarity-only, strict 1:1, and
order-insensitive; record that in `match_method`. Use threshold 0.55 for HDFC
and 0.60 for ICICI. Preserve these exact existing signatures/semantics:

- `hdfc/score_lib.py`: `norm_date(v)`, `norm_num(v)`, `norm_desc(v)`; date becomes
  DD/MM/YYYY, number becomes float after currency/comma/CR/DR handling, and
  description is case-folded with whitespace collapsed while punctuation remains.
- `icici/score_lib.py`: its local date wrapper is `date_norm(x)` and last-four
  helper is `norm4(v)`; canonical text/number normalizers are imported as
  `text`/`num`, not defined locally. Reuse the repository scorer behavior rather
  than inventing same-named functions.
- `sbi/score_lib_sbi.py`: its local date wrapper is `date_norm(v)`; canonical
  text/number normalizers are imported as `text`/`num`.

This names the actual signatures in commit `26f27bc`; the ICICI/SBI files do not
literally define `norm_date`, `norm_num`, or `norm_desc` despite older summaries.
