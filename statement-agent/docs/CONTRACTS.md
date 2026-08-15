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

- WS1 owns shared `contracts/`, `config.py`, `harness/transports.py`,
  `harness/policy.py`, and `harness/auth.py`. Downstream workstreams must not
  edit these files; `harness/transports.py` is on the stdlib test path.
- WS2 owns new graph modules, `skills/extract_statement.py`,
  `harness/extraction_adapter.py`, and `harness/cli.py`.
- WS3 owns `db/`, `skills/persist_result.py`, and
  `skills/record_feedback.py`. It must put a commented app resource snippet in
  its own new `docs/resources-ws3.md` for WS6 to consolidate.
- WS4 owns new `harness/tracing*.py` modules. It must put a commented app
  resource snippet in its own new `docs/resources-ws4.md` for WS6 to consolidate.
- WS5 owns `judge/`, `skills/judge_statement.py`, and
  `harness/judge_adapter.py`.
- WS6 solely owns `app.yaml`, `app/`, and new static frontend assets; it
  consolidates the WS3/WS4 resource snippets.

If WS3 or WS4 needs additional configuration, add a `CONFIGURE(<slug>)`-tagged
setting in a workstream-owned module (`db/config_ws3.py` or
`harness/config_ws4.py`). Keep environment names workstream-prefixed. Do not
race by appending to shared `config.py`; WS6 can consolidate after integration.

Downstream workstreams must not edit `contracts/`, `schema/`, `prompts/`,
`rules/`, `memory/`, this ownership document, or another workstream's files.
Coordinate any necessary contract change through WS1 rather than editing a seam.

## Judge discipline

`FieldComparison` accepts exactly seven paths. The three transaction paths are
per matched row. Matching is description-similarity-only, strict 1:1, and
order-insensitive; use `MatchMethod.DESCRIPTION_SIMILARITY_1TO1` for rows and
`MatchMethod.DIRECT` for scalars. `expected` is PDF ground truth read by Opus-5;
`actual` is the extraction value under test. `JudgeVerdict.match_method`
summarizes the transaction-row strategy, while each comparison records the
scope-valid method used for that field. Use threshold 0.55 for HDFC
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
