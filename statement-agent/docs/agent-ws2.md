# Workstream 2 — LangGraph parse agent design notes

## Graph shape

Linear pipeline: `route -> extract -> validate -> persist -> judge -> finalize`.

Every node takes and returns the same mutable `GraphState` instance. There are
no conditional edges: a node that hits a terminal failure sets
`state.outcome` and downstream nodes no-op on a terminal state. This keeps the
graph inspectable (six unconditional edges) and the in-memory test fakes
simple.

## Ports consumed (DI only — no concrete implementations imported)

`graph/nodes.py` depends ONLY on the ABCs in `contracts/ports.py`:

| Port             | Required | When absent                         |
|------------------|----------|-------------------------------------|
| `ExtractionAdapter` | yes   | graph cannot run                    |
| `ResultStore`    | no       | persistence skipped                 |
| `TraceSink`      | no       | no trace events recorded            |
| `JudgeAdapter`   | no       | judge stage skipped (not a failure) |
| `FeedbackStore`  | no       | not used by core path (WS3 wiring)  |

No `psycopg`, `mlgraph`, `langchain`, or `OpusJudgeAdapter` import appears
anywhere in `graph/`, `harness/extraction_adapter.py`, `skills/extract_statement.py`,
or `harness/cli.py`. WS3/WS4/WS5 hand in their concrete ports at integration.

## Validation short-circuit decision

**A validation failure does NOT short-circuit the judge.** Rationale:

The judge compares extraction fields against PDF ground truth *independently* of
schema/rule conformance. A partial-but-schema-invalid extraction is exactly the
output that benefits most from judging — you want to know whether the model
read the PDF correctly even when it shaped the answer wrong. Only a hard
`EXTRACTION_FAILED` outcome skips the judge (there is nothing to judge).

### Judge structural-shape gate (NB2)

A *schema-invalid-but-structurally-usable* payload (cards and transactions are
lists, payload is a dict) IS still judged. But a *structurally unusable* payload
(cards/transactions are not lists, or payload is not a dict) is NOT sent to the
judge: a real judge (WS5, PR #13) may reject such input, turning an intended
PARTIAL into JUDGE_FAILED. The `judge_node` checks
`_meets_judge_minimum_shape(extraction)` before calling the judge and records a
clear `judge_skipped_reason` when the payload does not meet it. The point is to
distinguish "invalid but judgeable" from "structurally unusable."

### Persistence-failure outcome (BLOCKING 4)

A run that failed to persist (e.g. `ResultStore.save_extraction` raised) must
never report SUCCESS. `finalize_node` treats any real stage error (in
`state.errors`, which excludes trace failures routed to `state.trace_errors`)
as at least PARTIAL. A user must never be told their statement was saved when
it was not.

### schema_valid propagation (BLOCKING 2)

The `validate_node` propagates the validated `schema_valid` into the frozen
`ExtractionResult` via `dataclasses.replace` *before* persistence, so the object
handed to `ResultStore.save_extraction` carries the validated value — not the
adapter's initial `False`.

Outcomes:
- `SUCCESS` — clean run, schema + rules both pass, no stage errors.
- `PARTIAL` — extraction succeeded and was persisted + judged, but validation
  flagged schema/rule violations OR a real stage (e.g. persistence) failed without
  short-circuiting (the UI shows a partial parse; never SUCCESS when unsaved).
- `EXTRACTION_FAILED` — the extract node failed terminally; persist + judge
  are skipped.
- `JUDGE_FAILED` — extraction + validation + persist succeeded, but the judge
  raised; the extraction is still available.

### Non-finite numbers (BLOCKING 1)

`json.loads` accepts `NaN`/`Infinity` literals, and `float('nan')` is a `float`.
The schema conformance checker requires `math.isfinite(value)` for the `"number"`
type, and the GT rules' `_is_number` helper requires it too. A `NaN`
`totalAmountDue` is reported `schema_valid=False`, not silently accepted.

### Truncation and refusal (BLOCKING 3)

`map_response` checks the first choice's `finish_reason` and any `message.refusal`
before parsing. `finish_reason` not in `(None, "stop")` (e.g. `"length"` for
truncation, `"content_filter"` for refusal) raises `ExtractionError`. A
parseable-but-clipped JSON is treated as an extraction failure, not a success
that silently drops transactions.

### Single timeout source (BLOCKING 5)

The request timeout comes from `RetryPolicy.timeout_seconds` only — not from
`settings.request_timeout_seconds`. `RetryPolicy` is the single source of both
retry behaviour and the timeout.

## langgraph import discipline

`langgraph` is imported function-locally inside `graph.graph.build_graph`. The
whole `graph/` package imports cleanly with stdlib only, so the pre-existing
stdlib gate runs untouched. The graph-level e2e test is `skipUnless(langgraph)`;
the node/routing/validation/payload/result-mapping logic has real non-skipped
stdlib tests.

## Live verification

A single live Luna call was made against
`databricks-gpt-5-6-luna` on the `fevm-stable` workspace using a synthetic
empty-content PDF stub (no PII). Result: HTTP 200, `schema_valid=True`, 0 rule
errors, 0 transactions (correct for a contentless PDF). This proves the full
transport path: `transports.extraction_payload` -> `auth.acquire_token` ->
stdlib `urllib` -> Luna -> `map_response` -> `validate_payload`.
